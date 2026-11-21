"""Read what an attachment says. A file name proves nothing.

`evidence` turns the bytes of one attachment into facts: is it readable, which document is it,
which UN numbers does it name. `collect` downloads a mail's attachments once and keeps those
facts on the message, so a later look at the same thread costs no further mailbox call.
"""

import hashlib
import io
import json
import re
import shutil
import subprocess
import zipfile
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.platform import get_adapters
from app.platform.mailbox import MailboxError

KINDS = ("dgd", "msds")
_UN = re.compile(r"\bUN\s?(\d{4})\b", re.IGNORECASE)
_KIND = {
    "dgd": re.compile(
        # PDF text often carries a typographic apostrophe.
        r"shipper['’]?s?\s+declaration\s+for\s+dangerous\s+goods|dangerous\s+goods\s+declaration",
        re.IGNORECASE,
    ),
    "msds": re.compile(r"safety\s+data\s+sheet", re.IGNORECASE),
}
_NOT_REGULATED = re.compile(
    r"\bnot\s+(?:regulated|restricted|classified\s+as\s+dangerous\s+goods)\b"
    r"|\bnon[- ]dangerous\s+goods\b"
    r"|\bnot\s+subject\s+to\s+(?:the\s+)?(?:iata|adr|dangerous\s+goods)",
    re.IGNORECASE,
)
_IMAGE = (b"\x89PNG", b"\xff\xd8", b"II*\x00", b"MM\x00*")


def un_numbers(text: str) -> list[str]:
    return sorted({f"UN{digits}" for digits in _UN.findall(text)})


def _ocr(content: bytes) -> str:
    if shutil.which("tesseract") is None:
        raise ValueError("this machine has no OCR, so a scan cannot be read")
    done = subprocess.run(["tesseract", "stdin", "stdout"], input=content, capture_output=True)
    return done.stdout.decode("utf-8", "replace")


def read_text(content: bytes) -> str:
    """Text of a PDF, spreadsheet, scan or text file. The format comes from the bytes."""
    if content.startswith(b"%PDF"):
        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if content.startswith(b"PK"):
        if "xl/workbook.xml" not in zipfile.ZipFile(io.BytesIO(content)).namelist():
            raise ValueError("an archive that is not a spreadsheet")
        import openpyxl

        book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        return "\n".join(
            " ".join(str(cell) for cell in row if cell is not None)
            for sheet in book
            for row in sheet.iter_rows(values_only=True)
        )
    if content.startswith(_IMAGE):
        return _ocr(content)
    if b"\x00" in content:
        raise ValueError("an unknown binary format")
    return content.decode("utf-8")


def evidence(filename: str, content: bytes) -> dict:
    """What one attachment proves, from its content alone."""
    found = {
        "filename": filename,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "readable": False,
        "kinds": [],
        "un_numbers": [],
        "says_not_regulated": False,
    }
    if not content:
        return {**found, "reason": "the file is empty"}
    try:
        text = read_text(content)
    except Exception as exc:  # noqa: BLE001 - a file no reader accepts is simply unreadable
        return {**found, "reason": f"{type(exc).__name__}: {exc}"[:160]}
    if not text.strip():
        return {**found, "reason": "the file has no readable text"}
    return {
        **found,
        "readable": True,
        "kinds": [kind for kind in KINDS if _KIND[kind].search(text)],
        "un_numbers": un_numbers(text),
        "says_not_regulated": bool(_NOT_REGULATED.search(text)),
        "text": text[:4000],  # a request may state its details only in the attached file
    }


def collect(db, message) -> list[dict]:
    """Evidence for every attachment of one mail. Each file is downloaded once."""
    raw = dict(message.raw) if isinstance(message.raw, dict) else {}
    attachments = list((raw.get("mail") or {}).get("attachments") or [])
    known = dict(raw.get("documents") or {})
    download = getattr(get_adapters().get("mailbox"), "download_attachment", None)
    for attachment in attachments:
        key = str(attachment.get("id"))
        if key in known:
            continue
        name = str(attachment.get("filename") or "")
        try:
            if download is None or attachment.get("id") is None:
                raise ValueError("the attachment cannot be downloaded here")
            known[key] = evidence(name, download(message.external_id, attachment["id"]))
        except MailboxError as exc:
            if exc.kind != "http_error" or exc.detail.startswith("HTTP 5"):
                raise  # a busy or unreachable mailbox says nothing about the file: retry the job
            known[key] = {**evidence(name, b""), "reason": f"download failed ({exc.detail})"}
        except ValueError as exc:
            known[key] = {**evidence(name, b""), "reason": str(exc)}
    if attachments and known != raw.get("documents"):
        message.raw = {**raw, "documents": known}
        flag_modified(message, "raw")
        db.flush()
    return [known[str(a.get("id"))] for a in attachments]


@lru_cache
def requirements() -> dict:
    """Which documents a dangerous-goods request needs, and where that rule comes from."""
    path = get_settings().dg_requirements_file or Path(__file__).parent / "data" / (
        "dg_requirements.json"
    )
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    listed = [rules["required_documents"], *rules.get("by_un_number", {}).values()]
    unknown = {kind for kinds in listed for kind in kinds} - set(KINDS)
    if unknown:
        raise ValueError(f"dg requirements name unknown documents: {sorted(unknown)}")
    return rules


def required_documents(request_un: list[str]) -> list[str]:
    rules = requirements()
    special = [
        rules["by_un_number"][un] for un in request_un if un in rules.get("by_un_number", {})
    ]
    wanted = (
        {kind for kinds in special for kind in kinds} if special else rules["required_documents"]
    )
    return [kind for kind in KINDS if kind in wanted]
