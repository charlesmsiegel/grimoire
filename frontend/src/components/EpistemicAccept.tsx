import { useState } from "react";
import { api } from "../api/client";
import { errorText } from "../api/errors";
import { type EpistemicItem, type EpistemicSlice, type KnowledgeStatus } from "../api/types";

const LABEL: Record<KnowledgeStatus, string> = {
  knows: "Mark as known", suspects: "Mark as suspected", unaware: "Mark as unaware",
};

/** One classified part of a past turn's history, made the user's word (spec
 *  11, 11-S3): "Mark as known / suspected / unaware" to a character, stored
 *  as an `accepted` Knowledge statement. A grant sends the fingerprints the
 *  part had WHEN THIS TURN WAS COMPOSED, so the server refuses it as `moved`
 *  when the post has changed since -- the user never grants text they were
 *  not shown. Only a part this turn's prompt carried can be granted: on an
 *  NPC's turn a part it was not given appears here by its class alone, so it
 *  can only be marked unaware. A post with no key can only be marked unaware
 *  too, which is stored about the whole scene (it withholds more, never
 *  less), and the button says so. The summary is not offered: a whole-scene
 *  grant would expose more than this turn showed. Offered on a captured turn
 *  only, never the live panel. */
export function EpistemicAccept({ cid, item, slice, readers, names, shown }: {
  cid: string; item: EpistemicItem; slice: EpistemicSlice;
  readers: string[]; names: Record<string, string>;
  /** Whether the prompt carried this part's text. */
  shown: boolean;
}) {
  const [who, setWho] = useState(readers[0] ?? "");
  const [note, setNote] = useState<string | null>(null);
  const keys = slice.keys ?? [];
  if (slice.part !== "posts" || keys.length === 0 || !who || !item.scene) return null;
  const unkeyed = keys.some((k) => !k.key);
  const mark = (status: KnowledgeStatus) => {
    const grant = status !== "unaware";
    setNote(null);
    api.ledgerCreateKnowledge(cid, {
      audience: who, status, scene: item.scene,
      posts: keys.map((k) => ({ key: k.key, part: k.part })), whole_scene: false,
      expect: grant ? keys : null, expect_digest: "", expect_shown: "",
      audience_name: names[who], source: "accepted",
    }).then(() => setNote(`Saved: ${names[who] ?? who} ${status === "knows" ? "knows"
      : status === "suspects" ? "suspects" : "is unaware of"} this.`))
      .catch((err: unknown) => {
        const kind = typeof err === "object" && err !== null
          ? (err as { kind?: unknown }).kind : undefined;
        setNote(kind === "moved" ? "This has changed since this turn; mark it from the Knowledge ledger instead."
          : errorText(err));
      });
  };
  return (
    <div className="ctx-accept">
      {readers.length > 1 ? (
        <select aria-label="Who" value={who} onChange={(e) => setWho(e.target.value)}>
          {readers.map((r) => <option key={r} value={r}>{names[r] ?? r}</option>)}
        </select>
      ) : <span className="field-hint">{names[who] ?? who}:</span>}
      {unkeyed && <span className="chip on">unkeyed</span>}
      {(unkeyed || !shown ? (["unaware"] as const)
        : (["knows", "suspects", "unaware"] as const)).map((s) => (
        <button key={s} type="button" className="subtle" onClick={() => mark(s)}>
          {LABEL[s]}{unkeyed ? " (whole scene)" : ""}
        </button>
      ))}
      {note && <div className="field-hint">{note}</div>}
    </div>
  );
}
