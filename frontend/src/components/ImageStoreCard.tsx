import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError, api, type ImageGcReport, type ImageMaintenanceReport,
  type ImageMigrationReport,
} from "../api/client";
import { newAttemptId, type RunHandle } from "../api/stream";
import { formatSize } from "./BackupsPanel";

/** How long between asks while a run is live. The request is local and tiny; a
 *  migration runs for minutes, so a second is plenty. */
const POLL_MS = 1000;
/** Failed polls in a row before the card stops waiting. A failed poll is not a
 *  failed run (a resuming tab, a dropped localhost fetch), so it is asked again;
 *  past this the run is left to be rediscovered by the next visit. */
const POLL_MISSES = 6;

type What = "check" | "migrate" | "find" | "delete" | "other";
const WORKING: Record<What, string> = {
  check: "Checking legacy images…",
  migrate: "Migrating legacy images…",
  find: "Looking for unused images…",
  delete: "Sending unused images to the recycle bin…",
  other: "An image-store run is in progress…",
};

/** A rediscovered run is described neutrally. The kind the server recorded says
 *  migrate or gc, but not whether it is the dry run or the real one -- a found
 *  `gc` may be a deletion, and a found `migrate` may be changing files -- so
 *  only a run this card started itself, which knows its mode, gets specific
 *  wording. */
