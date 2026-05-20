/**
 * REST client for the character-card import preview/commit flow.
 *
 * See ``backend/src/grimoire/api/imports.py`` and
 * ``docs/superpowers/specs/2026-05-19-card-imports-design.md`` §REST.
 */

import { ApiError } from "./client";

const API_BASE = "/api";

export interface IngestedGreetingPreview {
  source_index: number;
  body: string;
  is_primary: boolean;
}

export interface IngestedLoreEntryPreview {
  source_index: number;
  name: string | null;
  keys: string[];
  body: string;
  secondary_keys: string[];
  selective_logic: string;
  constant: boolean;
  enabled: boolean;
  case_sensitive: boolean;
  match_whole_words: boolean;
  priority: number;
  probability: number;
  position: string;
  at_depth: number | null;
  scan_depth: number | null;
  comment: string;
}

export interface IngestedCardPreview {
  data: { id: string; name: string; description: string; tags: string[] };
  spec: string;
  spec_version: string;
  creator: string;
  creator_notes: string;
  system_prompt: string;
  post_history_instructions: string;
  alternate_greetings: string[];
  extensions: Record<string, unknown>;
  warnings: string[];
  lore_entries: IngestedLoreEntryPreview[];
  greetings: IngestedGreetingPreview[];
}

export interface PreviewResponse {
  preview_id: string;
  expires_in_seconds: number;
  ingested: IngestedCardPreview;
}

export interface IngestOptionsPayload {
  expand_macros?: boolean;
  import_character_book?: boolean;
  import_alternate_greetings?: boolean;
  import_primary_greeting?: boolean;
  keep_embedded_avatar?: boolean;
  extract_relationships?: boolean;
  derive_image_prompt?: boolean;
}

export interface ImportResultPayload {
  created: string[];
  updated: string[];
  skipped: string[];
  warnings: string[];
  errors: string[];
}

export interface CommitResponse {
  result: ImportResultPayload;
}

export interface ImportReportRow {
  id: string;
  filename: string;
  size_bytes: number;
  modified_at: number;
}

export async function previewSillyTavernImport(
  worldId: string,
  file: File,
): Promise<PreviewResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(
    `${API_BASE}/library/worlds/${encodeURIComponent(worldId)}/imports/sillytavern/preview`,
    { method: "POST", body: form },
  );
  if (!res.ok) {
    throw new ApiError(res.status, await res.text().catch(() => ""));
  }
  return (await res.json()) as PreviewResponse;
}

export async function commitSillyTavernImport(
  worldId: string,
  previewId: string,
  options: IngestOptionsPayload,
): Promise<CommitResponse> {
  const res = await fetch(
    `${API_BASE}/library/worlds/${encodeURIComponent(worldId)}/imports/sillytavern/commit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preview_id: previewId, options }),
    },
  );
  if (!res.ok) {
    throw new ApiError(res.status, await res.text().catch(() => ""));
  }
  return (await res.json()) as CommitResponse;
}

export async function listImportReports(): Promise<ImportReportRow[]> {
  const res = await fetch(`${API_BASE}/library/imports`);
  if (!res.ok) {
    throw new ApiError(res.status, await res.text().catch(() => ""));
  }
  const body = (await res.json()) as { reports: ImportReportRow[] };
  return body.reports;
}

export async function getImportReport(id: string): Promise<string> {
  const res = await fetch(
    `${API_BASE}/library/imports/${encodeURIComponent(id)}`,
  );
  if (!res.ok) {
    throw new ApiError(res.status, await res.text().catch(() => ""));
  }
  const body = (await res.json()) as { id: string; body: string };
  return body.body;
}
