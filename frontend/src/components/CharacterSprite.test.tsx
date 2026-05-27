import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { CharacterSprite } from "./CharacterSprite";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("CharacterSprite", () => {
  it("renders the resolved sprite image when present", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        emotion: "happy",
        sprite_url: "/library/worlds/w/characters/beatrice/sprites/happy.png",
        fallback_used: false,
      }),
    );
    render(
      <CharacterSprite
        campaignId="cmp_1"
        characterId="beatrice"
        characterName="Beatrice"
        asOfTurn="t_1"
        expressionsEnabled
      />,
    );
    const img = await screen.findByRole("img");
    expect(img.getAttribute("src")).toContain("/sprites/happy.png");
    expect(img.getAttribute("alt")).toContain("Beatrice");
  });

  it("falls back to plain name when sprite_url is null", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ emotion: "neutral", sprite_url: null, fallback_used: true }),
    );
    render(
      <CharacterSprite
        campaignId="cmp_1"
        characterId="naked"
        characterName="Ralph"
        asOfTurn="t_1"
        expressionsEnabled
      />,
    );
    await waitFor(() => expect(screen.getByText("Ralph")).toBeInTheDocument());
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("does not fetch when campaignId is missing", () => {
    render(
      <CharacterSprite
        campaignId=""
        characterId="beatrice"
        characterName="Beatrice"
        expressionsEnabled
      />,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not fetch when expressions are disabled (default)", () => {
    render(<CharacterSprite campaignId="cmp_1" characterId="beatrice" characterName="Beatrice" />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("Beatrice")).toBeInTheDocument();
  });
});
