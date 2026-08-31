import type { Commitment, LedgerRelationship, PlotThread, RetiredFact, StandingFact } from "../../api/client";
import type { Draft, Field } from "./LedgerRowEditor";

/** Which record a row stands for, and what editing it looks like.
 *
 *  Attached to a row rather than derived from the section, because two rows in
 *  the same section can be different records — a relationship row is a
 *  directional feeling or an undirected bond, and they take different fields
 *  and different writers.
 *
 *  Deliberately carries no network: `rowsFor` is a pure projection and stays
 *  one. The view dispatches on `kind`.
 */
export type EditKind = "thread" | "commitment" | "fact" | "relationship" | "bond" | "chronicle";

export type EditSpec = {
  kind: EditKind;
  /** The record's own id — a thread slug, a fact id, a scene id. For a
   *  relationship it is the pair token the ledger already keys the row by. */
  id: string;
  fields: readonly Field[];
  initial: Draft;
  /** A relationship's two actors, which are not in `id` in a usable form. */
  pair?: { a: string; b: string };
  /** The one action worth a button on the row itself, without opening
   *  anything: the common ending for that kind of record. */
  quick?: { label: string; title: string };
  /** Absent where removal is not this page's to offer. */
  deletable?: boolean;
};

const THREAD_STATUSES = ["open", "advanced", "closed"] as const;
const COMMITMENT_KINDS = ["promise", "threat", "foreshadowing"] as const;
const COMMITMENT_STATUSES = ["open", "fulfilled", "broken", "expired"] as const;

/** A beat is appended, never replaced, so the box is always empty on open and
 *  saying so is the difference between adding a line and thinking you replaced
 *  one. Shared by both records that keep beats. */
const BEAT: Field = {
  key: "beat", label: "Add a beat", kind: "textarea",
  hint: "Appended to the record's history — leave blank to change nothing else.",
  placeholder: "What moved it",
};

const SCENE: Field = {
  key: "scene", label: "Scene", kind: "text",
  hint: "The scene this is filed under. Correct it when the absorb guessed wrong.",
};

export function threadSpec(t: PlotThread): EditSpec {
  return {
    kind: "thread", id: t.id, deletable: true,
    quick: t.status === "closed" ? undefined
      : { label: "Close", title: `Close “${t.title}”` },
    fields: [
      { key: "title", label: "Thread", kind: "text" },
      { key: "status", label: "Status", kind: "select", options: THREAD_STATUSES },
      BEAT, SCENE,
    ],
    initial: { title: t.title, status: t.status || "open", beat: "", scene: t.last_scene },
  };
}

export function commitmentSpec(c: Commitment): EditSpec {
  return {
    kind: "commitment", id: c.id, deletable: true,
    quick: c.status === "open" ? { label: "Done", title: `Mark “${c.title}” fulfilled` }
      : undefined,
    fields: [
      { key: "title", label: "Commitment", kind: "text" },
      { key: "kind", label: "Kind", kind: "select", options: COMMITMENT_KINDS },
      { key: "status", label: "Status", kind: "select", options: COMMITMENT_STATUSES },
      { key: "due", label: "Due", kind: "text",
        hint: "In the campaign's own reckoning. Empty is no deadline." },
      BEAT, SCENE,
    ],
    initial: {
      title: c.title, kind: c.kind || "promise", status: c.status || "open",
      due: c.due ?? "", beat: "", scene: c.last_scene,
    },
  };
}

/** A fact, whether it is standing or retired.
 *
 *  A retired one is still editable, and that is not an oversight: retirement is
 *  about truth and a correction is about wording. A typo in a fact that has
 *  since been superseded is still a typo, and the row is still on the page
 *  being read. What a retired fact does not get is the Retire button.
 */
export function factSpec(f: StandingFact | RetiredFact, retired: boolean): EditSpec {
  return {
    kind: "fact", id: f.id, deletable: true,
    quick: retired ? undefined : { label: "Retire", title: `Retire “${f.text}”` },
    fields: [
      { key: "text", label: "Fact", kind: "textarea",
        hint: "Corrections only. A fact that stopped being TRUE is retired, not rewritten." },
      { key: "date", label: "As of", kind: "text",
        hint: "The fiction's own dating — “the third night”, “two winters ago”." },
      SCENE,
    ],
    initial: { text: f.text, date: f.date, scene: f.scene.id },
  };
}

export function relationshipSpec(r: LedgerRelationship): EditSpec {
  const bond = r.kind === "bond";
  return {
    kind: bond ? "bond" : "relationship",
    id: r.id, deletable: true, pair: { a: r.a, b: r.b },
    fields: bond
      ? [{ key: "bond", label: "Bond", kind: "text",
           hint: "What they are to each other — “sisters”, “sworn enemies”." },
         SCENE]
      : [{ key: "trust", label: "Trust", kind: "meter" },
         { key: "affection", label: "Affection", kind: "meter" },
         { key: "tension", label: "Tension", kind: "meter" },
         { key: "note", label: "Note", kind: "text" }],
    initial: bond
      ? { bond: r.type, scene: r.since_scene }
      : {
        trust: String(r.trust), affection: String(r.affection),
        tension: String(r.tension), note: r.note,
      },
  };
}

export function chronicleSpec(sid: string, oneLine: string, date: string): EditSpec {
  return {
    // Not deletable: the record belongs to a scene, and dropping it is what
    // un-absorbing that scene means rather than a ledger edit.
    kind: "chronicle", id: sid,
    fields: [
      { key: "one_line", label: "What happened", kind: "textarea",
        hint: "The recap line. Re-absorb the scene to change what it was read as." },
      { key: "date", label: "Date", kind: "text" },
    ],
    initial: { one_line: oneLine, date },
  };
}

/** The blank a `+ New …` opens on. Same fields as an edit, so the form a
 *  reader learns creating with is the one they get correcting with. */
export function blankSpec(kind: "thread" | "commitment" | "fact" | "relationship"): EditSpec {
  if (kind === "thread") {
    return { ...threadSpec({
      id: "", title: "", status: "open", last_scene: "", latest_beat: "",
      scene: { id: "", title: "", date: "" },
    }), quick: undefined, deletable: false };
  }
  if (kind === "commitment") {
    return { ...commitmentSpec({
      id: "", title: "", status: "open", kind: "promise", due: "",
      last_scene: "", latest_beat: "", scene: { id: "", title: "", date: "" },
    }), quick: undefined, deletable: false };
  }
  if (kind === "fact") {
    return { ...factSpec({
      id: "", text: "", date: "", scene: { id: "", title: "", date: "" },
    }, false), quick: undefined, deletable: false };
  }
  return {
    kind: "relationship", id: "", deletable: false, pair: { a: "", b: "" },
    fields: [
      { key: "a", label: "From", kind: "text", hint: "An actor token — `characters/mara`." },
      { key: "b", label: "Toward", kind: "text" },
      { key: "trust", label: "Trust", kind: "meter" },
      { key: "affection", label: "Affection", kind: "meter" },
      { key: "tension", label: "Tension", kind: "meter" },
      { key: "note", label: "Note", kind: "text" },
      { key: "bond", label: "Bond instead", kind: "text",
        hint: "Filled in, this records an undirected bond and the meters are ignored." },
    ],
    initial: { a: "", b: "", trust: "0", affection: "0", tension: "0", note: "", bond: "" },
  };
}
