import { humanize, money, timeAgo } from "../../lib/format";
import { Icon } from "./bits";
import { type Candidate, dateTime, type DetailView, msUntil } from "./model";

/** Rate IDs from the challenge's rate portal are bookable for 15 minutes. */
const RATE_ID_LIFETIME_MS = 15 * 60_000;

function amountParts(value: string | null | undefined, currency: string) {
  const n = Number(value);
  if (value == null || !Number.isFinite(n)) return { whole: "—", cents: "" };
  let formatted: string;
  try {
    formatted = new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    formatted = n.toFixed(2);
  }
  const cut = formatted.lastIndexOf(".");
  return cut > 0 ? { whole: formatted.slice(0, cut), cents: formatted.slice(cut) } : { whole: formatted, cents: "" };
}

function RateValidity({ view, now }: { view: DetailView; now: number }) {
  const given = view.offer?.valid_until;
  if (given) {
    const left = msUntil(given, now);
    return (
      <>
        {dateTime(given)}{" "}
        <span className={`chip ${left > 0 ? "chip-positive" : "chip-negative"}`}>
          {left > 0 ? "valid" : "expired"}
        </span>
      </>
    );
  }
  const decided = view.quote?.decided_at;
  if (!decided) return <>Not given</>;
  const until = new Date(new Date(decided).getTime() + RATE_ID_LIFETIME_MS).toISOString();
  const left = msUntil(until, now);
  return (
    <>
      {dateTime(until)} <span className="subtle">(derived: quote + 15 min)</span>{" "}
      <span className={`chip ${left > 0 ? "chip-positive" : "chip-negative"}`}>
        {left > 0 ? "valid" : "lapsed"}
      </span>
    </>
  );
}

export function QuoteCard({ view, now }: { view: DetailView; now: number }) {
  const { offer, quote, request } = view;
  const currency = quote?.currency ?? offer?.currency ?? "EUR";
  const total = quote?.total ?? offer?.total ?? null;
  const parts = amountParts(total, currency);
  const charges = (offer?.charges ?? []).filter((c) => Number.isFinite(Number(c.amount)));
  const chargeSum = charges.reduce((sum, c) => sum + Number(c.amount), 0);
  const candidates = (quote?.evidence?.candidate_assessment ?? []) as Candidate[];
  const intent = request?.service_intent;

  if (!offer && !quote) {
    return (
      <section className="card ss-quote" aria-labelledby="ss-quote-h">
        <h2 id="ss-quote-h" className="card-title">
          Quote
        </h2>
        <p className="ss-quote-empty muted">
          <Icon name="coins" size={20} /> No price chosen yet. It shows here the moment the agent picks an offer.
        </p>
      </section>
    );
  }

  return (
    <section className="card ss-quote" aria-labelledby="ss-quote-h">
      <div className="card-head">
        <div>
          <h2 id="ss-quote-h" className="card-title">
            {quote ? "Quoted all-in price" : "Selected offer (not quoted yet)"}
          </h2>
          <p className="card-sub">{offer?.carrier ?? "Carrier not given"}</p>
        </div>
        {intent && (
          <span className="chip chip-info">
            <Icon name={intent === "fastest" ? "clock" : "coins"} size={12} /> {humanize(intent)}
          </span>
        )}
      </div>

      <p className="ss-amount num" aria-label={`${money(total, currency)} all-in`}>
        <span aria-hidden="true">{parts.whole}</span>
        <span className="ss-amount-cents" aria-hidden="true">
          {parts.cents}
        </span>
        <span className="ss-amount-cur" aria-hidden="true">
          {currency}
        </span>
      </p>

      <dl className="kv ss-quote-kv">
        <dt>Carrier</dt>
        <dd>
          {offer?.carrier ?? "—"}
          {offer?.carrier_code ? <span className="subtle"> · {offer.carrier_code}</span> : null}
        </dd>
        <dt>Transit</dt>
        <dd className="num">
          {offer?.transit_days != null ? `${offer.transit_days} day${offer.transit_days === 1 ? "" : "s"}` : "—"}
        </dd>
        <dt>Rate ID</dt>
        <dd className="mono">{offer?.rate_id ?? "—"}</dd>
        <dt>Valid until</dt>
        <dd>
          <RateValidity view={view} now={now} />
        </dd>
        {quote?.decided_at && (
          <>
            <dt>Decided</dt>
            <dd>
              {dateTime(quote.decided_at)} <span className="subtle">· {timeAgo(quote.decided_at, now)}</span>
            </dd>
          </>
        )}
        {quote?.evidence?.rule && (
          <>
            <dt>Rule</dt>
            <dd>{humanize(quote.evidence.rule)}</dd>
          </>
        )}
        {offer?.source_ref && (
          <>
            <dt>Source</dt>
            <dd className="mono ss-break">
              {offer.source_ref}
              {offer.source_version ? ` @ ${offer.source_version}` : ""}
            </dd>
          </>
        )}
      </dl>

      {charges.length > 0 && (
        <div className="ss-charges">
          <h3 className="ss-h3">Charges</h3>
          <div className="ss-charge-bar" role="img" aria-label="Share of each charge in the total, listed below">
            {charges.map((c, i) => (
              <span
                key={c.code + i}
                style={{
                  width: `${chargeSum > 0 ? (Number(c.amount) / chargeSum) * 100 : 0}%`,
                  background: `var(--series-${(i % 6) + 1})`,
                }}
              />
            ))}
          </div>
          <ul className="ss-charge-list">
            {charges.map((c, i) => (
              <li key={c.code + i}>
                <span className="ss-swatch" style={{ background: `var(--series-${(i % 6) + 1})` }} />
                <span>{humanize(c.code)}</span>
                {c.unit && <span className="subtle">per {c.unit}</span>}
                <span className="num ss-charge-amt">{money(c.amount, c.currency ?? currency)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {candidates.length > 1 && (
        <details className="ss-disclosure">
          <summary>
            Offers compared <span className="subtle">({candidates.length})</span>
          </summary>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Carrier</th>
                  <th scope="col">Total</th>
                  <th scope="col">Transit</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c, i) => (
                  <tr key={(c.rate_id ?? "") + i}>
                    <td>{c.carrier ?? "—"}</td>
                    <td className="num">{money(c.total, c.currency ?? currency)}</td>
                    <td className="num">{c.transit_days != null ? `${c.transit_days} d` : "—"}</td>
                    <td>
                      {c.result === "selected" ? (
                        <span className="chip chip-positive">Selected</span>
                      ) : (
                        <span className="subtle">{humanize(c.result)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}

export function BookingCard({ view }: { view: DetailView }) {
  const b = view.booking;
  if (!b) return null;
  const ref = b.booking_reference ?? b.reference;
  const confirmed = b.status === "confirmed";
  return (
    <section className="card ss-booking" aria-labelledby="ss-booking-h">
      <div className="card-head">
        <h2 id="ss-booking-h" className="card-title">
          <Icon name="box" size={18} /> Booking
        </h2>
        <span className={`chip ${confirmed ? "chip-positive" : "chip-warning"}`}>{humanize(b.status)}</span>
      </div>
      <p className="ss-booking-ref mono ss-break">{ref ?? "No booking reference yet"}</p>
      <dl className="kv">
        <dt>Team ref</dt>
        <dd className="mono ss-break">{b.team_reference ?? "—"}</dd>
        <dt>Rate ID</dt>
        <dd className="mono">{b.rate_id ?? "—"}</dd>
        <dt>Booked total</dt>
        <dd className="num">{b.total ? money(b.total, b.currency ?? "EUR") : "—"}</dd>
      </dl>
    </section>
  );
}
