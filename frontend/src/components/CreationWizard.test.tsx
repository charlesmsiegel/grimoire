import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  api: {
    putSheetCreation: vi.fn(),
  },
}));
import { api } from "../api/client";
import CreationWizard from "./CreationWizard";

beforeEach(() => {
  vi.clearAllMocks();
});

const scope = { kind: "world" as const, id: "w1" };

const module = {
  id: "testmod", source: "builtin", manifest: { id: "testmod", name: "Test" },
  sheets: {
    groups: { attributes: { label: "Attributes", fields: [{ key: "vigor", type: "dots", max: 5 }] } },
    sheet_types: {
      hero: {
        label: "Hero", kind: "items", groups: ["attributes"], fields: [],
        creation: { pools: { attributes: { budget: 3, costs: { vigor: 1 } } } },
      },
      plain: { label: "Plain", kind: "items", groups: [], fields: [] },
    },
  },
  checks: {}, rules: [], content: [], errors: [],
} as any;

describe("CreationWizard", () => {
  it("creates the record via createRecord, picks a type, spends a pool, and calls onDone", async () => {
    const createRecord = vi.fn().mockResolvedValue("sword");
    (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "hero", fields: {}, derived: {}, errors: [] } });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} onDone={onDone} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
    fireEvent.click(screen.getByText("Next"));
    expect(createRecord).not.toHaveBeenCalled();

    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
    fireEvent.click(screen.getByText("Next"));
    expect(createRecord).not.toHaveBeenCalled();

    const input = screen.getByLabelText("Vigor");
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(createRecord).toHaveBeenCalledWith("Sword"));
    await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
      scope, "testmod", "items", "sword", { sheet_type: "hero", spends: { attributes: { vigor: 3 } } }));
    expect(onDone).toHaveBeenCalledWith("sword");
  });

  it("does not create the record until the name/type/budget steps commit; Cancel at the type step creates nothing", async () => {
    const createRecord = vi.fn().mockResolvedValue("sword");
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} onDone={vi.fn()} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
    fireEvent.click(screen.getByText("Next"));
    await screen.findByLabelText("Sheet type");

    fireEvent.click(screen.getByText("Cancel"));

    expect(createRecord).not.toHaveBeenCalled();
    expect(api.putSheetCreation).not.toHaveBeenCalled();
  });

  it("a type with no creation block skips straight to a budget-free create call", async () => {
    const createRecord = vi.fn().mockResolvedValue("shield");
    (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "plain", fields: {}, derived: {}, errors: [] } });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} onDone={onDone} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Shield" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "plain" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
      scope, "testmod", "items", "shield", { sheet_type: "plain", spends: {} }));
    expect(onDone).toHaveBeenCalledWith("shield");
  });

  it("blocks spending over a pool's budget", async () => {
    const createRecord = vi.fn().mockResolvedValue("sword");
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} onDone={vi.fn()} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
    fireEvent.click(screen.getByText("Next"));
    const createButton = screen.getByText("Create") as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("Vigor"), { target: { value: "10" } });
    expect(createButton.disabled).toBe(true);
  });

  it("number fields with a nonzero min floor the pool spend at min, not 0", async () => {
    // "strength" is a number field with min: 1 (mirrors d20-basic's adept
    // creation pool) -- an untouched field resolves server-side to its min,
    // and spend is measured from that floor, not from 0.
    const numberModule = {
      ...module,
      sheets: {
        groups: {
          attributes: {
            label: "Attributes",
            fields: [{ key: "strength", label: "Strength", type: "number", min: 1, max: 20 }],
          },
        },
        sheet_types: {
          hero: {
            label: "Hero", kind: "items", groups: ["attributes"], fields: [],
            // Naive floor=0 math would price a raise to 3 at (3-0)*4 = 12 > budget 10
            // and disable Create; the correct floor=1 math prices it at
            // (3-1)*4 = 8, within budget.
            creation: { pools: { attributes: { budget: 10, costs: { strength: 4 } } } },
          },
        },
      },
    } as any;
    const createRecord = vi.fn().mockResolvedValue("elara");
    (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "hero", fields: {}, derived: {}, errors: [] } });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="items" module={numberModule}
                           createRecord={createRecord} onDone={onDone} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Elara" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "hero" } });
    fireEvent.click(screen.getByText("Next"));

    const input = screen.getByLabelText("Strength") as HTMLInputElement;
    expect(input.value).toBe("1"); // floor, not 0
    expect(screen.getByText(/0 \/ 10/)).toBeInTheDocument(); // spend at rest is 0, not (1-0)*4=4

    fireEvent.change(input, { target: { value: "3" } });
    expect(screen.getByText(/8 \/ 10/)).toBeInTheDocument(); // (3-1)*4, not (3-0)*4=12

    const createButton = screen.getByText("Create") as HTMLButtonElement;
    expect(createButton.disabled).toBe(false);
    fireEvent.click(createButton);

    await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
      scope, "testmod", "items", "elara", { sheet_type: "hero", spends: { attributes: { strength: 3 } } }));
    expect(onDone).toHaveBeenCalledWith("elara");
  });

  it("kind='pcs' finds sheet types declared with kind='characters' (typeKind mapping)", async () => {
    const pcModule = {
      ...module,
      sheets: {
        groups: {},
        sheet_types: { hero: { label: "Hero", kind: "characters", groups: [], fields: [] } },
      },
    };
    const createRecord = vi.fn().mockResolvedValue("elara");
    (api.putSheetCreation as any).mockResolvedValue({ sheet: { sheet_type: "hero", fields: {}, derived: {}, errors: [] } });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="pcs" module={pcModule as any}
                           createRecord={createRecord} onDone={onDone} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Elara" } });
    fireEvent.click(screen.getByText("Next"));
    const select = await screen.findByLabelText("Sheet type");
    expect(within(select).getByText("Hero")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "hero" } });
    fireEvent.click(screen.getByText("Create"));
    await waitFor(() => expect(api.putSheetCreation).toHaveBeenCalledWith(
      scope, "testmod", "pcs", "elara", { sheet_type: "hero", spends: {} }));
    expect(onDone).toHaveBeenCalledWith("elara");
  });

  it("rolls back the created record when putSheetCreation fails, then shows the error banner", async () => {
    const createRecord = vi.fn().mockResolvedValue("sword");
    const deleteRecord = vi.fn().mockResolvedValue(undefined);
    (api.putSheetCreation as any).mockRejectedValue({ detail: "budget rejected" });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord} deleteRecord={deleteRecord}
                           onDone={onDone} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "plain" } });
    fireEvent.click(screen.getByText("Create"));

    await waitFor(() => expect(deleteRecord).toHaveBeenCalledWith("sword"));
    expect(await screen.findByText("budget rejected")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("degrades gracefully when no deleteRecord is provided: still shows the error, doesn't throw", async () => {
    const createRecord = vi.fn().mockResolvedValue("sword");
    (api.putSheetCreation as any).mockRejectedValue({ detail: "budget rejected" });
    const onDone = vi.fn();
    render(<CreationWizard scope={scope} kind="items" module={module}
                           createRecord={createRecord}
                           onDone={onDone} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sword" } });
    fireEvent.click(screen.getByText("Next"));
    fireEvent.change(await screen.findByLabelText("Sheet type"), { target: { value: "plain" } });
    fireEvent.click(screen.getByText("Create"));

    expect(await screen.findByText("budget rejected")).toBeInTheDocument();
    expect(onDone).not.toHaveBeenCalled();
  });
});
