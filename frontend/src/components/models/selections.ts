import type { GenerativeRole, InferenceSelection } from "../../api/client";

export const GENERATIVE: GenerativeRole[] = ["primary", "fast", "decision"];
export const EMPTY_SEL: InferenceSelection = { provider: "", model: "", preset: "" };
export const sameSel = (a: InferenceSelection, b: InferenceSelection) =>
  a.provider === b.provider && a.model === b.model && a.preset === b.preset;
