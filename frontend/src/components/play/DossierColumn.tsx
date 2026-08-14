import { api, type Casefile, type Provenance } from "../../api/client";
import { Portrait } from "../Portrait";
import CitedRow from "./CitedRow";

/** A 0–5 meter as five pips. Filled pips are the accent; the rest are the
 *  hairline, so an empty meter still reads as a meter rather than as nothing. */
function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div className="meter-row">
      <span className="meter-label">{label}</span>
      <span className="meter" role="img" aria-label={`${label} ${value} of 5`}>
        {[0, 1, 2, 3, 4].map((i) => (
          <span key={i} className={"meter-pip" + (i < value ? " on" : "")} />
        ))}
      </span>
    </div>
  );
}



/** The context column's other state: one actor, in full.
 *
 *  It replaces the cast grid *in place*. That is the whole design of this
 *  screen and it is worth being explicit about what it is not: there is no
 *  backdrop, no scroll lock, no focus steal, and the transcript behind it does
 *  not re-render. Opening a dossier is not an interruption of play — the
 *  composer keeps its draft and its caret, and the strip above it says so.
 *
 *  Everything here is a file the absorb pass wrote. The source is named under
 *  the paragraph on purpose: the point of the panel is that these are records
 *  you can go and read, not a summary the app invented. */
export default function DossierColumn(
  { cid, casefile, provenance, onBack, onOpenActor, onRemove, onHoverQuote,
    onGoToTurn, busy }: {
    cid: string;
    casefile: Casefile | null;
    /** Citations keyed `"<kind>/<id>#<field>"`. Empty until the read lands, and
     *  legitimately empty for a campaign absorbed before the store existed. */
    provenance: Provenance;
    onBack: () => void;
    onOpenActor: (kind: string, id: string) => void;
    onRemove: () => void;
    /** The quote under the pointer, or "" — the transcript highlights it. */
    onHoverQuote?: (quote: string) => void;
    onGoToTurn?: (quote: string) => void;
    /** True while the scene is locked — removing someone mid-turn moves the
     *  cast out from under the write in flight. */
    busy: boolean;
  },
) {
  if (!casefile) {
    return (
      <>
        <button className="column-back" onClick={onBack}>‹ All cast</button>
        <p className="column-empty">Reading…</p>
      </>
    );
  }

  const c = casefile;
  const src = c.kind === "characters" && c.version
    ? api.campaignImageUrl(cid, c.id, c.version, "avatar")
    : null;
  // The dossier paragraph is written by a later absorb phase, which rests on no
  // transcript citation to weigh — so it is uncited by construction, and the
  // marker says so rather than pretending the store lost something.
  const state = provenance[`${c.kind}/${c.id}#current_state`];
  const roleLine = [
    c.role === "player" ? "Player" : "Cast",
    c.scenes.length
      ? `in ${c.scenes.length === 1 ? "scene" : "scenes"} ${c.scenes.map((x) => x.title).join(", ")}`
      : null,
  ].filter(Boolean).join(" · ");

  return (
    <>
      <button className="column-back" onClick={onBack}>‹ All cast</button>

      <div className="dossier-head">
        <span className="dossier-portrait"><Portrait src={src} name={c.name} /></span>
        <h3 className="dossier-name">{c.name}</h3>
        <div className="dossier-role">{roleLine}</div>
      </div>

      <div className="dossier-rows">
        {/* All three carry the SAME citation, and that is not a shortcut: the
            absorb stages one `character_state` edit whose `after` is the whole
            of state.md, which `playstate.parse_body` splits into these three
            headed sections on read. One edit, one quote, three rows.

            An empty row is dropped rather than shown blank: "STANDING —" reads
            as a fact about her, and the truth is that nothing has been
            recorded yet. */}
        {c.standing && <CitedRow label="Standing" value={c.standing} citation={state}
                                 onHoverQuote={onHoverQuote} onGoToTurn={onGoToTurn} />}
        {c.knows && <CitedRow label="Knows" value={c.knows} citation={state}
                              onHoverQuote={onHoverQuote} onGoToTurn={onGoToTurn} />}
        {c.suspects && <CitedRow label="Suspects" value={c.suspects} citation={state}
                                 onHoverQuote={onHoverQuote} onGoToTurn={onGoToTurn} />}
        {/* Not cited and never will be: where she was last seen is read off the
            appearance record, not proposed by a model. */}
        {c.last_seen && (
          <div className="dossier-row">
            <span className="dossier-row-label">Last seen</span>
            <span className="dossier-row-value">{c.last_seen}</span>
          </div>
        )}
      </div>

      {(c.dossier || c.tagline) && (
        <div className="dossier-para">
          <p>{c.dossier || c.tagline}</p>
          <span className="dossier-source">{c.dossier ? "dossier.md" : "tagline.md"}</span>
        </div>
      )}
      {!c.dossier && !c.tagline && !c.standing && (
        <p className="column-empty">
          Nothing recorded yet. Play a scene with {c.name} and the absorb pass
          writes her state, her dossier and how she feels about the room.
        </p>
      )}

      {c.feels_toward.length > 0 && (
        <div className="column-section">
          <div className="column-section-head">
            <span className="section-label">Feels toward</span>
            <span className="column-count">relationships.json</span>
          </div>
          {c.feels_toward.map((f) => (
            <div className="feeling-card" key={f.ref}>
              {/* Metadata that references another record is a way to get to
                  that record — the same rule the list/detail pages follow. */}
              <button className="chip" onClick={() => onOpenActor(f.kind, f.id)}>
                {f.name}
              </button>
              <Meter label="Trust" value={f.trust} />
              <Meter label="Affection" value={f.affection} />
              <Meter label="Tension" value={f.tension} />
              {f.note && <p className="feeling-note">{f.note}</p>}
            </div>
          ))}
        </div>
      )}

      {c.standing_facts.length > 0 && (
        <div className="column-section">
          <div className="column-section-head">
            <span className="section-label">Standing facts</span>
            <span className="column-count">facts.json</span>
          </div>
          {c.standing_facts.map((f) => (
            <div className="fact-row" key={f.id}>
              <span className="fact-id">{f.id}</span>
              <div>
                <div className="fact-text">{f.text}</div>
                <div className="fact-meta">
                  {f.scene?.title || "UNRECORDED"}{f.date ? ` · ${f.date}` : ""}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="dossier-actions">
        <button className="btn-outline" onClick={() => onOpenActor(c.kind, c.id)}>
          Full record →
        </button>
        <button className="subtle danger" onClick={onRemove} disabled={busy}>
          Remove from scene
        </button>
      </div>
    </>
  );
}
