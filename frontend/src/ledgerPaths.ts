/** The Ledger's addresses (capstone §12.1), built and parsed only here.
 *
 *  `/campaigns/<cid>/ledger[/<section>[/<row>]]` for the ledger's own sections,
 *  and `/campaigns/<cid>/ledger/continuity/<group>[/<candidate>]` for the
 *  continuity review. Every segment is encoded whole with `encodeSegment`, so a
 *  model-written plot id holding a `/` or a `:` (`mara/map`, `act:2`) stays one
 *  segment; the caller parses from the RAW `location.pathname`, because
 *  `useParams()["*"]` has already decoded it and lost that difference.
 *
 *  The server builds the same grammar for Todo's links (`routes/todo.py`
 *  `_ledger_href`), so a deep link from a chore lands here.
 *
 *  A leaf: no React, and nothing but types from the API module.
 */
import type { CandidateKind, ContinuityGroup } from "./api/types";
import { encodeSegment } from "./urlSegment";

/** Every section, in the order the column lists them. */
export const LEDGER_SECTIONS = [
  "facts", "threads", "commitments", "relationships", "standings", "changes", "timeline",
  "continuity",
] as const;
export type LedgerSection = (typeof LEDGER_SECTIONS)[number];

/** The continuity review's groups. `/ledger/continuity` alone opens the first. */
export const CONTINUITY_GROUPS: readonly ContinuityGroup[] = [
  "overlaps", "closures", "resolutions", "reviewed", "dismissed",
];

/** Where each kind of finding is reviewed (§6.2). */
export const GROUP_OF: Record<CandidateKind, ContinuityGroup> = {
  possible_duplicate: "overlaps",
  possible_relation: "overlaps",
  possible_thread_closure: "closures",
  possible_commitment_resolution: "resolutions",
};

export type LedgerTarget =
  | { section: Exclude<LedgerSection, "continuity">; row?: string }
  | { section: "continuity"; group: ContinuityGroup; candidate?: string };

/** The address of a section, a row, a group or a finding. The facts section
 *  with no row is the bare `/ledger` (Decision 22): it is where the page opens,
 *  so it has one address rather than two. */
export function ledgerHref(cid: string, target: LedgerTarget): string {
  const parts = [`/campaigns/${encodeSegment(cid)}/ledger`];
  if (target.section === "continuity") {
    parts.push("continuity", target.group);
    if (target.candidate) parts.push(encodeSegment(target.candidate));
  } else if (target.section !== "facts" || target.row) {
    parts.push(target.section);
    if (target.row) parts.push(encodeSegment(target.row));
  }
  return parts.join("/");
}

const isSection = (s: string): s is LedgerSection =>
  (LEDGER_SECTIONS as readonly string[]).includes(s);
const isGroup = (s: string): s is ContinuityGroup =>
  (CONTINUITY_GROUPS as readonly string[]).includes(s);

/** What `tail` (everything after `/ledger`, still encoded) addresses, or null
 *  for one that names no section or group -- which the page replaces with the
 *  bare `/ledger` rather than rendering an empty screen. Empty segments are
 *  dropped, at most three are read, and an undecodable one is null rather than
 *  a throw from inside a render. */
export function parseLedgerTail(tail: string): LedgerTarget | null {
  const raw = tail.split("/").filter(Boolean).slice(0, 3);
  let segments: string[];
  try {
    segments = raw.map((s) => decodeURIComponent(s));
  } catch {
    return null;
  }
  const [section = "facts", second, third] = segments;
  if (!isSection(section)) return null;
  if (section === "continuity") {
    const group = second ?? "overlaps";
    if (!isGroup(group)) return null;
    return third ? { section, group, candidate: third } : { section, group };
  }
  return second ? { section, row: second } : { section };
}
