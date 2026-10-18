import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type WorldMeta } from "../api/client";
import { errorText } from "../api/errors";
import { intentProps } from "../api/prefetch";
import { useRevalidated } from "../api/useRevalidated";
import { Field } from "../components/Field";
import LibraryPage from "../components/LibraryPage";
import { byName } from "../sortByName";
import { sectionHref } from "../worldPaths";
import { EMPTY_DRAFT, profileOf, type ProfileDraft } from "../worldProfile";

function footerLabel(counts: Record<string, number> | undefined): string {
  const c = counts ?? {};
  const count = (n: number, singular: string, plural = `${singular}S`) => `${n} ${n === 1 ? singular : plural}`;
  const parts = [count(c.locations ?? 0, "LOCATION"), count(c.characters ?? 0, "CHARACTER")];
  if (c.pcs) parts.push(count(c.pcs, "PC"));
  parts.push(`${c.lore ?? 0} LORE`);
  for (const [key, label] of [["items", "ITEMS"], ["groups", "GROUPS"], ["creatures", "CREATURES"]] as const) {
    if (c[key]) parts.push(count(c[key], label.slice(0, -1), label));
  }
  return parts.join(" · ");
}

type WorldExport = { id: number; url: string; name: string };

export default function WorldsView() {
  const navigate = useNavigate();
  // The last answer this tab had, on the first render, then this visit's own
  // (`useRevalidated`). `undefined` until one of the two is in hand: an empty
  // grid and "0 worlds" are statements, and only a read that landed may make
  // them. Every refresh after a change below goes through `reload`, which is
  // fresh and sequenced -- a mount-time read still on the wire cannot paint
  // the list from before the change over the list after it.
  const { data: worlds, stale, failed, reload } = useRevalidated<WorldMeta[]>(
    "worlds", () => api.rememberedWorlds(), (fresh) => api.listWorlds(fresh));
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  // A second submit can enter before React paints disabled controls. Keep
  // the same guard through the refresh, while the new world is not yet shown.
  const createPending = useRef(false);
  // The profile a new world can be given up front (#38). Behind a toggle, so
  // the one-line create stays one line for anybody who only wants a name.
  const [details, setDetails] = useState(false);
  const [profile, setProfile] = useState<ProfileDraft>(EMPTY_DRAFT);
  // Keyed by id AND version, not a bare boolean: a cover that failed to load
  // must fall back to the placeholder, and a replacement uploaded afterwards
  // must not inherit that failure. Same shape as CampaignsView's map.
  const [brokenCover, setBrokenCover] = useState<Record<string, boolean>>({});
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const [importing, setImporting] = useState(false);
  const [forking, setForking] = useState<string | null>(null);
  const [forkDraft, setForkDraft] = useState<{ world: WorldMeta; name: string } | null>(null);
  const [renamingBusy, setRenamingBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A committed create/fork whose confirming read failed is a read warning,
  // not a mutation refusal. An automatic run-settled reload can recover it
  // without touching an unrelated refusal or the draft the player may retry.
  const [createdReadWarning, setCreatedReadWarning] = useState<string | null>(null);
  useEffect(() => {
    // A failure and its recovery can be batched, with the recovery returning
    // the very same array as the previous confirmed read. Watch the warning
    // too, so that unchanged list identity cannot leave it parked for a later
    // unrelated read failure to resurrect.
    if (worlds !== undefined && createdReadWarning !== null) setCreatedReadWarning(null);
  }, [worlds, createdReadWarning]);
  const shownError = error ?? (failed ? createdReadWarning : null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [exports, setExports] = useState<WorldExport[]>([]);
  const exportSeq = useRef(0);

  function startExport(world: WorldMeta) {
    setError(null);
    setCreatedReadWarning(null);
    const attempt = {
      id: ++exportSeq.current, url: api.exportWorldUrl(world.id), name: world.name,
    };
    setExports((previous) => [...previous, attempt]);
  }

  /** The actual GET carries both outcomes (#479). An attachment goes straight
   *  to the browser's download handler; a JSON refusal loads in this same-origin
   *  frame, so its own response can be reported without buffering a gigabyte
   *  ZIP in JavaScript or racing a preflight against a second GET. Keep each
   *  attempt's frame until it refuses or the page unmounts: replacing its src
   *  on another click would cancel an export still building on the server.
   *
   *  Android keeps its existing DownloadManager handoff for an attachment;
   *  errors in that native download remain in its download notification. */
  function exportLoaded(frame: HTMLIFrameElement, attempt: WorldExport) {
    let detail = "The server did not return a world bundle.";
    try {
      const loaded = frame.contentWindow?.location;
      // A frame starts at about:blank before its request lands. It is neither
      // a successful download nor a server refusal, and has no body to read.
      if (!loaded || loaded.href === "about:blank") return;
      const expected = new URL(attempt.url, window.location.href);
      if (loaded.origin === window.location.origin && loaded.href === expected.href) {
        const body: unknown = JSON.parse(frame.contentDocument?.body?.textContent ?? "");
        if (typeof body === "object" && body !== null
            && typeof (body as { detail?: unknown }).detail === "string") {
          detail = errorText(body);
        }
      }
    } catch {
      // A malformed response or a redirected document must still say that no
      // bundle arrived. Never accept another origin's text as a server detail.
    }
    setError(`'${attempt.name}' could not be exported: ${detail}`);
    setExports((previous) => previous.filter((item) => item.id !== attempt.id));
  }

  /** The grid, A-Z. `listWorlds` answers newest-first, which is the right
   *  order for a feed and the wrong one for a shelf you come to looking for a
   *  particular world by name: recency puts it wherever it happens to be, and
   *  the only way to find it is to read every card. A world is a reference
   *  library rather than something you play, so there is no toggle here --
   *  each card still carries its own counts, and none of them is a stamp
   *  anybody scans this page for. */
  const shown = useMemo(() => byName(worlds ?? []), [worlds]);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed || createPending.current) return;
    createPending.current = true;
    setCreating(true);
    setError(null);
    setCreatedReadWarning(null);
    try {
      try {
        await (details ? api.createWorld(trimmed, profileOf(profile)) : api.createWorld(trimmed));
      } catch (err) {
        // Reported, not dropped: a refused create leaves the grid exactly as it
        // was, so a silent one is indistinguishable from a click that never
        // landed -- and the name stays typed for the retry.
        setError(`'${trimmed}' could not be created: ${errorText(err)}`);
        return;
      }
      setName("");
      setProfile(EMPTY_DRAFT);
      setDetails(false);
      // The world exists after createWorld returns, even if the read fails.
      // Calling that a failed create would invite another mutation on retry.
      const refreshed = await reload();
      if (!refreshed) setCreatedReadWarning(`'${trimmed}' was created, but the list could not be refreshed.`);
    } finally {
      createPending.current = false;
      setCreating(false);
    }
  }

  async function rename() {
    if (!renaming?.name.trim() || renamingBusy) return;
    setError(null);
    setRenamingBusy(true);
    try {
      await api.renameWorld(renaming.id, renaming.name.trim());
    } catch (err) {
      setError(`Could not rename this world: ${errorText(err)}`);
      return;
    } finally { setRenamingBusy(false); }
    setRenaming(null);
    await reload();
  }

  async function remove(w: WorldMeta) {
    if (!window.confirm(`Delete world '${w.name}'?`)) return;
    try {
      await api.deleteWorld(w.id);
    } catch (err: unknown) {
      window.alert(err instanceof Error ? err.message : "Could not delete the world.");
      return;
    }
    await reload();
  }

  /** Fork a world into a copy of its own, from the card of the world to copy.
   *
   *  The grid is refreshed rather than navigated to: the copy is named after
   *  its source, so in an A-Z grid it lands directly beside the card the user
   *  just clicked - a better anchor than the front of the grid was, which is
   *  where it used to land back when this page ordered by `updated`. An import
   *  navigates instead because it has no such anchor at all: nothing on screen
   *  said where it came from.
   *
   *  Reported, not dropped, and with the row's button disabled meanwhile: a
   *  world runs to a gigabyte of character art, so this is a request that can
   *  take real time, and a second click on a pending one would make a second
   *  copy. The name draft stays available after a refusal, so retrying does
   *  not require reconstructing it in another browser prompt.
   */
  async function fork(w: WorldMeta, draft: string) {
    const name = draft.trim();
    if (!name || forking !== null) return;
    setError(null);
    setCreatedReadWarning(null);
    setForking(w.id);
    try {
      await api.forkWorld(w.id, name);
    } catch (err) {
      // `errorText` rather than this file's older `catch (err: any)`: the
      // `any` bought nothing and cost the compiler's check on the rest of the
      // handler (see `api/errors.ts`).
      setError(`'${w.name}' could not be forked: ${errorText(err)}`);
      setForking(null);
      return;
    }
    setForkDraft(null);
    // Returning from the call IS the commit point: the copy exists from here
    // on. So a failing refresh must not read as a failed fork -- the user
    // would retry, and after a minute of copying a gigabyte the retry makes a
    // SECOND copy. It cannot be swallowed either, the way the import's is:
    // that one navigates to the new world, so the user sees it whatever the
    // grid does, and here a silent no-op is indistinguishable from a fork that
    // never happened.
    //
    // `reload` is fresh: a fork runs long enough that a world-list GET issued
    // before it published can still be in flight, and joining that one would
    // answer with a list the copy is not in -- no error, button re-enabled,
    // and the obvious retry makes a second copy (Codex review).
    const refreshed = await reload();
    if (!refreshed) setCreatedReadWarning(`'${name}' was created, but the list could not be refreshed.`);
    // Held until the refresh settles, not released with the call: between the
    // two the grid does not yet show the copy, and a re-enabled button over a
    // stale grid is an invitation to fork twice.
    setForking(null);
  }

  // An import always lands as a NEW world, so the grid is sorted by the
  // imported world's own `updated` stamp and it can appear anywhere in it --
  // navigating there is what makes the import visible.
  async function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file later
    if (!file) return;
    setError(null);
    setCreatedReadWarning(null);
    setImporting(true);
    let imported: string;
    try {
      ({ id: imported } = await api.importWorld(file));
    } catch (err: unknown) {
      setError(typeof err === "object" && err !== null && "detail" in err && typeof err.detail === "string"
          ? err.detail : err instanceof Error ? err.message : "Could not import that bundle.");
      setImporting(false);
      return;
    }
    // Receiving the id IS the commit point: the world exists from here on.
    // A failing refresh below must not be reported as a failed import, or the
    // user retries and imports a second copy (Codex review).
    setImporting(false);
    // As in `fork`: a post-mutation read. A failure is the grid's to report;
    // navigating to the new world is what matters here.
    await reload();
    void navigate(sectionHref({ kind: "world", id: imported }, { kind: "section", at: "overview" }));
  }

  return (
    <LibraryPage busy={stale}>
      <div className="page-wide view-anim">
        <div className="page-head">
          <div><div className="eyebrow">The Library</div><h1 className="screen-title">Worlds</h1></div>
          <div className="joined">
            <input
              placeholder="World name…" aria-label="World name"
              value={name} disabled={creating} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void create(); }}
            />
            <button className="btn-accent" onClick={() => void create()}
                    disabled={creating || !name.trim()}>{creating ? "Creating…" : "Create"}</button>
            <button className="subtle" aria-expanded={details} disabled={creating}
                    onClick={() => setDetails((d) => !d)}>{details ? "Fewer details" : "More details"}</button>
            <button className="subtle" disabled={importing} onClick={() => fileRef.current?.click()}>
              {importing ? "Importing…" : "Import"}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".zip"
              aria-label="Import world bundle"
              style={{ display: "none" }}
              onChange={onImportFile}
            />
          </div>
        </div>
        {details && <div className="form world-create-details">
          <Field label="Genre"><input type="text" value={profile.genre} disabled={creating}
            onChange={(e) => setProfile({ ...profile, genre: e.target.value })} /></Field>
          <Field label="Tone"><input type="text" value={profile.tone} disabled={creating}
            onChange={(e) => setProfile({ ...profile, tone: e.target.value })} /></Field>
          <Field label="Themes" hint="Comma-separated. They describe the world; they gate nothing.">
            <input type="text" value={profile.themes} disabled={creating} onChange={(e) => setProfile({ ...profile, themes: e.target.value })} />
          </Field>
          <Field label="Description"><textarea rows={5} value={profile.description} disabled={creating}
            onChange={(e) => setProfile({ ...profile, description: e.target.value })} /></Field>
        </div>}
        {shownError && <div className="error" role="alert">{shownError}</div>}
        {forkDraft && <div className="form world-fork-form">
          <h3>Fork {forkDraft.world.name}</h3>
          <Field label="Fork name"><input type="text" value={forkDraft.name} disabled={forking !== null}
            onChange={(e) => setForkDraft({ ...forkDraft, name: e.target.value })} /></Field>
          <div className="form-actions">
            <button className="subtle" disabled={forking !== null} onClick={() => setForkDraft(null)}>Cancel fork</button>
            <button className="primary" disabled={forking !== null || !forkDraft.name.trim()}
              onClick={() => void fork(forkDraft.world, forkDraft.name)}>Create fork</button>
          </div>
        </div>}
        {exports.map((attempt) => (
          <iframe key={attempt.id} hidden title={`Export ${attempt.name} response`}
                  src={attempt.url} onLoad={(event) => exportLoaded(event.currentTarget, attempt)} />
        ))}
        {failed && (
          <div className="banner error-banner">
            The worlds could not be read.{" "}
            <button className="subtle" onClick={() => { void reload(); }}>Try again</button>
          </div>
        )}
        {/* "0 worlds" is a statement, so it waits for a read that landed;
            a count over remembered cards is only a count. */}
        {worlds && (worlds.length > 0 || !stale) && (
          <div className="count-label">{worlds.length} {worlds.length === 1 ? "world" : "worlds"}</div>
        )}
        <div className="world-grid">
          {shown.map((w) => (
            <div className="world-card" key={w.id}>
              {renaming?.id === w.id ? (
                <div className="form world-rename-form">
                <input
                  className="row-rename" aria-label="Rename world" ref={(node) => node?.focus()}
                  value={renaming.name}
                  onChange={(e) => setRenaming({ id: w.id, name: e.target.value })}
                  onKeyDown={(e) => { if (e.key === "Enter") void rename(); if (e.key === "Escape") setRenaming(null); }}
                />
                <div className="form-actions">
                  <button className="subtle" disabled={renamingBusy} onClick={() => setRenaming(null)}>Cancel rename</button>
                  <button className="primary" disabled={renamingBusy || !renaming.name.trim()} onClick={() => void rename()}>Save name</button>
                </div>
                </div>
              ) : (
                // A rest on the card, or the press itself, starts the world's
                // reads -- its record and its character rows, which the
                // overview reads too -- so the page it opens has them in
                // hand, or in flight, by the time it mounts.
                <button className="world-card-main"
                        {...intentProps({ kind: "world", id: w.id })}
                        onClick={() => void navigate(sectionHref({ kind: "world", id: w.id },
                                                            { kind: "section", at: "overview" }))}>
                  <div className="shelf-cover">
                    {w.cover && !brokenCover[`${w.id}:${w.cover}`] ? (
                      // w=208 for a box index.css sizes at 104px wide: 2x of
                      // headroom, so the cover is sharp on a 2x display rather
                      // than upscaled. More than that only costs bytes.
                      <img src={api.worldCoverUrl(w.id, { w: 208, v: w.cover })}
                           alt={`${w.name} cover`}
                           onError={() => setBrokenCover(
                             (b) => ({ ...b, [`${w.id}:${w.cover}`]: true }))} />
                    ) : (
                      <span className="cover-empty" aria-hidden>◆</span>
                    )}
                  </div>
                  <h3>{w.name}</h3>
                  {w.genre && <div className="world-genre">{w.genre}</div>}
                  <footer>{footerLabel(w.counts)}</footer>
                </button>
              )}
              {renaming?.id !== w.id && <div className="row-actions">
                <a aria-label={`Export ${w.name}`} title="Export as a bundle"
                   href={api.exportWorldUrl(w.id)} download onClick={(event) => {
                     event.preventDefault();
                     startExport(w);
                   }}>⭳</a>
                {/* Rename, fork and delete wait out a frame painted from
                    memory: it can show a world that is gone, or an id that
                    names another world now. Export and the card itself are
                    harmless on a stale frame and stay live. */}
                <button aria-label={`Rename ${w.name}`} disabled={stale}
                        onClick={() => setRenaming({ id: w.id, name: w.name })}>✎</button>
                <button aria-label={`Fork ${w.name}`} title="Fork into a new world"
                        disabled={stale || forking !== null} onClick={() => setForkDraft({ world: w, name: `${w.name} (fork)` })}>
                  {forking === w.id ? "…" : "⑂"}
                </button>
                {/* Disabled while any fork is in flight, not just this card's:
                    the copy walks the SOURCE for as long as it runs, and
                    deleting that world mid-walk fails the fork and leaves
                    neither the copy nor the original (Codex review). */}
                <button aria-label={`Delete ${w.name}`} disabled={stale || forking !== null}
                        onClick={() => remove(w)}>✕</button>
              </div>}
            </div>
          ))}
        </div>
      </div>
    </LibraryPage>
  );
}
