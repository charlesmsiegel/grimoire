import type { ActiveConnection, InferenceSummary, ProviderHealth } from "./inference";

export type Config = {
  theme: string; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string;
  active_connection_id: string;
  active_connection: ActiveConnection | null;
  ready: boolean;
  inference: InferenceSummary;
  /** What the active connection's provider last did (#146), or null when there
   *  is no active connection. Read from the server's registry — no network
   *  call to the provider happens on a config read. */
  health: ProviderHealth | null;
  /** Seconds of silence before an LLM call is abandoned; "0" disables. */
  llm_timeout: string;
  /** Seconds one absorb's whole LLM sequence may take; "0" disables. */
  absorb_budget: string;
  /** Seconds one non-streaming LLM call may take in total; "0" disables. */
  llm_call_budget: string;
  /** Re-attempts a transiently-failed generation gets before its connection is
   *  given up on (#144); "0" is the old one-attempt behaviour. */
  llm_retries: string;
  context_budget: string;
  /** Longest anchor text the prompt and the drift judge see, in CODE
   *  POINTS. Server-owned so the editor's warning cannot drift from the
   *  backend's truncation; count it with [...text].length, not .length. */
  voice_anchor_cap?: number;
  /** Recent transcript messages every keyword scan reads — world info, chronicle
   *  recall, keyed mechanics rules and the semantic-recall query all share this
   *  window. "0" empties it; a scene opener's prompt and a director's note seed
   *  activation themselves either way. */
  context_scan_depth: string;
  /** Levels of world-info recursion a turn runs: entries pulled in by the text
   *  of entries already activated. "0" (the default) is off; at most "3". The
   *  server reports the effective value, so a hand-edited "9" reads as "3". */
  lore_recursion_depth: string;
  archive_depth: string;
  /** "on" once the setup wizard has been finished or dismissed (#194). */
  setup_done: string;
  /** Server's verdict on whether to show the setup wizard: the flag is unset
   *  AND the store holds no worlds and no campaigns. Derived rather than left
   *  to the client so a boot needs one request, not three. */
  first_run: boolean;
  /** The store this config describes, so a client can tell that a decision it
   *  made about `first_run` belongs to a library it is no longer looking at. */
  data_dir: string;
  prompt_log_depth: string;
  /** Posts between live rolling-summary refreshes; "0" turns the automatic
   *  refresh off, leaving only the inspector's own Refresh button. */
  rolling_summary_every: string;
  /** Posts between scene-break questions; "0" turns the whole feature off,
   *  panel included. Only the cadence — the heuristic still has to agree
   *  before anything reaches a provider, so the real cost is well under one
   *  call per this many posts. */
  scene_break_every: string;
  /** Characters the off-scene cast's "known to exist" tier may name; "0" = no
   *  ceiling. Over it, the ones the in-scene cast mentions are kept first. */
  offscene_known_limit: string;
  /** Entries a similarity pass may add on top of the keyword ones; "0" = recall
   *  off. It turns off recall only, never the other embedders. */
  history_recall_depth: string;
  history_recall_budget: string;
  history_recall_threshold: string;
  history_recall_rerank: string;
  /** "auto" plans history recall when the first retrieval looks thin
   *  (roadmap 10); "off" (the default) never plans. */
  history_plan: string;
  /** "on" (the default) lets "auto" take one repair hop after a check. */
  history_plan_repair: string;
  semantic_recall_depth: string;
  /** Cosine floor a recalled entry must clear. */
  semantic_recall_threshold: string;
  /** "on" applies the stored prompt layout; "off" (the default) renders the
   *  catalog and LEAVES the layout on disk, so it can be A/B'd. */
  prompt_layout_enabled: string;
  /** "on" renders the active-speaker section in group scenes; "off" default. */
  speaker_turn_taking: string;
  /** "on" once automatic backups are enabled (#32); "off" on every install
   *  until someone turns them on. */
  backup_enabled: string;
  /** Hours between automatic backups. */
  backup_interval_hours: string;
  /** Archives kept by the retention sweep; "0" keeps every one. */
  backup_keep: string;
  /** Where archives are written; "" means `<data dir>/backups`. */
  backup_dir: string;
  /** Model turns a retcon replay may redo before the transcript offers to fork
   *  the campaign first (#80). A threshold, not a limit: "0" nudges every
   *  replay rather than none. */
  replay_fork_threshold: string;
  /** Days a clock advance may cross before the panel offers to checkpoint the
   *  campaign first (#107). The same kind of threshold as the one above, and
   *  the same "0" reading — every skip that crosses a day is asked about. The
   *  comparison itself is the server's (`AdvanceDigest.fork`); this is only
   *  where the number is set. */
  advance_fork_threshold: string;
  /** The quietest level `store/logs.py` writes down. The STORED setting, which
   *  is not necessarily what is in force: an unrecognized value is narrowed to
   *  the default on the server, and `GET /logs/level` reports the real one. */
  log_level: string;
  /** The GLOBAL scene-tracker switch, "on" | "off"; a campaign may override it
   *  (`getCampaignTracker`). */
  tracker: string;
  /** "on" adds the perception rider to the prompt; "off" keeps the tracker's
   *  record without that prompt cost. */
  perception_rider: string;
  /** "on" sends post images to a model that can read them (#377). */
  send_images: string;
  /** How many of the newest images in the sent history go out. */
  send_images_limit: string;
  /** Whether images would reach the connection the global chat route uses:
   *  "off" (setting off), "yes", "no" (text only) or "unknown" (the catalog
   *  does not say). Read-only. */
  send_images_reach?: SendImagesReach;
  /** The play decisions (roadmap 02 §3.6), each global with a campaign
   *  override (`getCampaignPlayDecisions`): the speaker draw "on" | "off",
   *  the turn decision "off" | "intent" | "plan", the decide tool "on" | "off". */
  decide_speaker_sampling: string;
  decide_turn_plan: string;
  decide_play_tool: string;
  /** Roadmap 12's investigation switches, "on" | "off", each off by default,
   *  and its spend ceiling in US dollars ("" for none). */
  investigation_continuity: string;
  investigation_question: string;
  investigation_spend_ceiling: string;
};
export type SendImagesReach = "off" | "yes" | "no" | "unknown" | "none";
/**
 * The subset of Config the Configuration page writes — the mirror of the
 * backend's `ConfigUpdate`. Named rather than inlined at each call site: it
 * was spelled out three times, and the copies drifted the moment a field was
 * added.
 */
