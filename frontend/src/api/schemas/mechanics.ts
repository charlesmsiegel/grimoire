import { z } from "zod";

/**
 * Wire shape of `/api/mechanics/installed` (built inline in
 * `backend/src/grimoire/api/library.py::installed_mechanics` from
 * `RegisteredModule`; the live `instance` is stripped server-side).
 *
 * This replaces two diverging hand-written mirrors: `RegisteredModule` in
 * `api/library/mechanics.ts` and a `RegisteredMechanicsModule` in
 * `api/types.ts` that claimed `source` / `load_error` fields the endpoint
 * never served.
 */

export const ModuleManifestSchema = z.object({
  id: z.string(),
  name: z.string(),
  version: z.string(),
  api_version: z.string(),
  author: z.string(),
  homepage: z.string(),
  description: z.string(),
  sheet_kinds: z.array(z.string()),
  content_kinds: z.array(z.string()),
  capabilities: z.array(z.string()),
  ui: z.record(z.string(), z.unknown()),
});
export type ModuleManifest = z.infer<typeof ModuleManifestSchema>;

export const RegisteredMechanicsModuleSchema = z.object({
  manifest: ModuleManifestSchema,
  module_dir: z.string().nullable().optional(),
  sheet_schemas: z.record(z.string(), z.record(z.string(), z.unknown())).optional(),
  content_schemas: z.record(z.string(), z.record(z.string(), z.unknown())).optional(),
  theme_css: z.string().nullable().optional(),
});
export type RegisteredMechanicsModule = z.infer<typeof RegisteredMechanicsModuleSchema>;
