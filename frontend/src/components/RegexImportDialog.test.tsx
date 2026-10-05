import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RegexImportDialog } from "./RegexImportDialog";
import { api } from "../api/client";
import type { RegexImportRow } from "../api/client";

vi.mock("../api/client", () => ({
  api: { previewRegexImport: vi.fn(), importRegex: vi.fn() },
}));

const ruleOf = (name: string) => ({
  name, enabled: true, pattern: "x", flags: "ga", replacement: "", trim: [],
  targets: ["model" as const], applies: ["display" as const, "prompt" as const],
  rewrite_stored: false, min_depth: null, max_depth: null, imported: null,
});

const ROWS: RegexImportRow[] = [
  { index: 0, name: "Strip asides", verdict: "exact", notes: [], rule: ruleOf("Strip asides"),
    original: { scriptName: "Strip asides", findRegex: "/\\(OOC:[^)]*\\)/g" } },
  { index: 1, name: "Edge lines", verdict: "approximate", notes: ["Line breaks differ at CR."],
    rule: ruleOf("Edge lines"), original: { scriptName: "Edge lines", findRegex: "/^a/m" } },
  { index: 2, name: "Unicode names", verdict: "untranslatable", notes: ["Property escapes are not supported."],
    rule: null, original: { scriptName: "Unicode names", findRegex: "/\\p{L}+/gu" } },
];

const GLOBAL = { kind: "global" as const };

const wrap = (ui: React.ReactElement) => render(ui);

function pick(text: string) {
  const file = new File([text], "regex.json", { type: "application/json" });
  fireEvent.change(screen.getByLabelText("SillyTavern regex file"), { target: { files: [file] } });
}

beforeEach(() => {
  vi.mocked(api.previewRegexImport).mockReset().mockResolvedValue({ rows: ROWS });
  vi.mocked(api.importRegex).mockReset().mockResolvedValue({
    layer: { rules: [], off: [] }, inherited: [], warnings: {}, added: ["r-1"],
  });
});

test("import preview lists untranslatable rows disabled with notes", async () => {
  wrap(<RegexImportDialog scope={GLOBAL} onDone={() => {}} onClose={() => {}} />);
  pick(JSON.stringify([{ scriptName: "Strip asides" }]));
  expect(await screen.findByText("Unicode names")).toBeInTheDocument();
  expect(api.previewRegexImport).toHaveBeenCalledWith([{ scriptName: "Strip asides" }]);
  const bad = screen.getByRole("checkbox", { name: "Import Unicode names" });
  expect(bad).toBeDisabled();
  expect(bad).not.toBeChecked();
  expect(screen.getByText("Property escapes are not supported.")).toBeInTheDocument();
  expect(screen.getByText("/\\p{L}+/gu")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", { name: "Import Strip asides" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Import Edge lines" })).toBeChecked();
  expect(screen.getByText("Line breaks differ at CR.")).toBeInTheDocument();
});

test("Import selected sends only checked rows", async () => {
  const onDone = vi.fn();
  wrap(<RegexImportDialog scope={{ kind: "world", wid: "realm" }} onDone={onDone} onClose={() => {}} />);
  pick("[]");
  await screen.findByText("Edge lines");
  fireEvent.click(screen.getByRole("checkbox", { name: "Import Edge lines" }));
  fireEvent.click(screen.getByRole("button", { name: "Import selected" }));
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(api.importRegex).toHaveBeenCalledWith({ kind: "world", wid: "realm" }, [ROWS[0].rule]);
});

test("invalid JSON shows an error and calls nothing", async () => {
  wrap(<RegexImportDialog scope={GLOBAL} onDone={() => {}} onClose={() => {}} />);
  pick("{ not json");
  expect(await screen.findByRole("alert")).toHaveTextContent(/not valid JSON/i);
  expect(api.previewRegexImport).not.toHaveBeenCalled();
  expect(api.importRegex).not.toHaveBeenCalled();
});

test("Import selected is disabled when nothing is checked", async () => {
  wrap(<RegexImportDialog scope={GLOBAL} onDone={() => {}} onClose={() => {}} />);
  pick("[]");
  await screen.findByText("Edge lines");
  fireEvent.click(screen.getByRole("checkbox", { name: "Import Strip asides" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Import Edge lines" }));
  expect(screen.getByRole("button", { name: "Import selected" })).toBeDisabled();
});

test("a refused import is shown and the dialog stays open", async () => {
  const onDone = vi.fn();
  vi.mocked(api.importRegex).mockRejectedValueOnce(new Error("rows: nothing to import"));
  wrap(<RegexImportDialog scope={GLOBAL} onDone={onDone} onClose={() => {}} />);
  pick("[]");
  await screen.findByText("Edge lines");
  fireEvent.click(screen.getByRole("button", { name: "Import selected" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("nothing to import");
  expect(onDone).not.toHaveBeenCalled();
});

test("Escape closes it", async () => {
  const onClose = vi.fn();
  wrap(<RegexImportDialog scope={GLOBAL} onDone={() => {}} onClose={onClose} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
});

test("Import selected is held while the level is being saved", async () => {
  wrap(<RegexImportDialog scope={GLOBAL} held onDone={() => {}} onClose={() => {}} />);
  pick("[]");
  await screen.findByText("Edge lines");
  expect(screen.getByRole("button", { name: "Import selected" })).toBeDisabled();
});