function whatOf(_run: RunHandle): What {
  return "other";
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The clock time a too-new image becomes collectable. The window is an hour, so
 *  a date would only be noise. */
function clock(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString();
}

/** Where the outbox lives, under the store folder -- shown so a waiting file
 *  can be found by hand. */
const OUTBOX_DIR = ".cache/image-store/recycle/";

const NO_BIN = "This computer has no recycle bin Grimoire can use, so nothing is collected.";

/** Why a protected row with no date is kept, by the scan's `why`: the line after
 *  its count. */
const HELD: Record<string, string> = {
  "unarrived-objects": "kept while a picture is still arriving from another device.",
  "unreadable-sidecars": "kept while a picture's record can't be read.",
};

const isMigration = (r: ImageMaintenanceReport): r is ImageMigrationReport =>
  r.kind === "image-migration";

/** Settings → Storage → "Image store" (stage 4, M16): move the legacy per-record
 *  image files into the shared store, and clear out what nothing uses.
 *
 *  Both are detached `maintenance` runs. The card therefore owns a run, not a
 *  request: it starts one with an attempt id, follows it by polling, finds it
 *  again on mount by listing the global runs (a reload, or another tab, must not
 *  show an idle card over work that is changing files), and cancels it through
 *  the global run cancel. The report it shows is the one the server stored, read
 *  by run id, so it survives the run's own record being reaped.
 *
 *  Messages the server wrote -- a refusal, a blocking path, a refused token --
 *  are shown as written. They are the only account of why nothing happened. */
export function ImageStoreCard() {
  const [live, setLive] = useState<{ run: RunHandle; what: What } | null>(null);
  const [report, setReport] = useState<ImageMaintenanceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  // Which follow is current. Bumped to retire a follow loop -- on unmount, and
  // when something else has already seen the run end -- without an effect
  // dependency, so a settled run cannot restart its own poll.
  const seq = useRef(0);
  const mounted = useRef(true);
  // Whether a run is being followed, so the mount-time listing never takes over
  // from one the reader started while it was still answering.
  const active = useRef(false);

  const finish = useCallback(async (run: RunHandle, mine: number) => {
    if (mine !== seq.current || !mounted.current) return;
    seq.current += 1;
    active.current = false;
    setLive(null);
    setCancelling(false);
    try {
      const stored = await api.getImageMaintenanceReport(run.id);
      if (mounted.current) setReport(stored);
    } catch (err) {
      if (!mounted.current) return;
      const fallback = run.result as ImageMaintenanceReport | null | undefined;
      if (fallback && typeof fallback === "object" && "kind" in fallback) {
        setReport(fallback);
      } else {
        setError(run.error?.detail
          ?? (err instanceof ApiError ? err.detail : "The run left no report"));
      }
    }
  }, []);

  const follow = useCallback(async (start: RunHandle, what: What) => {
    const mine = ++seq.current;
    active.current = true;
    setLive({ run: start, what });
    let run = start;
    let missed = 0;
    for (let asked = 0; run.state === "running"; asked++) {
      if (asked > 0) await new Promise((r) => setTimeout(r, POLL_MS));
      if (mine !== seq.current || !mounted.current) return;
      try {
        run = (await api.getGlobalRun(run.id)).run;
        missed = 0;
      } catch (err) {
        // The run is gone from the registry: its report is the answer.
        if (err instanceof ApiError && err.status === 404) break;
        if (++missed > POLL_MISSES) {
          if (mine === seq.current && mounted.current) {
            seq.current += 1;
            active.current = false;
            setLive(null);
            setError("Lost contact with the server. The run may still be going; "
                     + "reopen this page to pick it up again.");
          }
          return;
        }
      }
    }
    await finish(run, mine);
  }, [finish]);

  useEffect(() => {
    mounted.current = true;
    api.listGlobalRuns().then(({ runs }) => {
      const found = runs.find((r) => r.cls === "maintenance" && r.state === "running");
      if (found && mounted.current && !active.current) void follow(found, whatOf(found));
    }, () => { /* no listing is no run to show; starting one reports for itself */ });
    return () => { mounted.current = false; active.current = false; seq.current += 1; };
  }, [follow]);

  async function begin(what: What, start: (attempt: string) => Promise<{ run: RunHandle }>) {
    setError(null);
    try {
      const { run } = await start(newAttemptId());
      // Only now: a start the server refused (busy, elsewhere) leaves the last
      // report on screen, and with it a scan's unspent token.
      setReport(null);
      await follow(run, what);
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof ApiError ? err.detail : "Could not start the run");
      if (err instanceof ApiError && err.kind === "run_in_flight") {
        // Somebody (another tab) already started one: show it rather than only
        // saying so.
        const { runs } = await api.listGlobalRuns().catch(() => ({ runs: [] as RunHandle[] }));
        const found = runs.find((r) => r.cls === "maintenance" && r.state === "running");
        if (found && mounted.current) {
          setError(null);
          void follow(found, whatOf(found));
        }
      }
    }
  }

  function check() {
    void begin("check", (a) => api.startImageMigration(true, a));
  }
  function migrate() {
    if (!window.confirm(
      "Migrate legacy images into the shared image store?\n\n"
      + "Each legacy image file is copied into the store, read back and checked, "
      + "and only then removed from its record's folder. Nothing is removed that "
      + "could not be read back. Run Check first to see what it would do.")) return;
    void begin("migrate", (a) => api.startImageMigration(false, a));
  }
  function find() {
    void begin("find", (a) => api.startImageGc({ dry_run: true }, a));
  }
  function remove(token: string, n: number) {
    if (!window.confirm(
      `Send ${plural(n, "unused image", "unused images")} to the recycle bin?\n\n`
      + "These are images no world, campaign or collection refers to, found by "
      + "the last scan. The pictures go to this computer's recycle bin, and can be "
      + "restored from there until it is emptied. Their descriptions and tags are "
      + "not kept. A picture another device linked but has not synced yet would "
      + "be binned too.")) return;
    void begin("delete", (a) => api.startImageGc({ dry_run: false, token }, a));
  }

  async function cancel() {
    if (!live || cancelling) return;
    setCancelling(true);
    const { run } = live;
    try {
      const { run: after } = await api.cancelGlobalRun(run.id);
      // The cancel route waits for the pass to stop; its answer is the ending.
      if (after.state !== "running") await finish(after, seq.current);
    } catch (err) {
      if (mounted.current) {
        setCancelling(false);
        setError(err instanceof ApiError ? err.detail : "Could not cancel the run");
      }
    }
  }

  const busy = live !== null;
  const offer = report && report.kind === "image-gc" ? deletable(report) : null;

  return (
    <section className="image-store-card" aria-label="Image store">
      <div className="section-label">Image store</div>
      <p className="config-copy">
        Images live in one shared store, kept once however many places use them.
        Libraries made before that keep a copy beside each record: <strong>Check</strong>{" "}
        shows what moving them would do and changes nothing; <strong>Migrate</strong>{" "}
        does it. Deleting a record leaves its image in the store, so{" "}
        <strong>Find unused images</strong> looks for what nothing refers to any more.
        A scan only reports: an image that is unused and at least an hour old can
        then be sent to this computer&apos;s recycle bin, where it can be restored from
        until the bin is emptied.
      </p>
      <div className="image-store-actions">
        <button className="btn-outline" onClick={check} disabled={busy}>Check</button>
        <button className="btn-accent" onClick={migrate} disabled={busy}>Migrate</button>
        <button className="btn-outline" onClick={find} disabled={busy}>
          Find unused images
        </button>
        {offer && (
          <button className="btn-outline" onClick={() => remove(offer.token, offer.count)}
                  disabled={busy}>
            {`Send ${plural(offer.count, "unused image", "unused images")} to the recycle bin`}
          </button>
        )}
      </div>
      {live && (
        <p className="field-hint" role="status">
          {WORKING[live.what]}{" "}
          <button className="link" onClick={() => { void cancel(); }} disabled={cancelling}>
            Cancel
          </button>
        </p>
      )}
      {error && <p className="config-msg err" role="alert">{error}</p>}
      {report && (isMigration(report)
        ? <MigrationReport report={report} />
        : <GcReport report={report} />)}
    </section>
  );
}

