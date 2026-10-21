/** Game mechanics: group state, modules and their authoring, sheets, and the
 *  scene state tracker. Spread into `api` by `client.ts`. */
import { notifyShell } from "../notify";
import { entityBase } from "../paths";
import { postZip, request } from "../transport";
import type { CampaignModule, CampaignTrackerSetting, EntityScope, GroupState,
  ModuleContentEntry, ModuleDetail, ModuleEditResult, ModuleRenameKind, ModuleSummary, Sheet,
  SheetBulkResult, SheetCoverage, SheetExpected, SheetRoster, TrackerEdits, TrackerLayer,
  TrackerLayerBundle, TrackerRecord, TrackerScope, TrackerSetting, TrackerSummary } from "../types";

/** The tracker's three field-definition layers share one body and differ only in path. */
function trackerFieldsPath(scope: TrackerScope): string {
  switch (scope.kind) {
    case "world": return `/api/worlds/${scope.wid}/tracker-fields`;
    case "campaign": return `/api/campaigns/${scope.cid}/tracker-fields`;
    case "scene": return `/api/campaigns/${scope.cid}/scenes/${scope.sid}/tracker-fields`;
  }
}

export const mechanicsApi = {
  // campaign group state (#47)
  getGroupState: (cid: string, gid: string) =>
    request<GroupState>("GET", `/api/campaigns/${cid}/groups/${gid}/state`),
  putGroupState: (cid: string, gid: string, state: Omit<GroupState, "updated">) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/groups/${gid}/state`, state),

  // modules
  listModules: () => request<ModuleSummary[]>("GET", "/api/modules"),
  readModule: (mid: string) => request<ModuleDetail>("GET", `/api/modules/${mid}`),
  readModuleContent: (mid: string, kind: string, id: string) =>
    request<ModuleContentEntry>("GET", `/api/modules/${mid}/content/${kind}/${id}`),
  instantiateContent: (scope: EntityScope, kind: string, mid: string, contentId: string) =>
    request<{ id: string }>(
      "POST",
      `${entityBase(scope)}/${kind}/instantiate/${mid}/${contentId}`),
  getCampaignModule: (cid: string) =>
    request<CampaignModule>("GET", `/api/campaigns/${cid}/module`),
  setCampaignModule: (cid: string, module: string) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/module`, { module }),
  setWorldModule: (wid: string, module: string) =>
    request<{ ok: boolean }>("PUT", `/api/worlds/${wid}/module`, { module }),

  // module authoring (Phase 8)
  createModule: (name: string) => request<{ id: string }>("POST", "/api/modules", { name }),
  duplicateModule: (mid: string, name: string) =>
    request<{ id: string }>("POST", `/api/modules/${mid}/duplicate`, { name }),
  importModule: (file: Blob) => postZip<{ id: string }>("/api/modules/import", file),
  exportModuleUrl: (mid: string) => `/api/modules/${mid}/export`,
  putModuleManifest: (mid: string, body: { name: string; description: string;
    version: string; dice: string; notes: string; dry_run: boolean }) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/manifest`, body),
  putModuleGroup: (mid: string, gid: string, group: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/groups/${gid}`,
      { group, dry_run: dryRun }),
  deleteModuleGroup: (mid: string, gid: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/groups/${gid}${dryRun ? "?dry_run=1" : ""}`),
  putModuleSheetType: (mid: string, tid: string, sheetType: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/sheet-types/${tid}`,
      { sheet_type: sheetType, dry_run: dryRun }),
  deleteModuleSheetType: (mid: string, tid: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/sheet-types/${tid}${dryRun ? "?dry_run=1" : ""}`),
  putModuleCheck: (mid: string, id: string, check: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/checks/${id}`,
      { check, dry_run: dryRun }),
  deleteModuleCheck: (mid: string, id: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/checks/${id}${dryRun ? "?dry_run=1" : ""}`),
  putModuleCheckDefaults: (mid: string, defaults: Record<string, unknown>, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/check-defaults`,
      { defaults, dry_run: dryRun }),
  putModuleRule: (mid: string, slug: string, flags: unknown, body: string, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/rules/${slug}`,
      { flags, body, dry_run: dryRun }),
  deleteModuleRule: (mid: string, slug: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/rules/${slug}${dryRun ? "?dry_run=1" : ""}`),
  readModuleRule: (mid: string, slug: string) =>
    request<{ meta: Record<string, string>; body: string }>(
      "GET", `/api/modules/${mid}/rules/${slug}`),
  putModuleContent: (mid: string, kind: string, id: string, body: Record<string, unknown>, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/content/${kind}/${id}`,
      { ...body, dry_run: dryRun }),
  deleteModuleContent: (mid: string, kind: string, id: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/content/${kind}/${id}${dryRun ? "?dry_run=1" : ""}`),
  putModuleLayout: (mid: string, layout: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/layout`, { layout, dry_run: dryRun }),
  putModuleTheme: (mid: string, theme: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/theme`, { theme, dry_run: dryRun }),
  renameModulePart: (mid: string, kind: ModuleRenameKind, address: Record<string, string>,
                     to: string, dryRun = false) =>
    request<ModuleEditResult>("POST", `/api/modules/${mid}/rename`,
      { kind, address, to, dry_run: dryRun }),

  // sheets
  getCampaignSheets: (cid: string) =>
    request<{ coverage: SheetCoverage; refs: [string, string][] }>(
      "GET", `/api/campaigns/${cid}/sheets`),
  getCampaignSheetRoster: (cid: string) =>
    request<{ roster: SheetRoster }>("GET", `/api/campaigns/${cid}/sheets/roster`),
  createMissingSheets: (cid: string, types: Record<string, string>) =>
    request<SheetBulkResult>("POST", `/api/campaigns/${cid}/sheets/create-missing`,
      { types }).then(notifyShell),
  getWorldSheetsIndex: (wid: string) =>
    request<{ modules: string[]; default: string }>("GET", `/api/worlds/${wid}/sheets`),
  getWorldSheets: (wid: string, mid: string) =>
    request<{ coverage: SheetCoverage; refs: [string, string][] }>(
      "GET", `/api/worlds/${wid}/sheets/${mid}`),
  getSheet: (scope: EntityScope, mid: string, kind: string, eid: string) =>
    request<{ sheet: Sheet | null }>(
      "GET",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`),
  putSheet: (scope: EntityScope, mid: string, kind: string, eid: string,
             body: { sheet_type: string; fields: Record<string, unknown> | null; expected: SheetExpected }) =>
    request<{ ok: boolean }>(
      "PUT",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}`,
      body).then(notifyShell),
  putSheetCreation: (scope: EntityScope, mid: string, kind: string, eid: string,
                     body: { sheet_type: string; spends: Record<string, Record<string, number>>; expected: SheetExpected }) =>
    request<{ sheet: Sheet }>(
      "PUT",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}/creation`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}/creation`,
      body),
  advanceSheet: (cid: string, kind: string, eid: string, field: string) =>
    request<{ sheet: Sheet }>("POST", `/api/campaigns/${cid}/sheets/${kind}/${eid}/advance`, { field }),
  deleteSheet: (scope: EntityScope, mid: string, kind: string, eid: string, gen: string | null) =>
    request<{ ok: boolean }>(
      "DELETE",
      scope.kind === "campaign"
        ? `/api/campaigns/${scope.id}/sheets/${kind}/${eid}${gen ? `?gen=${encodeURIComponent(gen)}` : ""}`
        : `/api/worlds/${scope.id}/sheets/${mid}/${kind}/${eid}${gen ? `?gen=${encodeURIComponent(gen)}` : ""}`)
      .then(notifyShell),
  // --- the scene state tracker ---------------------------------------------
  /** Polled while an update is pending, so never the shared read. */
  getTracker: (cid: string, sid: string) =>
    request<TrackerSummary>("GET", `/api/campaigns/${cid}/scenes/${sid}/tracker`,
                            undefined, { fresh: true }),
  getTrackerRecord: (cid: string, sid: string, key: string) =>
    request<TrackerRecord>(
      "GET", `/api/campaigns/${cid}/scenes/${sid}/tracker/records/${encodeURIComponent(key)}`,
      undefined, { fresh: true }),
  editTrackerRecord: (cid: string, sid: string, key: string, edits: TrackerEdits) =>
    request<TrackerRecord>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/tracker/records/${encodeURIComponent(key)}`,
      { edits }),
  /** Both answer the scene's summary, with the record already `pending`. */
  retryTracker: (cid: string, sid: string, key: string) =>
    request<TrackerSummary>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/tracker/records/${encodeURIComponent(key)}/retry`),
  rerunTrackerFrom: (cid: string, sid: string, key: string) =>
    request<TrackerSummary>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/tracker/records/${encodeURIComponent(key)}/rerun-from`),
  getTrackerFields: (scope: TrackerScope) =>
    request<TrackerLayerBundle>("GET", trackerFieldsPath(scope)),
  setTrackerFields: (scope: TrackerScope, layer: TrackerLayer) =>
    request<TrackerLayerBundle>("PUT", trackerFieldsPath(scope), layer),
  getCampaignTracker: (cid: string) =>
    request<CampaignTrackerSetting>("GET", `/api/campaigns/${cid}/tracker`),
  setCampaignTracker: (cid: string, setting: TrackerSetting) =>
    request<CampaignTrackerSetting>("PUT", `/api/campaigns/${cid}/tracker`, { setting }),
};