export type ConfigUpdate = Partial<Pick<Config,
  "theme" | "system_prompt" | "quote_color" | "user_label" | "assistant_label" |
  "llm_timeout" | "absorb_budget" | "llm_call_budget" |
  "llm_retries" |
  "context_budget" | "context_scan_depth" | "lore_recursion_depth" | "archive_depth" |
  "setup_done" | "prompt_log_depth" |
  "rolling_summary_every" |
  "scene_break_every" |
  "offscene_known_limit" |
  "semantic_recall_depth" | "semantic_recall_threshold" |
  "history_recall_depth" | "history_recall_budget" | "history_recall_threshold" | "history_recall_rerank" |
  "history_plan" | "history_plan_repair" |
  "prompt_layout_enabled" | "speaker_turn_taking" |
  "backup_enabled" | "backup_interval_hours" | "backup_keep" | "backup_dir" |
  "replay_fork_threshold" | "advance_fork_threshold" | "log_level" |
  "tracker" | "perception_rider" | "send_images" | "send_images_limit" |
  "decide_speaker_sampling" | "decide_turn_plan" | "decide_play_tool" |
  "investigation_continuity" | "investigation_question" | "investigation_spend_ceiling">>;
/** One archive written by `store/backups.py`. */
export type BackupEntry = {
  name: string;
  /** Older servers omit scope for full archives. */
  scope?: "full" | "settings";
  /** Bytes on disk. */
  size: number;
  /** When it was taken, read from its own filename. */
  created: string;
};
/** The archives, and the directory they live in — which is a setting, so the
 *  answer to "why is this list empty" is often "you moved it". */
export type BackupList = { dir: string; backups: BackupEntry[]; image_backups: BackupEntry[] };
/** A `POST /api/backups`: the refreshed listing, plus what that call did.
 *  `retention_error` is set when the archive was written but the sweep that
 *  follows it could not run — a success with a problem attached, which is a
 *  third outcome the two-state ok/failed shape could not say. */
export type BackupRun = BackupList & {
  created: string;
  swept: string[];
  retention_error: string | null;
};
export type ImageBackupRun =
  | (BackupList & { created: string; listing_error: null })
  | { dir: string; created: string; listing_error: string };
export type DataDirInfo = {
  data_dir: string;
  default: string;
  is_default: boolean;
  source: "env" | "custom" | "default";
  exists: boolean;
};
/** What an image-store migration reports, planned or done (stage 4, spec §11).
 *  A dry run and a real run share this shape: `dry_run` says which, and the
 *  "what it did" counters are zero in a dry run. `outcome` is how the pass
 *  ended; `state` is only present on the minimal report a pass that raised
 *  leaves behind. */