/** The token a scan issued, and how many images it covers (what is collectable
 *  plus what already waits in the outbox) -- only while it can still be used
 *  and a recycle bin is there. Spent, expired or absent, there is nothing to offer.
 *  A blocked scan offers nothing collectable, but its token still drains the
 *  outbox, so its waiting files are offered all the same. */
function deletable(r: ImageGcReport): { token: string; count: number } | null {
  if (r.mode !== "scan" || !r.token) return null;
  if (r.state !== "complete" && r.state !== "blocked") return null;
  if (r.token_expires_at !== null && r.token_expires_at * 1000 <= Date.now()) return null;
  const p: Partial<ImageGcReport> = r;
  // Only a bin known to be missing hides the offer: `null` is "not asked", and
  // the server refuses a collection that has no bin whatever the card shows.
  if (p.recycle_bin?.ok === false) return null;
  const count = (p.collectable ?? []).length + (p.collectable_blobs ?? []).length
    + (p.outbox ?? []).length;
  return count > 0 ? { token: r.token, count } : null;
}

/** A long list is a wall nobody reads, and a store with many legacy files can
 *  leave hundreds untouched for one reason. The count says how many more. */
const ROW_CAP = 50;

function Rows({ rows, label }: { rows: { path: string; reason: string }[]; label: string }) {
  if (rows.length === 0) return null;
  return (
    <ul className="backup-list" aria-label={label}>
      {rows.slice(0, ROW_CAP).map((r) => (
        <li key={`${r.path}:${r.reason}`} className="backup-row">
          <span className="backup-name">{r.path}</span>
          <span className="backup-meta">{r.reason}</span>
        </li>
      ))}
      {rows.length > ROW_CAP && (
        <li className="backup-row">
          <span className="backup-meta">and {rows.length - ROW_CAP} more</span>
        </li>
      )}
    </ul>
  );
}

/** A stored report is read as possibly partial: a pass that raised before
 *  writing its own leaves a minimal one, and one written by an older build may
 *  lack a list or a count. A missing list is empty and a missing count zero,
 *  rather than a card that throws. */
function MigrationReport({ report }: { report: ImageMigrationReport }) {
  const r: Partial<ImageMigrationReport> = report;
  const planned = report.dry_run;
  const stopped = report.outcome !== "done";
  const exact = r.exact_duplicates ?? 0;
  const conflicts = r.description_conflicts ?? 0;
  const errors = r.errors ?? [];
  return (
    <div className="image-store-report">
      {stopped && (
        <p className="config-msg err">
          {report.outcome === "cancelled"
            ? "Cancelled. What was done before it stopped is below."
            : `Stopped (${report.outcome}${report.stopped_at ? ` at ${report.stopped_at}` : ""}).`}
          {report.error ? ` ${report.error}` : ""}
        </p>
      )}
      <p className="field-hint">
        {plural(r.legacy_files ?? 0, "legacy file", "legacy files")} found, holding{" "}
        {plural(r.unique_images ?? 0, "unique image", "unique images")}
        {exact > 0
          ? ` (${plural(exact, "exact duplicate", "exact duplicates")})` : ""}.
        {planned
          ? ` Migrating would free ${formatSize(r.bytes_reclaimed ?? 0)}.`
          : ` ${plural(r.placed ?? 0, "image", "images")} placed and `
            + `${plural(r.legacy_deleted ?? 0, "legacy file", "legacy files")} removed, `
            + `freeing ${formatSize(r.bytes_reclaimed ?? 0)}.`}
        {conflicts > 0
          ? ` ${plural(conflicts, "image has", "images have")} `
            + "descriptions that disagree; both are kept for you to choose." : ""}
      </p>
      <Rows rows={r.untouched ?? []} label="Left alone" />
      {errors.length > 0 && (
        <p className="config-msg err">
          {plural(errors.length, "step failed", "steps failed")} and was left as it was:{" "}
          {errors.map((e) => [e.path, e.reason ?? e.error].filter(Boolean).join(" — ")).join("; ")}
        </p>
      )}
    </div>
  );
}

