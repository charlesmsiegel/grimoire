import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ImageDescriptionField } from "./ImageDescriptionField";

/** The distinction the whole feature rests on: `undefined` (nobody has looked
 *  at this image) and `""` (somebody looked and decided it needs nothing) are
 *  different states, and the collapsed label has to say which. */
describe("the collapsed label", () => {
  it("invites a description when the image has never been reviewed", () => {
    render(<ImageDescriptionField name="gallery_1" value={undefined}
                                  onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Description of gallery_1/ }))
      .toHaveTextContent("Describe…");
  });

  it("says so when the image was reviewed and left undescribed", () => {
    render(<ImageDescriptionField name="gallery_1" value="" onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Description of gallery_1/ }))
      .toHaveTextContent("No description");
  });

  it("shows the description once there is one", () => {
    render(<ImageDescriptionField name="gallery_1" value="A grey quay."
                                  onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Description of gallery_1/ }))
      .toHaveTextContent("A grey quay.");
  });
});

describe("editing", () => {
  it("opens a textarea seeded with the current text and saves it", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<ImageDescriptionField name="avatar" value="Old text." onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: /Description of avatar/ }));

    const box = await screen.findByRole("textbox", { name: /Description of avatar/ });
    expect(box).toHaveValue("Old text.");
    fireEvent.change(box, { target: { value: "New text." } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("New text."));
  });

  it("persists an empty string for 'No description' rather than deleting", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<ImageDescriptionField name="avatar" value={undefined} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: /Description of avatar/ }));
    await screen.findByRole("textbox", { name: /Description of avatar/ });

    fireEvent.click(screen.getByRole("button", { name: "No description" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(""));
  });

  it("closes without saving on Cancel", async () => {
    const onSave = vi.fn();
    render(<ImageDescriptionField name="avatar" value="Kept." onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: /Description of avatar/ }));
    const box = await screen.findByRole("textbox", { name: /Description of avatar/ });
    fireEvent.change(box, { target: { value: "Discarded." } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.queryByRole("textbox", { name: /Description of avatar/ })).toBeNull());
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Description of avatar/ }))
      .toHaveTextContent("Kept.");
  });

  it("keeps the editor open and shows why when a save fails", async () => {
    const onSave = vi.fn().mockRejectedValue({ detail: "image not found" });
    render(<ImageDescriptionField name="avatar" value={undefined} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: /Description of avatar/ }));
    await screen.findByRole("textbox", { name: /Description of avatar/ });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("image not found")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Description of avatar/ })).toBeInTheDocument();
  });
});

describe("the model-drafted first pass", () => {
  it("offers no draft button when the caller has no endpoint for it", async () => {
    render(<ImageDescriptionField name="avatar" value={undefined} onSave={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Description of avatar/ }));
    await screen.findByRole("textbox", { name: /Description of avatar/ });
    expect(screen.queryByRole("button", { name: /Describe it for me/ })).toBeNull();
  });

  it("fills the textarea with the draft without saving it", async () => {
    const onSave = vi.fn();
    const onDraft = vi.fn().mockResolvedValue("A drafted sentence.");
    render(<ImageDescriptionField name="avatar" value={undefined}
                                  onSave={onSave} onDraft={onDraft} />);
    fireEvent.click(screen.getByRole("button", { name: /Description of avatar/ }));
    await screen.findByRole("textbox", { name: /Description of avatar/ });

    fireEvent.click(screen.getByRole("button", { name: /Describe it for me/ }));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /Description of avatar/ }))
        .toHaveValue("A drafted sentence."));
    // The human still has the last word: a draft is never written to the store.
    expect(onSave).not.toHaveBeenCalled();
  });
});
