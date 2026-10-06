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
  delete: "Deleting unused images…",
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

function day(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString();
}

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
      `Delete ${plural(n, "unused image", "unused images")}?\n\n`
      + "These are images no world, campaign or collection refers to, found by "
      + "the last scan. This cannot be undone.")) return;
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
        It never deletes: a scan only reports, and an image has to be seen unused on
        two scans a grace period apart before it can be deleted.
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
            {`Delete ${plural(offer.count, "unused image", "unused images")}`}
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

/** The token a scan issued, and how many images it covers -- only while it can
 *  still be used. Spent, expired or absent, there is nothing to offer. */
function deletable(r: ImageGcReport): { token: string; count: number } | null {
  if (r.mode !== "scan" || r.state !== "complete" || !r.token) return null;
  if (r.token_expires_at !== null && r.token_expires_at * 1000 <= Date.now()) return null;
  const count = r.collectable.length + r.collectable_blobs.length;
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

function MigrationReport({ report }: { report: ImageMigrationReport }) {
  const planned = report.dry_run;
  const stopped = report.outcome !== "done";
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
        {plural(report.legacy_files, "legacy file", "legacy files")} found, holding{" "}
        {plural(report.unique_images, "unique image", "unique images")}
        {report.exact_duplicates > 0
          ? ` (${plural(report.exact_duplicates, "exact duplicate", "exact duplicates")})` : ""}.
        {planned
          ? ` Migrating would free ${formatSize(report.bytes_reclaimed)}.`
          : ` ${plural(report.placed, "image", "images")} placed and `
            + `${plural(report.legacy_deleted, "legacy file", "legacy files")} removed, `
            + `freeing ${formatSize(report.bytes_reclaimed)}.`}
        {report.description_conflicts > 0
          ? ` ${plural(report.description_conflicts, "image has", "images have")} `
            + "descriptions that disagree; both are kept for you to choose." : ""}
      </p>
      <Rows rows={report.untouched} label="Left alone" />
      {report.errors.length > 0 && (
        <p className="config-msg err">
          {plural(report.errors.length, "step failed", "steps failed")} and was left as it was:{" "}
          {report.errors.map((e) => [e.path, e.reason ?? e.error].filter(Boolean).join(" — ")).join("; ")}
        </p>
      )}
    </div>
  );
}

function GcReport({ report }: { report: ImageGcReport }) {
  if (report.mode === "collect") return <CollectReport report={report} />;
  const now = report.counts.collectable + report.counts.collectable_blobs;
  const dated = new Map<string, number>();
  let undated = 0;
  for (const row of report.protected) {
    if (row.collectable_at === null) undated += 1;
    else {
      const when = day(row.collectable_at);
      dated.set(when, (dated.get(when) ?? 0) + 1);
    }
  }
  return (
    <div className="image-store-report">
      <Stopped report={report} />
      {report.state === "complete" && (
        <p className="field-hint">
          {now > 0
            ? `${plural(now, "unused image", "unused images")} can be deleted now, `
              + `freeing ${formatSize(report.reclaimable_bytes)}.`
            : "No unused images can be deleted yet."}
          {" "}{plural(report.counts.objects, "image", "images")} in the store,{" "}
          {plural(report.counts.unreferenced, "image is", "images are")} not used by anything.
        </p>
      )}
      {[...dated.entries()].map(([when, n]) => (
        <p key={when} className="field-hint">
          {n} more {n === 1 ? "becomes" : "become"} collectable on {when}, if still unused
          on a scan then.
        </p>
      ))}
      {undated > 0 && (
        <p className="field-hint">
          {plural(undated, "image", "images")} cannot be dated: see the notes below.
        </p>
      )}
      {report.clock_skew.length > 0 && (
        <p className="config-msg err">
          {plural(report.clock_skew.length, "image has", "images have")} a timestamp in the
          future and will not be collected until the clock catches up.
        </p>
      )}
      {report.unreadable_sidecars.length > 0 && (
        <Rows label="Unreadable records"
              rows={report.unreadable_sidecars.map((path) => ({ path, reason: "unreadable" }))} />
      )}
    </div>
  );
}

/** Why a scan or a collection did not do its work, as the server wrote it. */
function Stopped({ report }: { report: ImageGcReport }) {
  if (report.state === "complete") return null;
  const lead: Record<string, string> = {
    blocked: "Nothing was deleted, because these could not be read safely:",
    refused: "Nothing was deleted.",
    cancelled: "Cancelled. What was done before it stopped is below.",
    "root-changed": "The storage location changed during the run. Nothing more was deleted.",
    failed: "The run failed.",
  };
  return (
    <>
      <p className="config-msg err">
        {lead[report.state] ?? `Stopped (${report.state}).`}
        {report.error ? ` ${report.error}` : ""}
      </p>
      <Rows rows={report.blocking} label="Blocking paths" />
    </>
  );
}

function CollectReport({ report }: { report: ImageGcReport }) {
  const n = report.deleted.objects.length;
  return (
    <div className="image-store-report">
      <Stopped report={report} />
      {(report.state === "complete" || n > 0) && (
        <p className="field-hint">
          Deleted {plural(n, "unused image", "unused images")}, freeing{" "}
          {formatSize(report.deleted.bytes)}.
          {report.skipped.length > 0
            ? ` ${plural(report.skipped.length, "image was", "images were")} left `
              + `(${[...new Set(report.skipped.map((s) => s.reason))].join(", ")}).` : ""}
        </p>
      )}
    </div>
  );
}
