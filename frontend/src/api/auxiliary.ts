/**
 * Auxiliary-task REST client.
 *
 * Auxiliary tasks (drafts, rewrites, brainstorms, etc.) are non-canonical
 * model calls — they never mutate scene state until the user explicitly
 * accepts a result. The backend parks each result in an in-memory slot
 * keyed by the returned `id`; accept commits it via the appropriate
 * canonical path, discard throws it away.
 */

import { api } from "./client";

export type AuxiliaryKind =
  | "impersonate_pc"
  | "rewrite_post"
  | "continue_as"
  | "what_would_x_say"
  | "brainstorm"
  | "edit_prose"
  | "translate";

export type AuxiliaryCommitAction =
  | "submit_post"
  | "replace_post"
  | "append_post"
  | "copy"
  | "replace_draft";

export interface AuxiliaryResult {
  id: string;
  kind: AuxiliaryKind;
  text: string;
  model_used: string;
  tokens: number;
  pending_commit_action: AuxiliaryCommitAction;
  warnings: string[];
  completed_at: string;
}

export interface AcceptAuxiliaryResponse {
  committed: boolean;
  action: AuxiliaryCommitAction;
  result_id: string;
  text?: string;
  post_id?: string;
  alternate_id?: string;
  cascaded_replace?: boolean;
  turn_id?: string;
}

const enc = encodeURIComponent;
const base = (campaignId: string) => `/api/campaigns/${enc(campaignId)}/auxiliary`;

export const auxiliaryApi = {
  impersonatePC: (campaignId: string, steeringHint?: string) =>
    api.post<AuxiliaryResult>(`${base(campaignId)}/impersonate-pc`, {
      steering_hint: steeringHint,
    }),

  rewritePost: (campaignId: string, postId: string, editInstruction: string) =>
    api.post<AuxiliaryResult>(`${base(campaignId)}/rewrite-post`, {
      post_id: postId,
      edit_instruction: editInstruction,
    }),

  continueAs: (
    campaignId: string,
    characterRef: string,
    targetPostId?: string,
    steeringHint?: string,
  ) =>
    api.post<AuxiliaryResult>(`${base(campaignId)}/continue-as`, {
      character_ref: characterRef,
      target_post_id: targetPostId,
      steering_hint: steeringHint,
    }),

  whatWouldXSay: (campaignId: string, characterRef: string, snippet: string) =>
    api.post<AuxiliaryResult>(`${base(campaignId)}/what-would-x-say`, {
      character_ref: characterRef,
      snippet,
    }),

  brainstorm: (campaignId: string, prompt: string) =>
    api.post<AuxiliaryResult>(`${base(campaignId)}/brainstorm`, { prompt }),

  editProse: (campaignId: string, snippet: string, editInstruction: string) =>
    api.post<AuxiliaryResult>(`${base(campaignId)}/edit-prose`, {
      snippet,
      edit_instruction: editInstruction,
    }),

  translate: (campaignId: string, snippet: string, targetLanguage: string) =>
    api.post<AuxiliaryResult>(`${base(campaignId)}/translate`, {
      snippet,
      target_language: targetLanguage,
    }),

  accept: (campaignId: string, resultId: string, editedText?: string) =>
    api.post<AcceptAuxiliaryResponse>(`${base(campaignId)}/${enc(resultId)}/accept`, {
      edited_text: editedText,
    }),

  discard: (campaignId: string, resultId: string) =>
    api.post<{ discarded: boolean; result_id: string }>(
      `${base(campaignId)}/${enc(resultId)}/discard`,
    ),

  inFlight: (campaignId: string) => api.get<AuxiliaryResult[]>(`${base(campaignId)}/in-flight`),
};