/** Read as possibly partial, for `MigrationReport`'s reason. */
function GcReport({ report }: { report: ImageGcReport }) {
  if (report.mode === "collect") return <CollectReport report={report} />;
  const r: Partial<ImageGcReport> = report;
  const now = (r.counts?.collectable ?? 0) + (r.counts?.collectable_blobs ?? 0);
  const skew = r.clock_skew ?? [];
  const unreadable = r.unreadable_sidecars ?? [];
  const waiting = (r.outbox ?? []).length;
  // One line for every too-new image, naming when the last of them is due: each
  // upload has its own due second, so a line per time is a wall after a burst.
  // An undated row is held for a reason, and each reason has its own line; a
  // clock-skew row is the timestamp line's.
  let dated = 0;
  let latest = 0;
  const held: Record<string, number> = {};
  for (const row of r.protected ?? []) {
    if (row.collectable_at !== null) {
      dated += 1;
      latest = Math.max(latest, row.collectable_at);
    } else if (row.why !== "clock-skew") {
      held[row.why] = (held[row.why] ?? 0) + 1;
    }
  }
  const undated = Object.entries(held).filter(([why]) => !(why in HELD)).reduce(
    (sum, [, n]) => sum + n, 0);
  return (
    <div className="image-store-report">
      <Stopped report={report} />
      {report.state === "complete" && r.recycle_bin?.ok === false && (
        <p className="config-msg err">{r.recycle_bin.message ?? NO_BIN}</p>
      )}
      {report.state === "complete" && (
        <p className="field-hint">
          {now > 0
            ? `${plural(now, "unused image", "unused images")} can be sent to the `
              + `recycle bin now, freeing ${formatSize(r.reclaimable_bytes ?? 0)} once it is emptied. `
            // With files waiting, the button sends those: the Waiting paragraph
            // says so, and "nothing can be sent" would contradict it.
            : waiting > 0 ? "" : "No unused images can be sent to the recycle bin yet. "}
          {plural(r.counts?.objects ?? 0, "image", "images")} in the store,{" "}
          {plural(r.counts?.unreferenced ?? 0, "image is", "images are")} not used by anything.
        </p>
      )}
      {dated > 0 && (
        <p className="field-hint">
          {dated} more {dated === 1 ? "becomes" : "become"} collectable by{" "}
          {clock(latest)}, if still unused.
        </p>
      )}
      {Object.entries(HELD).map(([why, line]) => (held[why] ?? 0) > 0 && (
        <p key={why} className="field-hint">{held[why]} {line}</p>
      ))}
      {undated > 0 && (
        <p className="field-hint">
          {plural(undated, "image", "images")} cannot be dated yet.
        </p>
      )}
      {skew.length > 0 && (
        <p className="config-msg err">
          {plural(skew.length, "image has", "images have")} a timestamp in the
          future and will not be collected until the clock catches up.
        </p>
      )}
      {unreadable.length > 0 && (
        <Rows label="Unreadable records"
              rows={unreadable.map((path) => ({ path, reason: "unreadable" }))} />
      )}
      <Waiting report={report} />
    </div>
  );
}

/** What already sits in the outbox, waiting to go to the bin, and the files in
 *  other devices' outboxes, which this device never touches. */
function Waiting({ report }: { report: ImageGcReport }) {
  const waiting = report.outbox ?? [];
  const foreign = report.foreign_outboxes ?? 0;
  if (waiting.length === 0 && foreign === 0) return null;
  return (
    <>
      {waiting.length > 0 && (
        <>
          <p className="field-hint">
            {plural(waiting.length, "image is", "images are")} waiting to be sent to the
            recycle bin, in <code>{OUTBOX_DIR}</code> under the store folder. The next
            collection sends {waiting.length === 1 ? "it" : "them"} first.
          </p>
          <Rows label="Waiting to be sent"
                rows={waiting.map((path) => ({ path, reason: "waiting" }))} />
        </>
      )}
      {foreign > 0 && (
        <p className="field-hint">
          {foreign === 1 ? "1 file is" : `${foreign} files are`} waiting in another
          folder under <code>{OUTBOX_DIR}</code>, from another device, or from this one
          under an earlier store location. This collection leaves{" "}
          {foreign === 1 ? "it" : "them"} alone; {foreign === 1 ? "it" : "they"} can be
          emptied by hand from <code>{OUTBOX_DIR}</code>.
        </p>
      )}
    </>
  );
}

