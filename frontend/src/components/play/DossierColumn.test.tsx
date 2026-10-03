import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import DossierColumn from "./DossierColumn";
import type { Casefile, Provenance, TrackerRecord } from "../../api/client";
import type { NowTracker } from "../tracker/TrackerNow";

const getTrackerRecord = vi.fn();
const editTrackerRecord = vi.fn();
vi.mock("../../api/client", () => ({
  api: {
    getTrackerRecord: (...a: unknown[]) => getTrackerRecord(...a),
    editTrackerRecord: (...a: unknown[]) => editTrackerRecord(...a),
    actorImageUrl: (sc: { id: string }, kind: string, aid: string, v: string, n: string,
                    o?: { w?: number; v?: string | null }) =>
      `/api/campaigns/${sc.id}/${kind}/${aid}/versions/${v}/images/${n}${o?.w ? `?w=${o.w}` : ""}${o?.v ? `${o?.w ? "&" : "?"}v=${o.v}` : ""}`,
  },
}));

const AUD: Casefile = {
  kind: "characters", id: "aud", name: "Sister Aud", version: "v1", role: "npc",
  scenes: [{ id: "004--x", title: "The Priory Door" }, { id: "011--y", title: "The Long Tide" }],
  last_seen: "The Long Tide",
  standing: "Guarded. Will not be alone with the Reeve.",
  knows: "The priory's debt. What the dry wood means.",
  suspects: "That Wyle is being paid by someone upriver",
  dossier: "A novice of the priory who counts the tide instead of the hours.",
  tagline: "",
  feels_toward: [
    { ref: "pcs:wyle", kind: "pcs", id: "wyle", name: "Ferrant Wyle",
      trust: 2, affection: 4, tension: 1, note: "He asks the right questions." },
  ],
  standing_facts: [
    { id: "f4", text: "Aud's priory owes the Reeve", date: "4 Reaping 1183",
      scene: { id: "004--the-priory-door", title: "The Priory Door", date: "4 Reaping 1183" } },
  ],
};

const opened: string[] = [];
const removed: number[] = [];
const changed: number[] = [];
function renderDossier(casefile: Casefile | null = AUD, busy = false,
                      provenance: Provenance = {}, tracker: NowTracker | null = null) {
  changed.length = 0;
  opened.length = 0; removed.length = 0;
  return render(
    <DossierColumn cid="saltmarch" casefile={casefile} busy={busy} provenance={provenance}
                   onBack={() => opened.push("back")}
                   onOpenActor={(kind, id) => opened.push(`${kind}/${id}`)}
                   onRemove={() => removed.push(1)}
                   tracker={tracker} onTrackerChanged={() => changed.push(1)} />,
  );
}

test("shows the four state rows the absorb pass writes", () => {
  renderDossier();
  expect(screen.getByText("Standing")).toBeInTheDocument();
  expect(screen.getByText(/Will not be alone with the Reeve/)).toBeInTheDocument();
  expect(screen.getByText(/What the dry wood means/)).toBeInTheDocument();
  expect(screen.getByText(/paid by someone upriver/)).toBeInTheDocument();
  expect(screen.getByText("The Long Tide")).toBeInTheDocument();
});

test("an unrecorded row is dropped, not shown blank", () => {
  // "STANDING —" reads as a fact about her; the truth is that nothing has
  // been recorded yet.
  renderDossier({ ...AUD, suspects: "" });
  expect(screen.queryByText("Suspects")).not.toBeInTheDocument();
  expect(screen.getByText("Knows")).toBeInTheDocument();
});

test("names the file each block came from", () => {
  // The panel's claim is that these are records you can go and read, not a
  // summary the app invented.
  renderDossier();
  expect(screen.getByText("dossier.md")).toBeInTheDocument();
  expect(screen.getByText("relationships.json")).toBeInTheDocument();
  expect(screen.getByText("facts.json")).toBeInTheDocument();
});

test("falls back to the tagline for someone never played, and says which file", () => {
  renderDossier({ ...AUD, dossier: "", tagline: "A novice who counts the tide." });
  expect(screen.getByText("A novice who counts the tide.")).toBeInTheDocument();
  expect(screen.getByText("tagline.md")).toBeInTheDocument();
});

