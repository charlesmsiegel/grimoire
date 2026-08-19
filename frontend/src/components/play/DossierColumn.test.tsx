import { fireEvent, render, screen, within } from "@testing-library/react";
import DossierColumn from "./DossierColumn";
import type { Casefile, Provenance } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: {
    actorImageUrl: (sc: { id: string }, kind: string, aid: string, v: string, n: string) =>
      `/api/campaigns/${sc.id}/${kind}/${aid}/versions/${v}/images/${n}`,
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
function renderDossier(casefile: Casefile | null = AUD, busy = false,
                      provenance: Provenance = {}) {
  opened.length = 0; removed.length = 0;
  return render(
    <DossierColumn cid="saltmarch" casefile={casefile} busy={busy} provenance={provenance}
                   onBack={() => opened.push("back")}
                   onOpenActor={(kind, id) => opened.push(`${kind}/${id}`)}
                   onRemove={() => removed.push(1)} />,
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