export type ImageMigrationReport = {
  kind: "image-migration";
  run_id?: string;
  mode?: "plan" | "migrate";
  dry_run: boolean;
  outcome: "done" | "cancelled" | "failed" | "root-changed";
  state?: string;
  error?: string | null;
  stopped_at?: string | null;
  legacy_files: number;
  unique_images: number;
  exact_duplicates: number;
  pixel_variants: number;
  bytes_before: number;
  bytes_after: number;
  /** The plan's figure on a dry run; on a real run, what it actually freed. */
  bytes_reclaimed: number;
  description_conflicts: number;
  placed: number;
  already_placed: number;
  legacy_deleted: number;
  /** Files left where they were, and why (`path` is store-relative). */
  untouched: { path: string; reason: string }[];
  skipped: { reason?: string; path?: string }[];
  errors: { reason?: string; path?: string; error?: string }[];
  format1_collections: unknown[];
  collections_converted: unknown[];
  collections_kept: unknown[];
};
/** One image the collector would delete: `blob` is the blob key, `bytes` what
 *  deleting it frees (the blob only counts when every holder goes too). */
export type ImageGcRow = { id: string | null; blob: string; bytes: number };
/** An unreachable image that is not collectable yet. `collectable_at` is epoch
 *  seconds, `null` when no date can be promised (unreadable sidecars). */
export type ImageGcProtected = {
  id: string | null; blob: string | null; why: string; collectable_at: number | null;
};
export type ImageGcBlocker = { path: string; reason: string };
/** One file the collector removed: the object it belonged to, its blob key, its
 *  size. `id` is null for an orphan blob, which no sidecar named. */
export interface ImageGcDeletedRow { id: string | null; blob: string; bytes: number }

/** What the collector reports. `mode` `scan` is the dry run; `collect` the
 *  deletion. `token` is single-use and only present when something is
 *  collectable and nothing blocked the walk. Times are epoch seconds. */
export type ImageGcReport = {
  kind: "image-gc";
  mode: "scan" | "collect";
  run_id: string;
  state: "complete" | "blocked" | "cancelled" | "refused" | "root-changed" | "failed"
    | "trash-unavailable" | "recycle-bin-unverified" | "running";
  /** The unused-for window, in hours. A report written before the recycle bin
   *  carries `grace_days` instead; both are optional so either reads. */
  grace_hours?: number | null;
  grace_days?: number | null;
  counts: {
    placements: number; objects: number; blobs: number; unreferenced: number;
    collectable: number; collectable_blobs: number; protected: number;
  };
  collectable: ImageGcRow[];
  collectable_blobs: ImageGcRow[];
  protected: ImageGcProtected[];
  reclaimable_bytes: number;
  blocking: ImageGcBlocker[];
  /** Staging trees still count as image roots, including old leftovers (#479). */
  staging?: { path: string; reason: string }[];
  unreadable_sidecars: string[];
  clock_skew: { id: string | null; blob: string | null; what: string }[];
  token: string | null;
  token_expires_at: number | null;
  /** One row per file removed, appended as each unlink lands; `bytes` is the total. */
  deleted: { objects: ImageGcDeletedRow[]; blobs?: ImageGcDeletedRow[]; bytes: number };
  /** Each file the recycle bin took (`id`/`blob` null for one drained from the
   *  outbox). A file Windows did not confirm is here AND in `unverified`. */
  recycled?: { id: string | null; blob: string | null; name: string; bytes: number }[];
  /** Objects whose sidecar went but whose picture stayed or was already gone. */
  released?: { id: string; blob: string; reason: string }[];
  /** Names of files waiting in this device's outbox. */
  outbox?: string[];
  /** Files waiting in another device's outbox, never touched from here. */
  foreign_outboxes?: number;
  /** Names the bin took without Windows confirming it. */
  unverified?: string[];
  /** Whether a real recycle bin is here. `ok` is `null` when the pass ended
   *  before it knew (a failed report): unknown, not "no bin". */
  recycle_bin?: { ok: boolean | null; code: string | null; message: string | null };
  skipped: { id: string | null; blob: string | null; reason: string }[];
  /** Gone from reports written by this build; older stored ones still carry it. */
  kept_blobs?: { id: string; blob: string; reason: string }[];
  /** Reachable ids with no readable sidecar yet: orphan blobs are kept. */
  unarrived_objects: string[];
  /** Thumbnails or index entries a collection could not remove (best effort). */
  cache_cleanup: { path: string; reason: string }[];
  error: string | null;
};
export type ImageMaintenanceReport = ImageMigrationReport | ImageGcReport;
/** A file a sync client left behind when two devices wrote the same record
 *  (#35). `path` is relative to the store root, slash-separated. */
export type StoreConflict = {
  path: string; name: string; tool: string;
  kind: "file" | "directory";
  /** null for a directory — a conflicted folder is reported, not measured. */
  size: number | null;
  modified: string;
};
export type StoreConflicts = { conflicts: StoreConflict[]; truncated: boolean };