test("a character with nothing recorded says what would record it", () => {
  renderDossier({
    ...AUD, standing: "", knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  });
  expect(screen.getByText(/the absorb pass writes her state/i)).toBeInTheDocument();
});

test("a feeling draws three five-pip meters and names who it is toward", () => {
  renderDossier();
  const card = screen.getByText("Ferrant Wyle").closest(".feeling-card") as HTMLElement;
  expect(within(card).getByLabelText("Trust 2 of 5")).toBeInTheDocument();
  expect(within(card).getByLabelText("Affection 4 of 5")).toBeInTheDocument();
  expect(within(card).getByLabelText("Tension 1 of 5")).toBeInTheDocument();
  expect(card.querySelectorAll(".meter-pip")).toHaveLength(15);
  expect(card.querySelectorAll(".meter-pip.on")).toHaveLength(2 + 4 + 1);
  expect(within(card).getByText(/asks the right questions/)).toBeInTheDocument();
});

test("the person a feeling points at is a way to get to them", () => {
  renderDossier();
  fireEvent.click(screen.getByText("Ferrant Wyle"));
  expect(opened).toEqual(["pcs/wyle"]);
});

test("a standing fact leads with its id, so it can be cited", () => {
  renderDossier();
  const row = screen.getByText(/priory owes the Reeve/).closest(".fact-row") as HTMLElement;
  expect(within(row).getByText("f4")).toBeInTheDocument();
  expect(within(row).getByText(/The Priory Door · 4 Reaping 1183/)).toBeInTheDocument();
});

test("the portrait is a thumbnail sized for the 134px frame, not the original", () => {
  renderDossier();
  const img = screen.getByAltText("Sister Aud portrait");
  const base = "/api/campaigns/saltmarch/characters/aud/versions/v1/images/avatar";
  expect(img.getAttribute("src")).toBe(`${base}?w=512`);
  expect(img.getAttribute("srcset")).toContain(`${base}?w=1024 682w`);
  expect(img.getAttribute("sizes")).toBe("134px");
});

test("the portrait carries the casefile's avatar token, so it is cached immutable", () => {
  renderDossier({ ...AUD, avatar_v: "1a-2b" });
  const img = screen.getByAltText("Sister Aud portrait");
  const base = "/api/campaigns/saltmarch/characters/aud/versions/v1/images/avatar";
  expect(img.getAttribute("src")).toBe(`${base}?w=512&v=1a-2b`);
  expect(img.getAttribute("srcset")).toContain(`${base}?w=1024&v=1a-2b 682w`);
});

test("‹ All cast returns to the grid", () => {
  renderDossier();
  fireEvent.click(screen.getByRole("button", { name: /all cast/i }));
  expect(opened).toEqual(["back"]);
});

test("Remove from scene is locked while the scene is being written to", () => {
  // Removing someone mid-turn moves the cast out from under the write.
  const locked = renderDossier(AUD, true);
  expect(screen.getByRole("button", { name: /remove from scene/i })).toBeDisabled();
  locked.unmount();

  renderDossier(AUD, false);
  fireEvent.click(screen.getByRole("button", { name: /remove from scene/i }));
  expect(removed).toEqual([1]);
});

