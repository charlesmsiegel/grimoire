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
});
