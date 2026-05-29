import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManifestForm } from "../ManifestForm";
import type { ManifestSpec } from "../../../../api/library/mechanics";

const spec: ManifestSpec = {
  id: "acme",
  name: "Acme",
  version: "1.0.0",
  api_version: "1",
  sheet_kinds: ["character"],
};

describe("ManifestForm", () => {
  it("edits name and emits", () => {
    const onChange = vi.fn();
    render(<ManifestForm value={spec} onChange={onChange} idEditable={false} />);
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: "Renamed" } });
    expect(onChange.mock.calls.at(-1)?.[0].name).toBe("Renamed");
  });

  it("disables id when not editable", () => {
    render(<ManifestForm value={spec} onChange={vi.fn()} idEditable={false} />);
    expect(screen.getByLabelText(/^id/i)).toBeDisabled();
  });
});
