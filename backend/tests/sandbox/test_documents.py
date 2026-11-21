"""A document counts for what it says. Its file name never does."""

import io
import json
from types import SimpleNamespace

import openpyxl
import pytest
from app.db.models import MessageRow
from app.platform.mailbox import MailboxError
from app.sandbox import runner
from app.sandbox.scenarios import DGD_TEXT, SDS_TEXT
from app.sandbox.world import pdf_bytes
from app.workflows import documents
from app.workflows.extract import assess_dg

REQUEST = "Please quote 300 kg lithium-ion batteries (UN3480) from FRA to ORD."
# Deliberately unhelpful names: only the content can make these count.
DGD = documents.evidence("scan-001.pdf", pdf_bytes(DGD_TEXT))
SDS = documents.evidence("scan-002.pdf", pdf_bytes(SDS_TEXT))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_a_relevant_readable_document_counts_whatever_it_is_called():
    assert DGD["kinds"] == ["dgd"] and DGD["un_numbers"] == ["UN3480"]
    assert SDS["kinds"] == ["msds"]
    found = assess_dg([(REQUEST, [DGD, SDS])])
    assert found["status"] == "documented" and not found["blocked"]
    assert found["docs_present"] == ["dgd", "msds"] and len(found["documents"]) == 2


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "empty"),
        (b"%PDF-1.4 this is not really a pdf", ""),
        (b"\x00\x01\x02\x03 binary", "binary"),
        (pdf_bytes(""), "no readable text"),
    ],
)
def test_an_empty_or_corrupt_file_named_like_the_right_paper_proves_nothing(content, reason):
    broken = documents.evidence("MSDS.pdf", content)
    assert not broken["readable"] and not broken["kinds"] and reason in broken["reason"]
    found = assess_dg([(REQUEST, [DGD, broken])])
    assert found["status"] == "docs_missing" and found["docs_missing"] == ["msds"]
    assert found["unreadable_attachments"] == ["MSDS.pdf"]


def test_a_renamed_unrelated_document_is_refused():
    invoice = documents.evidence("MSDS.pdf", pdf_bytes("INVOICE 2026-114\nOffice chairs"))
    assert invoice["readable"] and invoice["kinds"] == []
    found = assess_dg([(REQUEST, [DGD, invoice])])
    assert found["status"] == "docs_missing" and found["docs_missing"] == ["msds"]
    assert found["rejected_documents"][0]["problem"] == "unrelated"


def test_a_document_for_different_goods_is_refused():
    paint = pdf_bytes("SHIPPER'S DECLARATION FOR DANGEROUS GOODS\nUN1263 Paint, Class 3")
    found = assess_dg([(REQUEST, [documents.evidence("DGD.pdf", paint), SDS])])
    assert found["status"] == "docs_missing" and found["docs_missing"] == ["dgd"]
    rejected = found["rejected_documents"][0]
    assert rejected["problem"] == "other_goods" and "UN1263" in rejected["reason"]


def test_a_declaration_without_a_un_number_is_refused():
    blank = pdf_bytes("SHIPPER'S DECLARATION FOR DANGEROUS GOODS\nBatteries")
    found = assess_dg([(REQUEST, [documents.evidence("DGD.pdf", blank), SDS])])
    assert found["docs_missing"] == ["dgd"]
    assert found["rejected_documents"][0]["problem"] == "incomplete"


def test_a_document_that_contradicts_the_email_blocks_the_quote():
    harmless = pdf_bytes("SAFETY DATA SHEET\nSection 14: Not regulated for transport.")
    found = assess_dg([(REQUEST, [DGD, documents.evidence("sds.pdf", harmless)])])
    assert found["status"] == "conflict" and found["blocked"]
    assert found["rejected_documents"][0]["problem"] == "contradicts_email"


def test_an_email_that_contradicts_the_documents_is_not_quoted_as_normal_cargo():
    found = assess_dg([("Please quote 300 kg non-dangerous toys, FRA to ORD.", [DGD, SDS])])
    assert found["signals"] == ["UN3480"]  # the declaration says so, the mail did not
    assert found["status"] == "conflict" and found["blocked"]
    # Saying "not dangerous" again does not clear it while a declaration is on file.
    again = assess_dg([("non-dangerous toys", [DGD, SDS]), ("They are not dangerous goods.", [])])
    assert again["status"] == "conflict"


