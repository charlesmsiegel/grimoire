import { describe, expect, it } from "vitest";

import { cleanDraftForSave } from "../schemaForm";

describe("cleanDraftForSave", () => {
  it("drops null, undefined, and empty-string fields", () => {
    expect(
      cleanDraftForSave({
        model_path: "/m.gguf",
        n_ctx: null, // cleared number input
        seed: undefined,
        chat_format: "", // cleared text input
      }),
    ).toEqual({ model_path: "/m.gguf" });
  });

  it("preserves 0 and false (real values, not blanks)", () => {
    expect(cleanDraftForSave({ n_gpu_layers: 0, flag: false })).toEqual({
      n_gpu_layers: 0,
      flag: false,
    });
  });

  it("drops empty nested objects and arrays but keeps 0/false", () => {
    expect(
      cleanDraftForSave({
        api_key: "k",
        provider: { sort: "", order: [], allow_fallbacks: false },
        extra_headers: {},
        timeout_seconds: 0,
      }),
    ).toEqual({ api_key: "k", provider: { allow_fallbacks: false }, timeout_seconds: 0 });
  });

  it("removes a nested object that compacts to empty", () => {
    expect(cleanDraftForSave({ provider: { sort: "", order: [] } })).toEqual({});
  });
});
