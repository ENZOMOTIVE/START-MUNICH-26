/**
 * Snapshot of the START Munich board (Standings tab, "updated 11:32", 193 rounds sent).
 * Typed in by hand from the board, not read from our API, so it does not update by itself:
 * change SNAPSHOT and ROWS together when you copy a newer board.
 * Only columns whose meaning is certain are kept. Checked against the board's own totals:
 * accepted sums to 1994 and bookings to 1710. The two columns without a header on the
 * pasted board are left out rather than guessed.
 */
export const OUR_TEAM = "FREE CANDY";

export const SNAPSHOT = {
  updatedAt: "11:32",
  roundsSent: 193,
  teams: 15,
};

export type BoardRow = {
  team: string;
  points: number;
  accepted: number;
  answered: number;
  bookings: number;
};

export const ROWS: BoardRow[] = [
  { team: "START Milan", points: 18654.02, accepted: 172, answered: 191, bookings: 151 },
  { team: "Mythos", points: 18531.78, accepted: 168, answered: 193, bookings: 147 },
  { team: "Leylü", points: 17926.28, accepted: 164, answered: 188, bookings: 142 },
  { team: "Google", points: 17896.02, accepted: 166, answered: 192, bookings: 160 },
  { team: "TALOS", points: 16032.16, accepted: 152, answered: 189, bookings: 133 },
  { team: "WRATHOFCODE", points: 15606.03, accepted: 154, answered: 189, bookings: 124 },
  { team: "MAKEANOTHERMD", points: 15226.7, accepted: 142, answered: 178, bookings: 122 },
  { team: "PIXEL", points: 14932.75, accepted: 142, answered: 187, bookings: 121 },
  { team: "ATLA", points: 14730.33, accepted: 148, answered: 180, bookings: 134 },
  { team: "BLACKBOX", points: 14407.43, accepted: 138, answered: 173, bookings: 111 },
  { team: "Austrian Power", points: 13405.94, accepted: 132, answered: 164, bookings: 113 },
  { team: "AEIOU", points: 12642.29, accepted: 131, answered: 174, bookings: 96 },
  { team: "FREE CANDY", points: 9527.89, accepted: 93, answered: 134, bookings: 81 },
  { team: "vietgang", points: 7239.72, accepted: 92, answered: 135, bookings: 75 },
  { team: "REISY", points: 0, accepted: 0, answered: 0, bookings: 0 },
];

const perAccepted = (r: BoardRow) => (r.accepted > 0 ? r.points / r.accepted : 0);
const rate = (r: BoardRow) => (r.answered > 0 ? r.accepted / r.answered : 0);

/** Rank (1 = best) of `team` by `by` among all listed teams. */
function rankBy(team: string, by: (r: BoardRow) => number): number {
  const mine = ROWS.find((r) => r.team === team);
  return mine ? 1 + ROWS.filter((r) => by(r) > by(mine)).length : 0;
}

export function standing(team = OUR_TEAM) {
  const mine = ROWS.find((r) => r.team === team);
  if (!mine) return null;
  const leader = [...ROWS].sort((a, b) => b.points - a.points)[0]!;
  return {
    row: mine,
    rank: rankBy(team, (r) => r.points),
    teams: ROWS.length,
    perAccepted: perAccepted(mine),
    perAcceptedRank: rankBy(team, perAccepted),
    rate: rate(mine),
    rateRank: rankBy(team, rate),
    roundsMissed: SNAPSHOT.roundsSent - mine.answered,
    leader,
    gapToLeader: leader.points - mine.points,
  };
}

export { perAccepted, rate };
