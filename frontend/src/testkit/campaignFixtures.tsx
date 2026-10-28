import { useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { CascadeReport, SceneMeta } from "../api/client";
import type { RollProposalPayload } from "../api/stream";
import { ONE_SCENE } from "./campaignHarness";
import { fixture } from "./fixture";


/** Reads back whatever is left on the history entry, so a test can watch the
 *  handoff be consumed rather than merely assume it was. */
export function ShowState() {
  const seed = (useLocation().state as { seedPrompt?: string } | null)?.seedPrompt;
  return <span data-testid="hist-state">{seed ?? "none"}</span>;
}

// `listScenes` as the server actually answers it around a rename: the mount
// read still sees the old id, and every read AFTER the rename landed sees the
// new one. The relists that follow a mutation are `fresh` reads issued once the
// write returned, so they cannot come back pre-rename — and mocking them that
// way models a server that lost the rename it just confirmed.
export function relistsAs(before: SceneMeta[], after: SceneMeta[]) {
  vi.mocked(api.listScenes).mockResolvedValueOnce(before).mockResolvedValue(after);
}

// `listScenes` no longer takes a `fresh` flag — the endpoint never coalesces
// now (#87), so nothing distinguishes a mount read from a relist at the API
// level. These fixtures tell them apart by order instead: the first read of a
// campaign is its mount read, every later one is a relist.
export function readCounter() {
  const seen = new Map<string, number>();
  return (cid: string) => {
    const n = (seen.get(cid) ?? 0) + 1;
    seen.set(cid, n);
    return n;
  };
}
export const trackerCallsFor = (sid: string) =>
  vi.mocked(api.getTracker).mock.calls.filter((c) => c[1] === sid).length;

// ---- cascade post-delete (#75) ----
//
// The gutter's 🗑 takes the post it sits on and every one after it, and (for a
// scene that has been absorbed) reverses what that scene wrote. The confirm is
// part of the contract, not decoration: what the cascade reverts is invisible in
// the transcript afterwards, so the prompt has to name it before the fact and
// the reply has to report anything it could not put back.

export const CASCADE_OK: CascadeReport = { index: 1, removed: 2, was_absorbed: false, records: 0,
                     refused: [], chronicle: false, plot_beats: 0,
                     commitment_beats: 0, changes: 0, citations: 0, failed: [] };

export function twoPostScene() {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
}

// A scene absorbed into the chronicle is finished: its summary is written and
// its changes are applied, so anything appended now sits outside the record
// taken of it.
export const DONE_SCENE: SceneMeta[] = [{ id: "s1", title: "Old", model: "", created: "", updated: "", date: "", done: true }];

export const SCENES_WITH_DATE: SceneMeta[] = ONE_SCENE.map((scene) => ({ ...scene, date: "" }));

// The wire addresses a variant by a content-derived `id`, not by position:
// retention shifts every index when a full set gains a take.
export const ALT = (preview: string, model = "") =>
  ({ id: `id-${preview}`, created: "t", guidance: "", model, posts: 1, preview });

export const PROPOSAL_PAYLOAD: RollProposalPayload = {
  id: "pr-1", check: "brawl", check_label: "Vigor + Brawl",
  actor: "characters:mara", actor_label: "Mara", difficulty: 6,
  available: { "characters:mara": [["brawl", "Vigor + Brawl"]] },
  problems: [],
};

// --- scene navigation (#87) ------------------------------------------------
// The scene on screen lives in the URL, so a reload — or a shared link — lands
// back on the scene the reader was in rather than whichever one was edited last.

// listScenes answers most-recently-updated first, so s1 is what the view
// picks with nothing in the URL to say otherwise. s2 is therefore the scene
// that can only be reached by asking for it.
export const TWO_SCENES: SceneMeta[] = [
  { id: "s1", title: "Old", model: "", created: "", updated: "2", date: "" },
  { id: "s2", title: "The Saltmarch Gate", model: "", created: "", updated: "1", date: "" },
];

// One transcript per scene, so "which scene loaded" is readable off the screen.
export function transcriptsPerScene() {
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => fixture({
    meta: {}, messages: [{ role: "assistant", content: `transcript of ${sid}` }],
  }));
}

