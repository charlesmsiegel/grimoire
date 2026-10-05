import { describe, expect, it } from "vitest";
import type { LLMConnection } from "../api/client";
import { embeddingsOn } from "./embeddingsOn";

const UNCHECKED: LLMConnection["health"] = { state: "unknown", kind: "", detail: "", at: "" };
const conn = (over: Partial<LLMConnection>): LLMConnection => ({
  id: "local", kind: "openai_compatible", name: "Local vectors",
  base_url: "http://localhost:1234/v1", model: "", effective_model: "",
  post_process: "none", key_set: false, rev: "r1", health: UNCHECKED,
  ...over,
});
const draft = { embeddings_connection_id: "local", embeddings_model: "text-embedding-3-small" };

describe("embeddingsOn", () => {
  it("is on when the id resolves to an openai-compatible connection with a URL and a model is set", () => {
    expect(embeddingsOn(draft, [conn({})])).toBe(true);
  });

  it("is off with no connection id", () => {
    expect(embeddingsOn({ ...draft, embeddings_connection_id: "" }, [conn({})])).toBe(false);
  });

  it("is off when the id names no connection", () => {
    expect(embeddingsOn({ ...draft, embeddings_connection_id: "gone" }, [conn({})])).toBe(false);
  });

  it("is off for a connection kind that serves no /embeddings route", () => {
    expect(embeddingsOn(draft, [conn({ kind: "openrouter" })])).toBe(false);
  });

  it("is off for a connection with no base URL", () => {
    expect(embeddingsOn(draft, [conn({ base_url: "" })])).toBe(false);
  });

  it("is off with a blank model", () => {
    expect(embeddingsOn({ ...draft, embeddings_model: "   " }, [conn({})])).toBe(false);
  });
});