test("shows a reading state rather than the previous actor's dossier", () => {
  // Two people's private states; briefly attributing one to the other is the
  // one failure this panel must not have.
  renderDossier(null);
  expect(screen.getByText("Reading…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /all cast/i })).toBeInTheDocument();
});

// ---- provenance (4a) ----

const STATE_CITATION = {
  quote: "I'd rather the mud than his company.", speaker: "Sister Aud",
  certainty: 0.92, authority: "self", band: "high", scene: "ix",
  recorded: "2026-08-13T10:00:00Z",
};

test("the three state rows share one citation, because one edit wrote them", () => {
  // The absorb stages a single `character_state` edit whose `after` is the
  // whole of state.md; `playstate.parse_body` splits it into three headed
  // sections on read. One edit, one quote, three rows.
  renderDossier(AUD, false, { "characters/aud#current_state": STATE_CITATION });
  for (const label of ["Standing", "Knows", "Suspects"]) {
    expect(screen.getByRole("button", { name: new RegExp(`^${label}: Cited`) }))
      .toHaveClass("cited");
  }
});

test("a campaign with no citations renders every row uncited rather than blank", () => {
  // Normal for anything absorbed before the store existed, and for a record
  // edited by hand.
  renderDossier(AUD, false, {});
  expect(screen.getByRole("button", { name: /^Standing: No citation/ }))
    .toHaveClass("uncited");
});

test("Last seen carries no marker at all", () => {
  // It is read off the appearance record, not proposed by a model, so there is
  // nothing for a citation to be about.
  renderDossier(AUD, false, { "characters/aud#current_state": STATE_CITATION });
  expect(screen.queryByRole("button", { name: /^Last seen:/ })).not.toBeInTheDocument();
  expect(screen.getByText("The Long Tide")).toBeInTheDocument();
});

const NOW: NowTracker = {
  cid: "saltmarch", sid: "004--x", key: "k2", ref: "characters:aud", enabled: true, entry: undefined,
};
const RECORD: TrackerRecord = {
  key: "k2", status: "ok", flags: { upstream_changed: false, text_changed: false },
  fields: [
    { key: "mood", label: "Mood", type: "text", aware: "present", hint: "" },
    { key: "plan", label: "Plan", type: "text", aware: "self", hint: "" },
  ],
  names: { "characters:aud": "Sister Aud", "pcs:wyle": "Ferrant Wyle" },
  snapshot: {
    "characters:aud": { present: true, fields: {
      mood: { value: "wary", aware: "present" }, plan: { value: "stall", aware: [] } } },
    "pcs:wyle": { present: true, fields: { mood: { value: "eager", aware: "present" } } },
  },
};

describe("Now", () => {
  beforeEach(() => { getTrackerRecord.mockReset(); editTrackerRecord.mockReset(); });

  test("no Now section without a tracked key", () => {
    renderDossier(AUD, false, {}, { ...NOW, key: null });
    expect(screen.queryByText("Now")).not.toBeInTheDocument();
    expect(getTrackerRecord).not.toHaveBeenCalled();
  });

  test("no Now section when the tracker is not wired", () => {
    renderDossier();
    expect(screen.queryByText("Now")).not.toBeInTheDocument();
  });

  test("shows this actor's current values and only this actor's", async () => {
    getTrackerRecord.mockResolvedValue(RECORD);
    renderDossier(AUD, false, {}, NOW);
    expect(await screen.findByText("Now")).toBeInTheDocument();
    expect(screen.getByText("scene state")).toBeInTheDocument();
    expect(screen.getByText(/wary/)).toBeInTheDocument();
    expect(screen.queryByText(/eager/)).not.toBeInTheDocument();
    expect(getTrackerRecord).toHaveBeenCalledWith("saltmarch", "004--x", "k2");
  });

  test("an actor missing from the snapshot gets no Now section", async () => {
    getTrackerRecord.mockResolvedValue({ ...RECORD, snapshot: { "pcs:wyle": RECORD.snapshot!["pcs:wyle"] } });
    renderDossier(AUD, false, {}, NOW);
    await waitFor(() => expect(getTrackerRecord).toHaveBeenCalled());
    expect(screen.queryByText("Now")).not.toBeInTheDocument();
  });

  test("Edit reveals a form limited to this actor, and saving sends only what moved", async () => {
    getTrackerRecord.mockResolvedValue(RECORD);
    editTrackerRecord.mockResolvedValue(RECORD);
    renderDossier(AUD, false, {}, NOW);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Sister Aud Mood")).toHaveValue("wary");
    expect(screen.queryByLabelText("Ferrant Wyle Mood")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Sister Aud Mood"), { target: { value: "calm" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(editTrackerRecord).toHaveBeenCalledWith(
      "saltmarch", "004--x", "k2", { "characters:aud": { mood: { value: "calm" } } }));
    await waitFor(() => expect(changed).toEqual([1]));
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
  });

  test("Edit is not offered while the tracker is off", async () => {
    getTrackerRecord.mockResolvedValue(RECORD);
    renderDossier(AUD, false, {}, { ...NOW, enabled: false });
    expect(await screen.findByText(/wary/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  test("a re-read landing under an open form does not change what it diffs against", async () => {
    getTrackerRecord.mockResolvedValue(RECORD);
    editTrackerRecord.mockResolvedValue(RECORD);
    const view = renderDossier(AUD, false, {}, NOW);
    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    // The summary moves (a poll found the entry changed); the record behind
    // the key now reads differently. The open form keeps what it was built on.
    getTrackerRecord.mockResolvedValue({
      ...RECORD,
      snapshot: { ...RECORD.snapshot!, "characters:aud": { present: true, fields: {
        mood: { value: "furious", aware: "present" } } } },
    });
    view.rerender(
      <DossierColumn cid="saltmarch" casefile={AUD} busy={false} provenance={{}}
                     onBack={() => {}} onOpenActor={() => {}} onRemove={() => {}}
                     tracker={{ ...NOW, entry: { status: "ok", changed: [],
                       flags: { upstream_changed: false, text_changed: false } } }}
                     onTrackerChanged={() => {}} />);
    await waitFor(() => expect(getTrackerRecord).toHaveBeenCalledTimes(2));
    await act(async () => { await Promise.resolve(); });
    // Nothing touched: if the diff were taken against the re-read record, the
    // untouched "wary" would now read as an edit away from "furious".
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument());
    expect(editTrackerRecord).not.toHaveBeenCalled();
  });

  test("advancing the key keeps the last record up until the new one lands", async () => {
    getTrackerRecord.mockResolvedValueOnce(RECORD);
    const view = renderDossier(AUD, false, {}, NOW);
    expect(await screen.findByText(/wary/)).toBeInTheDocument();
    let landNew!: (r: TrackerRecord) => void;
    getTrackerRecord.mockImplementationOnce(() => new Promise((r) => { landNew = r; }));
    view.rerender(
      <DossierColumn cid="saltmarch" casefile={AUD} busy={false} provenance={{}}
                     onBack={() => {}} onOpenActor={() => {}} onRemove={() => {}}
                     tracker={{ ...NOW, key: "k3" }} onTrackerChanged={() => {}} />);
    await waitFor(() => expect(getTrackerRecord).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Now")).toBeInTheDocument();
    expect(screen.getByText(/wary/)).toBeInTheDocument();
    landNew({ ...RECORD, key: "k3", snapshot: { "characters:aud": { present: true, fields: {
      mood: { value: "newer", aware: "present" } } } } });
    expect(await screen.findByText(/newer/)).toBeInTheDocument();
    expect(screen.queryByText(/wary/)).not.toBeInTheDocument();
  });

  test("a response for a key that is no longer current is dropped", async () => {
    let resolveOld!: (r: TrackerRecord) => void;
    getTrackerRecord.mockImplementationOnce(() => new Promise((r) => { resolveOld = r; }));
    const view = renderDossier(AUD, false, {}, NOW);
    getTrackerRecord.mockResolvedValue({
      ...RECORD, key: "k3",
      snapshot: { "characters:aud": { present: true, fields: { mood: { value: "newer", aware: "present" } } } },
    });
    view.rerender(
      <DossierColumn cid="saltmarch" casefile={AUD} busy={false} provenance={{}}
                     onBack={() => {}} onOpenActor={() => {}} onRemove={() => {}}
                     tracker={{ ...NOW, key: "k3" }} onTrackerChanged={() => {}} />);
    expect(await screen.findByText(/newer/)).toBeInTheDocument();
    // The k2 read answers last. Nothing else is on screen to hide it, so
    // only the `live` guard keeps "wary" from replacing "newer".
    await act(async () => { resolveOld(RECORD); await Promise.resolve(); });
    expect(screen.queryByText(/wary/)).not.toBeInTheDocument();
    expect(screen.getByText(/newer/)).toBeInTheDocument();
  });

  test("an API error shows inline", async () => {
    getTrackerRecord.mockRejectedValue(new Error("tracker store unreadable"));
    renderDossier(AUD, false, {}, NOW);
    expect(await screen.findByRole("alert")).toHaveTextContent(/tracker store unreadable/);
    expect(screen.getByText("Now")).toBeInTheDocument();
  });
});
