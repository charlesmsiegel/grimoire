import { Link } from "react-router-dom";
import type { Config } from "../../api/client";
import type { Draft, DraftField, SectionId } from "../../configSections";
import { BackupsPanel } from "../../components/BackupsPanel";
import { ImageStoreCard } from "../../components/ImageStoreCard";
import { StorageLocation } from "../../components/StorageLocation";
import { StoreConflictNotice } from "../../components/StoreConflictNotice";
import { NumField } from "./NumField";
import { LOG_LEVELS } from "./settingsDraft";

export function InstallSections({ section, draft, config, edit, storeEpoch, onStoreMoved }: {
  section: SectionId;
  draft: Draft;
  config: Config | null;
  edit: (field: DraftField, value: string) => void;
  storeEpoch: number;
  onStoreMoved: () => void;
}) {
  return (
    <>
      {section === "storage" && (
        <>
          <StorageLocation onMoved={onStoreMoved} />
          <p className="field-hint">
            The one setting on this page that does not wait for Save: moving the
            library moves the file this form writes to, so a draft held back for
            it would land in whichever store the pointer named by the time you
            pressed Save.
          </p>
          {/* Remounted on a move: the notice describes one library, and the
              pointer now names a different one. */}
          <StoreConflictNotice key={storeEpoch} />
          {/* Remounted on a move too: a run the card is following belongs to
              the library it started in, and a move is refused while one is
              live, so what this shows after one is the new library's. */}
          <ImageStoreCard key={`image-store-${storeEpoch}`} />
        </>
      )}

      {section === "setup" && (
        <>
          <p className="config-copy">
            The four questions a new library is asked: where it lives, which
            model runs it, how it looks, and the first world to play in.
            Every step commits as it is answered, so Back is navigation
            rather than undo — walking it again does not put anything back.
          </p>
          <p className="config-copy">
            Worth re-running when you move to another machine, or point the
            storage location at a library that has never been set up here.
            Nothing in it is destructive: the storage step is the same
            control as <em>Storage</em> above, and the world step offers a
            new world rather than replacing the ones you have.
          </p>
          <div className="form-actions">
            {/* The explicit re-entry signal. `/welcome` is a redirect once
                setup is done, so a bare link would bounce straight back
                here -- see the route's own comment in `App.tsx`. */}
            <Link className="hub-primary" to="/welcome?again=1">
              Run first-run setup again →
            </Link>
          </div>
        </>
      )}

      {section === "backups" && (
        <>
          <p className="config-copy">
            A full backup is the whole library zipped into one file — worlds, campaigns,
            scenes, settings. Everything grimoire knows is plain files under the
            storage location, so an archive is a complete restore point and nothing
            else has to be running to use it. The rebuildable thumbnail cache and the
            backups folder itself are left out.
          </p>
          <p className="config-copy">
            Automatic backups happen while the app is running and nowhere else: it
            checks hourly whether the newest archive is older than the interval. They
            are <strong>off</strong> until you turn them on, because each archive is a
            second copy of your library and the count below multiplies it — and if your
            storage location is a synced folder, every archive is uploaded whole. Point
            the folder somewhere outside the library to avoid that.
          </p>
          <label className="checkbox-row">
            <input
              type="checkbox"
              aria-label="Back up automatically"
              checked={draft.backup_enabled === "on"}
              onChange={(e) => edit("backup_enabled", e.target.checked ? "on" : "off")}
            />
            Back up automatically
          </label>
          <div className="config-fields">
            <NumField id="cfg-backup-interval" label="Every" unit="hours"
                      placeholder="24" caption="checked against the newest archive"
                      value={draft.backup_interval_hours}
                      onChange={(v) => edit("backup_interval_hours", v)} />
            <NumField id="cfg-backup-keep" label="Keep" unit="archives"
                      placeholder="7" caption="0 = keep every one"
                      value={draft.backup_keep}
                      onChange={(v) => edit("backup_keep", v)} />
            <div className="config-field">
              <label htmlFor="cfg-backup-dir">Backup folder</label>
              <div className="config-input">
                <input id="cfg-backup-dir" type="text" className="mono-input"
                       value={draft.backup_dir}
                       placeholder={`${config?.data_dir ?? ""}/backups`}
                       onChange={(e) => edit("backup_dir", e.target.value)} />
              </div>
              <p className="config-caption">blank = inside the library</p>
            </div>
          </div>
          {/* The saved value, not the draft: the list below is the folder the
              server is actually writing to, and re-reading it against a field
              nobody has saved yet would show an empty directory as if it were
              the state of your backups. */}
          <BackupsPanel dir={config?.backup_dir ?? ""} />
        </>
      )}

      {section === "logging" && (
        <>
          <p className="config-copy">
            What the backend writes down about itself, in{" "}
            <code>{config?.data_dir ?? "your library"}/logs</code> — one JSON line per
            entry, a file per month, readable in any editor. This is the floor:
            anything quieter is never written at all, so turning it down is how the
            files stay small and turning it up is what you do before reproducing a
            bug. <strong>Errors are recorded whatever this says.</strong>
          </p>
          {/* The capture boundary is incoming bodies, not request logging.
              A provider can echo private content, so explain it where the
              reader chooses the logging level and decides what to share. */}
          <p className="config-copy">
            Requests and authorization headers are not captured. At Debug level,
            logs include complete incoming LLM response bodies, including reasoning
            text and provider metadata. They also name the campaign and scene a
            failure happened in, and occasionally a character. Read the log before
            sharing it; response bodies can contain private campaign content.
          </p>
          <div className="config-fields">
            <div className="config-field">
              <label htmlFor="cfg-log-level">Record</label>
              <div className="config-input">
                <select id="cfg-log-level" value={draft.log_level}
                        onChange={(e) => edit("log_level", e.target.value)}>
                  {LOG_LEVELS.map((l) => (
                    <option key={l} value={l}>{l} and above</option>
                  ))}
                </select>
              </div>
              <p className="config-caption">
                debug is verbose and off by default
              </p>
            </div>
          </div>
          <p className="config-copy">
            <Link to="/stats">Instrumentation</Link> is where these are read back —
            filtered, tailed live, and counted per module beside the latency figures.
          </p>
        </>
      )}

      {section === "timeouts" && (
        <>
          <p className="config-copy">
            How long a generation may go without sending anything before it is abandoned, and
            how long one end-of-scene absorb (extraction, dossiers, mechanics audit) may take
            in total — past the budget, the remaining dossier refreshes are skipped and the
            audit reports as failed, leaving the absorb itself intact. Set either to
            <code> 0</code> to remove the limit, e.g. for a slow local endpoint.
          </p>
          <p className="config-copy">
            The call ceiling bounds one whole one-shot generation — a tagline, a voice
            anchor, scene suggestions — because a reply that keeps trickling in never trips
            the no-reply timeout above. Scene prose is deliberately exempt (a long reply you
            are already reading must not be cut off mid-sentence), and so is absorb, which
            the budget beside it already covers. <code>0</code> removes it.
          </p>
          <p className="config-copy">
            A call that fails for a passing reason — a rate limit, a dropped
            connection — is re-sent up to the retry count, with a growing pause
            between tries. Only ever <em>before</em> the reply starts arriving: once
            text is on screen it is never re-requested, because a second attempt
            would repeat what you have already read. <code>0</code> retries sends
            once and reports the failure.
          </p>
          <p className="config-copy">
            If the model still cannot answer, the role's fallback — set on the{" "}
            <Link to="/models">Models</Link> page — gets one attempt, and it can be an entirely
            different provider.
          </p>
          <div className="config-fields">
            <NumField id="cfg-llm-timeout" label="No-reply timeout" unit="seconds"
                      placeholder="120" value={draft.llm_timeout}
                      onChange={(v) => edit("llm_timeout", v)} />
            <NumField id="cfg-absorb-budget" label="Absorb budget" unit="seconds"
                      placeholder="600" value={draft.absorb_budget}
                      onChange={(v) => edit("absorb_budget", v)} />
            <NumField id="cfg-llm-call-budget" label="One-shot call ceiling" unit="seconds"
                      placeholder="300" value={draft.llm_call_budget}
                      onChange={(v) => edit("llm_call_budget", v)} />
            <NumField id="cfg-llm-retries" label="Retries" placeholder="2"
                      caption="0 = send once, then report the failure"
                      value={draft.llm_retries}
                      onChange={(v) => edit("llm_retries", v)} />
          </div>
        </>
      )}

    </>
  );
}
