import type { RollProposalPayload } from "../stream";

// dice rolls
export type DieDetail = { value: number; rolls: number[]; kept: boolean };
export type RollResult = {
  notation: string; seed: number; dice: DieDetail[]; modifier: number;
  pool_target: number | null; vs: number | null;
  total: number | null; successes: number | null; outcome: string | null;
};
export type RollEntry = {
  id: string; ts: string; scene: string | null; label: string | null; result: RollResult;
};
export type ProposalRecord = { id: string; status: string; payload: RollProposalPayload; resolution: CheckResolution | null };
export type CheckResolution = { check: string; check_label: string; actor: string; actor_label: string; notation: string; tier: string | null; difficulty: number | null; modifier: number; roll_id?: string };
export type SceneCheckActor = { ref: string; label: string; sheet_type: string; checks: [string, string][] };

// campaign group state (#47)
export type GroupState = {
  goals: string; resources: string; focus: string;
  public_perception: string; secrets: string; updated?: string;
};

// modules
export type LayoutNode = {
  row?: LayoutNode[]; column?: LayoutNode[]; group?: string;
  fields?: string[]; derived?: string[]; title?: string; grid?: boolean;
};
export type ModuleTheme = {
  colors?: Partial<Record<"bg" | "ink" | "muted" | "accent" | "rule", string>>;
  fonts?: Partial<Record<"display" | "body", string>>;
  dots?: string; corners?: string;
};
export type DisplayError = {
  source: "layout" | "theme";
  // "*" = file-level failure that dropped every layout
  sheet_type: string | null;
  message: string;
};

export type ModuleSummary = {
  id: string; name: string; description: string;
  version: string; source: "builtin" | "user"; valid: boolean;
  display_ok?: boolean;
};
export type ModuleField = {
  key: string; label?: string; type: string;
  max?: number; min?: number; default?: number;
  ref_kind?: string;
};
export type ModuleSheetType = {
  label: string; kind: string; groups: string[];
  fields: ModuleField[]; derived?: Record<string, string>;
  creation?: { pools: Record<string, { budget: number | string; costs: Record<string, number> }> };
  advancement?: { pool: string; costs: Record<string, string> };
};
export type ModuleEditResult = {
  ok: boolean; errors: string[]; display_errors: DisplayError[];
  impact?: { sheet_types: string[]; sheets_migrated: number;
             sheets_newly_invalid: number; dangling_refs: number };
  sample?: Record<string, { fields: Record<string, unknown>;
                            derived: Record<string, number | boolean> }>;
  migration?: { migrated: number; skipped: string[] };
};
export type ModuleRenameKind =
  "group" | "field" | "derived" | "sheet_type" | "check" | "rule" | "content";

export type ModuleDetail = {
  id: string;
  source: "builtin" | "user";
  manifest: { id: string; name: string; description?: string; version?: string; dice?: string; notes?: string };
  sheets: { groups: Record<string, { label?: string; fields: ModuleField[]; derived?: Record<string, string> }>;
            sheet_types: Record<string, ModuleSheetType> };
  checks: Record<string, { label?: string; roll?: string; requires?: string[]; rules?: string[];
                           difficulty?: number; outcomes?: { label: string; when: string }[] }>;
  rules: { id: string; keys: string[]; always: boolean; on_roll: boolean; sheet_types: string[] }[];
  content: { kind: string; id: string; name: string; sheet_type: string | null }[];
  errors: string[];
  layout?: { sheet_types: Record<string, LayoutNode> };
  layout_source?: Record<string, unknown>;
  theme?: ModuleTheme;
  display_errors?: DisplayError[];
};
export type ModuleContentEntry = {
  // The backend keeps custom string-scalar frontmatter beside these fixed
  // fields (store/modules/content.py), so editing content must retain it.
  [key: string]: unknown;
  kind: string; id: string; name: string; body: string; keys: string;
  sheet_type: string | null; fields: Record<string, unknown>;
};
export type CampaignModule = {
  setting: string; resolved: string | null; source: "campaign" | "world" | null;
};

// sheets (Phase 3 mechanics)
export type Sheet = {
  sheet_type: string | null;
  fields: Record<string, unknown>;
  derived: Record<string, number | boolean>;
  errors: string[];
  gen: string | null;
};
export type SheetExpected = { sheet_type: string | null; fields: Record<string, unknown>; gen: string | null } | null;
export type SheetCoverage = Record<string, { total: number; sheeted: number; invalid: number }>;

/** One cast member on the sheets roster: `coverage` counts these, this names
 *  them. `sheet_type`/`errors`/`creation_pending` describe the stored sheet and
 *  are the empty answers when `sheeted` is false. */
export type SheetRosterRow = {
  id: string;
  name: string;
  sheeted: boolean;
  sheet_type: string | null;
  errors: string[];
  /** The module's creation pools this sheet has never been through — non-empty
   *  only while its values are still exactly the schema defaults, which is the
   *  state a bulk create leaves them in. Empty for a sheet anyone has worked
   *  on, and for a type with no creation step. */
  creation_pending: string[];
};

export type SheetRoster = Record<string, SheetRosterRow[]>;

/** What one bulk create did. Every cast member it looked at is in `created`,
 *  `failed`, or was already sheeted; every kind it could not choose a type for
 *  is in `skipped` with the reason. */
export type SheetBulkResult = {
  created: { kind: string; id: string; name: string; sheet_type: string;
             creation_pending: string[] }[];
  skipped: { kind: string; reason: string }[];
  failed: { kind: string; id: string; detail: string }[];
};
