/**
 * Regression test for PR #417: `cleanRoutes` must strip incomplete
 * `"provider."` entries so the auto-save PUT does not 422.
 *
 * When the user picks a provider before choosing a model, the
 * `RouteRow` writes `"<provider>."` into component state so the
 * provider dropdown stays selected. The backend's `Route.parse`
 * rejects that string, so we strip it on the way out.
 */
import { describe, expect, it } from "vitest";

import { cleanRoutes, type RoutingValue } from "../campaignRouting";

describe("cleanRoutes", () => {
  it("drops entries that end in '.' (provider picked, no model)", () => {
    const input: RoutingValue = {
      llm: { main: "openai.gpt-4o", drift_check: "anthropic." },
      embedding: {},
      imagegen: {},
    };
    expect(cleanRoutes(input)).toEqual({
      llm: { main: "openai.gpt-4o" },
      embedding: {},
      imagegen: {},
    });
  });

  it("drops empty-string entries defensively", () => {
    const input: RoutingValue = {
      llm: { main: "" },
      embedding: { "embed:context": "openai.text-embedding-3-small" },
      imagegen: {},
    };
    expect(cleanRoutes(input)).toEqual({
      llm: {},
      embedding: { "embed:context": "openai.text-embedding-3-small" },
      imagegen: {},
    });
  });

  it("passes through fully-specified provider.model entries", () => {
    const input: RoutingValue = {
      llm: { main: "openai.gpt-4o-mini" },
      embedding: { "embed:context": "openai.text-embedding-3-small" },
      imagegen: { portrait: "replicate.flux-schnell" },
    };
    expect(cleanRoutes(input)).toEqual(input);
  });

  it("treats missing blocks as empty", () => {
    const input = {} as RoutingValue;
    expect(cleanRoutes(input)).toEqual({ llm: {}, embedding: {}, imagegen: {} });
  });
});
