import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError, api, type Actor, type AuthorsNote, type AuthorsNotes, type AuthorsNotesNext,
} from "../api/client";

type Level = "campaign" | "scene" | "character";

const TABS: [Level, string][] = [["campaign", "Campaign"], ["scene", "This scene"],
                                 ["character", "Character"]];

/** What a note field shows when its level holds none: the server's defaults. */
const BLANK: AuthorsNote = { text: "", depth: 4, every: 1 };

function reason(err: unknown): string {
  return err instanceof ApiError ? err.detail : String(err);
}

/** One line per configured note, as the next turn will treat it. */
function nextLine(n: AuthorsNotesNext["notes"][number]): string {
  const who = n.level === "campaign" ? "Campaign" : n.level === "scene" ? "This scene"
    : (n.name ?? n.ref ?? "Character");
  if (!n.applies) return `${who}: not next turn (every ${n.every})`;
  return n.level === "character" ? `${who}: applies when ${who} speaks`
    : `${who}: applies next turn`;
}

/** The scene inspector's Author's notes section (play controls V): a standing
 *  instruction at three levels -- the campaign, this scene, and a character in
 *  it -- each inserted into the history `depth` posts from the end on every
 *  `every`-th turn.
 *
 *  `next` is the scene's `/authors-notes/next` read, owned by the inspector so
 *  the section header can show its count; `onSaved` asks it to read again. */
export function AuthorsNotesPanel({ cid, sid, cast, next, onSaved }: {
  cid: string; sid: string; cast: Actor[]; next: AuthorsNotesNext | null;
  onSaved: () => void;
}) {
  const [notes, setNotes] = useState<AuthorsNotes | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Level>("campaign");
  const npcs = useMemo(() => cast.filter((a) => a.role === "npc" && a.kind === "characters"),
                       [cast]);
  const [ref, setRef] = useState("");
  const [draft, setDraft] = useState<AuthorsNote>(BLANK);
  // A save and a reload can both be in flight; only the newest may render.
  const ticket = useRef(0);

  const charRef = ref || (npcs[0] ? `characters:${npcs[0].id}` : "");

  const stored = useCallback((data: AuthorsNotes | null): AuthorsNote => {
    if (!data) return BLANK;
    const note = tab === "campaign" ? data.campaign
      : tab === "scene" ? data.scenes[sid] : data.characters[charRef];
    return note ?? BLANK;
  }, [tab, sid, charRef]);

  // On `sid` too: a rename moves the key the scene tab reads the note under.
  useEffect(() => {
    const n = (ticket.current += 1);
    api.getAuthorsNotes(cid)
      .then((data) => { if (n === ticket.current) { setError(null); setNotes(data); } })
      .catch((err: unknown) => { if (n === ticket.current) setError(reason(err)); });
  }, [cid, sid]);

  // The form follows what is stored for the level (and character) on show.
  useEffect(() => { setDraft(stored(notes)); }, [notes, stored]);

  async function save() {
    setBusy(true);
    setError(null);
    const n = (ticket.current += 1);
    try {
      const body = { text: draft.text, depth: draft.depth, every: draft.every };
      const data = tab === "campaign" ? await api.setCampaignAuthorsNote(cid, body)
        : tab === "scene" ? await api.setSceneAuthorsNote(cid, sid, body)
        : await api.setCharacterAuthorsNote(cid, charRef, body);
      if (n === ticket.current) setNotes(data);
      onSaved();
    } catch (err: unknown) {
      if (n === ticket.current) setError(reason(err));
    } finally {
      setBusy(false);
    }
  }

  const number = (value: string, fallback: number) => {
    const parsed = Number.parseInt(value, 10);
    return Number.isNaN(parsed) ? fallback : parsed;
  };

  return (
    <div className="authors-notes">
      {next && next.notes.length > 0 && (
        <ul className="authors-notes-next">
          {next.notes.map((n) => <li key={`${n.level}:${n.ref ?? ""}`}>{nextLine(n)}</li>)}
        </ul>
      )}
      <div className="field-hint">
        The count beside the title is every note due next turn, character notes included --
        a character's note reaches only a call made for that character (a character turn, or
        a reply chip naming them), so it may not reach the next turn at all. The context
        inspector describes the turn just composed; this describes the next one.
      </div>
      <div className="tabs" role="tablist" aria-label="Author's note level">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
                  className={"tab" + (tab === key ? " active" : "")}
                  onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "character" && (npcs.length === 0
          ? <div className="field-hint">No NPC is cast in this scene.</div>
          : (
            <select aria-label="Character" value={charRef}
                    onChange={(e) => setRef(e.target.value)}>
              {npcs.map((a) => (
                <option key={a.id} value={`characters:${a.id}`}>{a.name}</option>
              ))}
            </select>
          ))}
        {(tab !== "character" || npcs.length > 0) && (
          <>
            <textarea aria-label="Author's note" maxLength={2000} rows={3}
                      value={draft.text} disabled={!notes}
                      onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
            <label>
              Depth{" "}
              <input type="number" aria-label="Depth" min={0} max={50} value={draft.depth}
                     onChange={(e) => setDraft({ ...draft,
                                                 depth: number(e.target.value, draft.depth) })} />
            </label>
            <label>
              Every{" "}
              <input type="number" aria-label="Every N turns" min={1} max={50}
                     value={draft.every}
                     onChange={(e) => setDraft({ ...draft,
                                                 every: number(e.target.value, draft.every) })} />
              {" "}turns
            </label>
            <div className="field-hint">
              Depth is posts from the end; 0 = after the last post. In a scene with no player
              posts (an offscreen scene), any depth above 0 puts the note at the start of the
              history, the first thing trimmed. Empty text clears the note.
            </div>
            <div className="form-actions">
              <button type="button" disabled={busy || !notes}
                      onClick={() => { void save(); }}>Save</button>
            </div>
          </>
        )}
        {error && <div className="banner">{error}</div>}
      </div>
    </div>
  );
}
