import { useCallback, useMemo, type Dispatch, type SetStateAction } from "react";
import type { CharacterSummary, Edges, EntitySummary, Greeting } from "../../api/client";
import { Field } from "../Field";
import { repointPrimary, toggled, type GreetingDraft } from "./form";

/** Editable greeting values and plot-map chips. Saving, import and revision
 * ownership stay in the coordinating editor. */
export function GreetingFields({ form, setForm, edges, setEdges, chars,
                                 locations, tags, greetings, gid, listsReady,
                                 locationsRead, worldScope, onImport }: {
  form: GreetingDraft; setForm: Dispatch<SetStateAction<GreetingDraft>>;
  edges: Edges; setEdges: Dispatch<SetStateAction<Edges>>;
  chars: CharacterSummary[]; locations: EntitySummary[];
  tags: Record<string, string>; greetings: Greeting[]; gid: string | null;
  listsReady: boolean; locationsRead: boolean; worldScope: boolean;
  onImport: () => void;
}) {
  const versions = chars.find((c) => c.id === form.character)?.versions ?? [];
  const others = useMemo(() => greetings.filter((g) => g.id !== gid), [greetings, gid]);
  const existing = gid !== null;
  // Updaters rather than a spread of the form this render holds: the chip
  // lists below that call these are memoized, so a click can reach a closure
  // from several keystrokes ago -- and spreading THAT form would put the body
  // back the way it was then.
  const toggle = useCallback((list: "leads_to" | "excludes", id: string) =>
    setEdges((e) => ({ ...e, [list]: toggled(e[list], id) })), [setEdges]);
  const toggleTag = useCallback((tid: string) =>
    setForm((f) => ({ ...f, requires_tags: toggled(f.requires_tags, tid) })), [setForm]);
  const togglePresent = useCallback((cid: string) =>
    setForm((f) => ({ ...f, present: toggled(f.present, cid) })), [setForm]);

  // The form's long lists, for the rail's reason: each rebuilt only when what
  // it lists or which of them are on changes, never for a keystroke elsewhere.
  const characterOptions = useMemo(
    () => chars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>), [chars]);
  const locationOptions = useMemo(
    () => locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>), [locations]);
  const presentChips = useMemo(() => chars.map((c) => (
    <button key={c.id} className={"chip" + (form.present.includes(c.id) ? " on" : "")}
            onClick={() => togglePresent(c.id)}>{c.name}</button>
  )), [chars, form.present, togglePresent]);
  const tagChips = useMemo(() => Object.keys(tags).sort().map((tid) => (
    <button key={tid} className={"chip" + (form.requires_tags.includes(tid) ? " on" : "")}
            onClick={() => toggleTag(tid)}>{tags[tid]}</button>
  )), [tags, form.requires_tags, toggleTag]);
  const leadsToChips = useMemo(
    () => edgeChips(others, edges.leads_to, (id) => toggle("leads_to", id)),
    [others, edges.leads_to, toggle]);
  const excludesChips = useMemo(
    () => edgeChips(others, edges.excludes, (id) => toggle("excludes", id)),
    [others, edges.excludes, toggle]);

  return <>
    <Field label="Name">
      <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
    </Field>
    <Field label="Character"
           hint={existing ? "re-pointing keeps this greeting's id and plot-map edges; {{char}} already baked into the text does not change" : undefined}>
      <select value={form.character} aria-label="Character"
              onChange={(e) => {
                const next = e.target.value;
                setForm((draft) => repointPrimary(draft, next));
              }}>
        <option value="">— no character (narrator-only) —</option>
        {characterOptions}
        {/* A stored character the list does not offer (deleted since, or
            the list failed). Without this the controlled select renders
            the narrator option while the field still holds the dead id —
            same rule as the location picker below. */}
        {form.character && !chars.some((c) => c.id === form.character) && (
          <option value={form.character}>
            {listsReady ? `${form.character} (missing)` : form.character}
          </option>
        )}
      </select>
    </Field>
    {form.character && (
      <Field label="Version">
        <select value={form.version} aria-label="Version"
                onChange={(e) => setForm({ ...form, version: e.target.value })}>
          <option value="">— pick a version —</option>
          {versions.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          {form.version && !versions.some((v) => v.id === form.version) && (
            <option value={form.version}>
              {listsReady ? `${form.version} (missing)` : form.version}
            </option>
          )}
        </select>
      </Field>
    )}
    {worldScope && form.character && (
      <div className="form-actions">
        <button className="subtle" onClick={onImport} disabled={!form.character || !form.version}>
          Import greetings from this character/version
        </button>
      </div>
    )}
    <Field label="Greeting text">
      <textarea value={form.body} rows={6} onChange={(e) => setForm({ ...form, body: e.target.value })} />
    </Field>
    <Field label="Offscreen"
           hint="an NPC-only opener — no player character; {{user}} becomes your PC's name">
      <div className="chips">
        <button className={"chip" + (form.pcless ? " on" : "")}
                onClick={() => setForm({ ...form, pcless: !form.pcless })}>
          Offscreen (no PC)
        </button>
      </div>
    </Field>
    <Field label="Location"
           hint="where a scene from this greeting opens — the new-scene form starts here, and can be changed or cleared there">
      <select value={form.location} aria-label="Location"
              onChange={(e) => setForm({ ...form, location: e.target.value })}>
        <option value="">— no location —</option>
        {locationOptions}
        {/* A stored id the list does not offer. Without this the
            controlled select renders blank, which claims the greeting
            has no location while the field still holds one, and the next
            save would silently carry it through. Shown as itself, so it
            can be seen and cleared — but called "missing" only once the
            list has actually come back, because until then its absence
            says nothing about the location. */}
        {form.location && !locations.some((l) => l.id === form.location) && (
          <option value={form.location}>
            {locationsRead ? `${form.location} (missing)` : form.location}
          </option>
        )}
      </select>
    </Field>
    <Field label="Present characters" hint="everyone cast into the scene when it starts from this greeting">
      <div className="chips">
        {presentChips}
        {chars.length === 0 && <span className="field-hint">No characters in this world yet.</span>}
      </div>
    </Field>
    <Field label="Phase" hint="story phase used to keep recommendations near the current arc">
      <input aria-label="Phase" type="text" value={form.phase}
             onChange={(e) => setForm({ ...form, phase: e.target.value })} />
    </Field>
    <Field label="Sequence" hint="author order within the phase">
      <input aria-label="Sequence" type="number" min="1" value={form.sequence ?? ""}
             onChange={(e) => setForm({ ...form, sequence: e.target.value ? Number(e.target.value) : null })} />
    </Field>
    <Field label="Optional" hint="recommend alongside direct successors while this phase is current">
      <div className="chips">
        <button className={"chip" + (form.optional ? " on" : "")}
                onClick={() => setForm({ ...form, optional: !form.optional })}>
          Optional within phase
        </button>
      </div>
    </Field>
    <Field label="Required tags" hint="the greeting unlocks only if a player PC carries these">
      <div className="chips">
        {tagChips}
        {Object.keys(tags).length === 0 && <span className="field-hint">No tags in this world yet.</span>}
      </div>
    </Field>
    <Field label="Predecessor join">
      <select value={form.predecessor_join} aria-label="Predecessor join"
              onChange={(e) => setForm({ ...form, predecessor_join: e.target.value as "all" | "any" })}>
        <option value="all">all predecessors must be played</option>
        <option value="any">any predecessor unlocks it</option>
      </select>
    </Field>
    <Field label="Leads to" hint="greetings this one unlocks once played">
      <div className="chips">
        {leadsToChips}
        {others.length === 0 && <span className="field-hint">No other greetings yet.</span>}
      </div>
    </Field>
    <Field label="Excludes" hint="playing this one locks these (mutually exclusive)">
      <div className="chips">
        {excludesChips}
      </div>
    </Field>
  </>;
}

/** One plot-map edge list as chips: every other greeting, lit where `on` names it. */
function edgeChips(others: Greeting[], on: string[], onToggle: (id: string) => void) {
  return others.map((g) => (
    <button key={g.id} className={"chip" + (on.includes(g.id) ? " on" : "")}
            onClick={() => onToggle(g.id)}>{g.name}</button>
  ));
}

