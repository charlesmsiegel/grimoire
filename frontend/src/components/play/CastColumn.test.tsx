import { fireEvent, render, screen, within } from "@testing-library/react";
import CastColumn, { tiers } from "./CastColumn";
import type { Actor, Briefing, RosterEntry } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    actorImageUrl: (sc: { id: string }, kind: string, aid: string, v: string, n: string) =>
      `/api/campaigns/${sc.id}/${kind}/${aid}/versions/${v}/images/${n}`,
  },
}));

const CAST: Actor[] = [
  { kind: "pcs", id: "wyle", role: "player", name: "Ferrant Wyle" },
  { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" },
];
const ROSTER: RosterEntry[] = [
  { kind: "pcs", id: "wyle", version: "v1", role: "player", scenes: ["s1"] },
  { kind: "characters", id: "aud", version: "v1", role: "npc", scenes: ["s1"] },
  // played before, not here now — the grid must not show her
  { kind: "characters", id: "reeve", version: "v1", role: "npc", scenes: ["s0"] },
  // named, never played
  { kind: "characters", id: "unseen", version: "v1", role: "npc", scenes: [] },
];
const BRIEFING: Briefing = {
  focus: ["Ferrant Wyle"],
  plot: [{ id: "t1", title: "The priory's debt", status: "open",
           last_scene: "iv", latest_beat: "", involves: [] }],
  commitments: [
    { id: "c1", title: "The Reeve will call it in", status: "open", kind: "threat",
      due: "by the turn of the tide", last_scene: "iv", latest_beat: "", involves: [] },
    { id: "c2", title: "Bring the nail back", status: "open", kind: "promise",
      due: "", last_scene: "v", latest_beat: "", involves: [] },
  ],
  relationships: [], last_time: null,
};

const opened: string[] = [];
function renderColumn(props: Partial<Parameters<typeof CastColumn>[0]> = {}) {
  opened.length = 0;
  return render(
    <CastColumn cid="saltmarch" cast={CAST} roster={ROSTER} briefing={BRIEFING}
                onOpen={(kind, id) => opened.push(`${kind}/${id}`)} {...props} />,
  );
}

describe("tiering", () => {
  it("is the room and only the room", () => {
    // The campaign's wider roster is a browsable list of records and lives on
    // the Characters page. Folding it in here buried the two or three people
    // actually on stage under everyone the campaign has ever met.
    expect(tiers(CAST, ROSTER).map((t) => [t.id, t.state])).toEqual([
      ["wyle", "PLAYER"],
      ["aud", "IN SCENE"],
    ]);
  });

  it("keeps cast order", () => {
    expect(tiers(CAST, ROSTER).map((t) => t.id)).toEqual(["wyle", "aud"]);
  });

  it("takes the locked version from the roster, so a portrait resolves", () => {
    // The one thing the roster is still read for: the scene's cast record does
    // not carry the version this campaign locked, and the portrait URL needs it.
    expect(tiers(CAST, ROSTER)[1].version).toBe("v1");
  });

  it("copes with a cast member the roster does not list", () => {
    // The appearance record and the scene's cast are written by different
    // paths; a tile with no version renders its initials rather than throwing.
    expect(tiers(CAST, []).map((t) => t.version)).toEqual(["", ""]);
  });
});

test("every actor is a tile that opens their dossier", () => {
  renderColumn();
  fireEvent.click(screen.getByText("Sister Aud"));
  expect(opened).toEqual(["characters/aud"]);
});

test("a PC's tile shows their portrait, not just initials", () => {
  // Before #219 the tile hard-coded `kind === "characters"`, so the player --
  // the one person on stage the reader is playing -- was the only one in the
  // grid who never had a face.
  renderColumn();
  const pc = screen.getByText("Ferrant Wyle").closest(".cast-tile")!;
  expect(pc.querySelector("img")!.getAttribute("src"))
    .toBe("/api/campaigns/saltmarch/pcs/wyle/versions/v1/images/avatar");
  const npc = screen.getByText("Sister Aud").closest(".cast-tile")!;
  expect(npc.querySelector("img")!.getAttribute("src"))
    .toBe("/api/campaigns/saltmarch/characters/aud/versions/v1/images/avatar");
});

test("a tile says where its actor stands", () => {
  renderColumn();
  expect(screen.getByText("PLAYER")).toBeInTheDocument();
  expect(screen.getByText("IN SCENE")).toBeInTheDocument();
});

test("someone the campaign has met but who is not in this scene is not in the grid", () => {
  // The Reeve is in the roster with a scene behind her. She is not on stage,
  // so the column beside the transcript does not claim she is.
  renderColumn();
  expect(screen.queryByText("reeve")).not.toBeInTheDocument();
  expect(screen.getByText("Sister Aud").closest(".cast-grid")!
    .querySelectorAll(".cast-tile")).toHaveLength(2);
});

test("threads and commitments sit beside the transcript, not behind a toggle", () => {
  renderColumn();
  expect(screen.getByText("The priory's debt")).toBeInTheDocument();
  expect(screen.getByText("The Reeve will call it in")).toBeInTheDocument();
  // named as the files they are, so they can be gone and read
  expect(screen.getByText("plot.json")).toBeInTheDocument();
  expect(screen.getByText("commitments.json")).toBeInTheDocument();
});

test("a threat reads in the alert colour; a promise does not", () => {
  renderColumn();
  expect(screen.getByText("THREAT")).toHaveClass("alert");
  expect(screen.getByText("PROMISE")).not.toHaveClass("alert");
});

test("a commitment with no date says so rather than showing a blank", () => {
  renderColumn();
  const promise = screen.getByText("Bring the nail back").closest(".brief-row") as HTMLElement;
  expect(within(promise).getByText(/NO DEADLINE/)).toBeInTheDocument();
});

test("an empty room says what would fill it, not just that it is empty", () => {
  // Roster left populated on purpose: a scene with nobody cast is empty even
  // in a campaign with a long history.
  renderColumn({ cast: [] });
  expect(screen.getByText(/cast someone to begin/i)).toBeInTheDocument();
});

test("nothing open and nothing owed are stated, not left blank", () => {
  renderColumn({ briefing: { ...BRIEFING, plot: [], commitments: [] } });
  expect(screen.getByText("Nothing open.")).toBeInTheDocument();
  expect(screen.getByText("Nothing owed.")).toBeInTheDocument();
});

test("a briefing that failed to load empties its own blocks and nothing else", () => {
  renderColumn({ briefing: null });
  expect(screen.getByText("Sister Aud")).toBeInTheDocument();
  expect(screen.getByText("Nothing open.")).toBeInTheDocument();
});