/** Why a scan or a collection did not do its work, as the server wrote it. */
function Stopped({ report }: { report: ImageGcReport }) {
  if (report.state === "complete") return null;
  // Said by `CollectReport`, beside the files it names.
  if (report.state === "recycle-bin-unverified") return null;
  const p: Partial<ImageGcReport> = report;
  if (report.state === "trash-unavailable") {
    const n = (p.outbox ?? []).length;
    return (
      <p className="config-msg err">
        The recycle bin refused a file; {n} {n === 1 ? "is" : "are"} waiting to be sent.
        Nothing more was collected.
      </p>
    );
  }
  // A report with no `recycled` was written by a build that deleted, and says
  // so in its words. Otherwise nothing is deleted any more: a stop is about
  // what was not sent to the bin, and "more" once something already was.
  const legacy = p.recycled === undefined;
  const none = legacy ? "Nothing was deleted"
    : (p.recycled ?? []).length > 0 ? "Nothing more was sent to the recycle bin"
    : "Nothing was sent to the recycle bin";
  const noneMore = legacy ? "Nothing more was deleted" : "Nothing more was sent to the recycle bin";
  const lead: Record<string, string> = {
    blocked: `${none}, because these could not be read safely:`,
    refused: `${none}.`,
    cancelled: "Cancelled. What was done before it stopped is below.",
    "root-changed": `The storage location changed during the run. ${noneMore}.`,
    failed: "The run failed.",
  };
  // A collection refused for want of a bin carries a code in `error`; the
  // sentence is `recycle_bin.message`, and the code is never shown.
  if (report.error === "no_recycle_bin") {
    return (
      <p className="config-msg err">{p.recycle_bin?.message ?? NO_BIN}</p>
    );
  }
  return (
    <>
      <p className="config-msg err">
        {lead[report.state] ?? `Stopped (${report.state}).`}
        {report.error ? ` ${report.error}` : ""}
      </p>
      <Rows rows={p.blocking ?? []} label="Blocking paths" />
    </>
  );
}

/** What a legacy collection deleted, counted the way the scan counted it: each
 *  object, plus each orphan blob (a row with no object, or none deleted here). A
 *  blob that went with an object it belonged to is that object, not a second
 *  image. Only reports written before the recycle bin have `deleted.blobs`. */
function deletedCount(deleted: Partial<ImageGcReport["deleted"]> | undefined): number {
  const objects = deleted?.objects ?? [];
  const ids = new Set(objects.map((o) => o.id));
  const orphans = (deleted?.blobs ?? []).filter((b) => b.id === null || !ids.has(b.id));
  return objects.length + orphans.length;
}

function CollectReport({ report }: { report: ImageGcReport }) {
  const r: Partial<ImageGcReport> = report;
  const skipped = r.skipped ?? [];
  const left = skipped.length > 0
    ? ` ${plural(skipped.length, "image was", "images were")} left `
      + `(${[...new Set(skipped.map((s) => s.reason))].join(", ")}).` : "";
  // A report written before the recycle bin has no `recycled`: it deleted, and
  // says so in the words of the build that did.
  if (r.recycled === undefined) {
    const n = deletedCount(r.deleted);
    return (
      <div className="image-store-report">
        <Stopped report={report} />
        {(report.state === "complete" || n > 0) && (
          <p className="field-hint">
            Deleted {plural(n, "unused image", "unused images")}, freeing{" "}
            {formatSize(r.deleted?.bytes ?? 0)}.{left}
          </p>
        )}
      </div>
    );
  }
  // A file Windows did not confirm is in `recycled` too (the bin was handed it),
  // but it is not known to be in the bin, so it is never counted as sent.
  const unverified = r.unverified ?? [];
  const unconfirmed = new Set(unverified);
  const sent = r.recycled.filter((f) => !unconfirmed.has(f.name));
  const bytes = sent.reduce((sum, f) => sum + f.bytes, 0);
  return (
    <div className="image-store-report">
      <Stopped report={report} />
      {(report.state === "complete" || sent.length > 0) && (
        <p className="field-hint">
          Sent {plural(sent.length, "image", "images")} to the recycle bin
          ({formatSize(bytes)}). The space is freed when you empty the bin.
          {sent.length > 0 ? " Their names start with grimoire - ." : ""}{left}
        </p>
      )}
      {unverified.length > 0 && (
        <>
          <p className="config-msg err">
            Windows did not confirm these went to the Recycle Bin. Check the bin before
            collecting again.
          </p>
          <Rows label="Not confirmed"
                rows={unverified.map((path) => ({ path, reason: "not confirmed" }))} />
        </>
      )}
      <Waiting report={report} />
    </div>
  );
}
