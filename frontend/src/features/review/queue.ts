import type { ReviewQueue } from "../../api/client";

export type ReviewMail = ReviewQueue["mail"][number];
export type MailFilter = "all" | "unmatched" | "tariff" | "bid";
export type MailKind = Exclude<MailFilter, "all"> | "other";

/** Which kind of review a mail needs. A bid or tariff wins over a plain unmatched flag. */
export function mailKind(mail: ReviewMail): MailKind {
  if (mail.bid) return "bid";
  if (mail.tariff) return "tariff";
  if (mail.unmatched) return "unmatched";
  return "other";
}