def test_a_scan_is_read_when_ocr_exists_and_unreadable_when_it_does_not(monkeypatch):
    monkeypatch.setattr(documents.shutil, "which", lambda _: None)
    assert not documents.evidence("DGD.png", PNG)["readable"]
    monkeypatch.setattr(documents, "_ocr", lambda content: DGD_TEXT)
    assert documents.evidence("IMG_0042.png", PNG)["kinds"] == ["dgd"]


def test_a_spreadsheet_is_read_by_content():
    book, out = openpyxl.Workbook(), io.BytesIO()
    for line in SDS_TEXT.splitlines():
        book.active.append([line])
    book.save(out)
    sheet = documents.evidence("data.xlsx", out.getvalue())
    assert sheet["kinds"] == ["msds"] and sheet["un_numbers"] == ["UN3480"]


def test_required_documents_come_from_configuration(monkeypatch, tmp_path):
    shipped = documents.requirements()
    assert shipped["required_documents"] == ["dgd", "msds"]
    assert shipped["confirmed"] is False and "organisers" in shipped["source"]
    assert assess_dg([(REQUEST, [])])["requirements"]["confirmed"] is False

    rules = {"required_documents": ["dgd"], "by_un_number": {"UN3481": ["msds"]}}
    monkeypatch.setattr(documents, "requirements", lambda: rules)
    assert assess_dg([(REQUEST, [DGD])])["status"] == "documented"
    packed = assess_dg([("batteries packed with equipment, UN3481", [])])
    assert packed["docs_missing"] == ["msds"]

    bad = tmp_path / "rules.json"
    bad.write_text(json.dumps({"required_documents": ["passport"]}))
    monkeypatch.undo()
    monkeypatch.setattr(
        documents, "get_settings", lambda: SimpleNamespace(dg_requirements_file=str(bad))
    )
    documents.requirements.cache_clear()
    with pytest.raises(ValueError, match="unknown documents"):
        documents.requirements()
    monkeypatch.undo()
    documents.requirements.cache_clear()


def test_each_attachment_is_downloaded_once_and_a_busy_mailbox_is_retried(monkeypatch):
    calls = []

    class Mailbox:
        def download_attachment(self, message_id, attachment_id):
            calls.append(attachment_id)
            if attachment_id == 2:
                raise MailboxError("http_error", "HTTP 404")
            if attachment_id == 3:
                raise MailboxError("network", "timed out")
            return pdf_bytes(DGD_TEXT)

    monkeypatch.setattr(documents, "get_adapters", lambda: {"mailbox": Mailbox()})
    db = SimpleNamespace(flush=lambda: None)
    files = [{"id": 1, "filename": "a.pdf"}, {"id": 2, "filename": "b.pdf"}]
    message = MessageRow(external_id="7", raw={"mail": {"attachments": files}})
    first = documents.collect(db, message)
    assert first[0]["kinds"] == ["dgd"] and not first[1]["readable"]
    assert "HTTP 404" in first[1]["reason"]
    assert documents.collect(db, message) == first and calls == [1, 2]

    busy = MessageRow(external_id="8", raw={"mail": {"attachments": [{"id": 3, "filename": "c"}]}})
    with pytest.raises(MailboxError):
        documents.collect(db, busy)
    assert "documents" not in busy.raw  # nothing is recorded about a file that was never read


def test_wrongly_named_files_are_refused_in_the_thread_and_the_customer_is_told_why(db):
    result = runner.run("dg_wrong_documents", 42)
    assert all(c["passed"] for c in result["checks"])
    sent = [m for m in result["threads"]["rfq1"] if m["direction"] == "out"]
    assert [m["purpose"] for m in sent[:3]] == ["dg_documents", "dg_documents", "first_quote"]
    second = sent[1]["body"]
    assert "MSDS.pdf" in second and "DGD.pdf" in second and "UN1263" in second
    assert "EUR" not in sent[0]["body"] + second
