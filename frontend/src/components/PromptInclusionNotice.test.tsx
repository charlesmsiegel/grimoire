import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { PromptInclusionNotice } from "./PromptInclusionNotice";

vi.mock("../api/client", () => ({ api: { getPromptLayout: vi.fn() } }));
import { api } from "../api/client";

beforeEach(() => { vi.clearAllMocks(); });

test("a section the layout switched off links to the layout section itself", async () => {
  // A bare /config opens on Storage, a page away from the switch the notice
  // is about.
  vi.mocked(api.getPromptLayout).mockResolvedValue({
    enabled: true, sections: [{ id: "scene_focus", label: "Scene focus", enabled: false }],
  } as never);
  render(<MemoryRouter><PromptInclusionNotice sectionId="scene_focus" /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: "Prompt layout settings" }))
    .toHaveAttribute("href", "/config?section=layout");
});

test("a section the layout includes says nothing", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue({
    enabled: true, sections: [{ id: "scene_focus", label: "Scene focus", enabled: true }],
  } as never);
  const { container } = render(
    <MemoryRouter><PromptInclusionNotice sectionId="scene_focus" /></MemoryRouter>);
  await waitFor(() => expect(api.getPromptLayout).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
});
