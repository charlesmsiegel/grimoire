import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BackupsPanel, formatSize } from "./BackupsPanel";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    constructor(public status: number, public detail: string) { super(detail); }
  },
  api: { listBackups: vi.fn(), createBackup: vi.fn() },
}));
import { ApiError, api } from "../api/client";

const listing = {
  dir: "/home/u/.grimoire/backups",
  backups: [
    { name: "grimoire-20260815T210000Z.zip", size: 5_242_880, created: "2026-08-15T21:00:00Z" },
    { name: "grimoire-20260814T210000Z.zip", size: 5_000_000, created: "2026-08-14T21:00:00Z" },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.listBackups as any).mockResolvedValue(listing);
});

test("shows where the archives live and lists them newest first", async () => {
  render(<BackupsPanel dir="" />);

  expect(await screen.findByLabelText(/current backup folder/i))
    .toHaveValue("/home/u/.grimoire/backups");
  const names = [...document.querySelectorAll(".backup-name")].map((n) => n.textContent);
  expect(names).toEqual(["grimoire-20260815T210000Z.zip", "grimoire-20260814T210000Z.zip"]);
  expect(screen.getByText(/5\.0 MB/)).toBeInTheDocument();
});

test("a store with no archives says so rather than showing an empty box", async () => {
  (api.listBackups as any).mockResolvedValue({ dir: "/x/backups", backups: [] });
  render(<BackupsPanel dir="" />);

  expect(await screen.findByText("No backups yet.")).toBeInTheDocument();
  expect(document.querySelector(".backup-list")).toBeNull();
});

test("a folder that cannot be read is reported, never shown as no backups", async () => {
  (api.listBackups as any).mockRejectedValue(new ApiError(500, "could not list backups: denied"));
  render(<BackupsPanel dir="" />);

  expect(await screen.findByText(/could not list backups: denied/)).toBeInTheDocument();
  expect(screen.queryByText("No backups yet.")).toBeNull();
});

test("Back up now adopts the response as the new listing", async () => {
  (api.createBackup as any).mockResolvedValue({
    dir: "/home/u/.grimoire/backups",
    created: "grimoire-20260816T090000Z.zip",
    swept: [],
    backups: [{ name: "grimoire-20260816T090000Z.zip", size: 5_300_000,
                created: "2026-08-16T09:00:00Z" }],
  });
  render(<BackupsPanel dir="" />);
  fireEvent.click(await screen.findByRole("button", { name: /back up now/i }));

  expect(await screen.findByText("Backed up to grimoire-20260816T090000Z.zip"))
    .toBeInTheDocument();
  expect(screen.getByText("grimoire-20260816T090000Z.zip")).toBeInTheDocument();
  expect(api.listBackups).toHaveBeenCalledTimes(1);
});

test("retention is reported, because nobody sees a file get deleted", async () => {
  (api.createBackup as any).mockResolvedValue({
    dir: "/home/u/.grimoire/backups",
    created: "grimoire-20260816T090000Z.zip",
    swept: ["grimoire-20260814T210000Z.zip", "grimoire-20260815T210000Z.zip"],
    backups: [{ name: "grimoire-20260816T090000Z.zip", size: 5_300_000,
                created: "2026-08-16T09:00:00Z" }],
  });
  render(<BackupsPanel dir="" />);
  fireEvent.click(await screen.findByRole("button", { name: /back up now/i }));

  expect(await screen.findByText(/2 older archives removed/)).toBeInTheDocument();
});

test("a failed backup says why and leaves the previous listing standing", async () => {
  (api.createBackup as any).mockRejectedValue(new ApiError(500, "no space left on device"));
  render(<BackupsPanel dir="" />);
  fireEvent.click(await screen.findByRole("button", { name: /back up now/i }));

  expect(await screen.findByText(/no space left on device/)).toBeInTheDocument();
  expect(screen.getByText("grimoire-20260815T210000Z.zip")).toBeInTheDocument();
});

test("the button is held while a backup is in flight", async () => {
  let release: (v: unknown) => void = () => {};
  (api.createBackup as any).mockReturnValue(new Promise((r) => { release = r; }));
  render(<BackupsPanel dir="" />);
  const button = await screen.findByRole("button", { name: /back up now/i });
  fireEvent.click(button);

  expect(await screen.findByRole("button", { name: /backing up/i })).toBeDisabled();

  release({ ...listing, created: "grimoire-20260816T090000Z.zip", swept: [] });
  await waitFor(() => expect(api.createBackup).toHaveBeenCalledTimes(1));
});

test("a newly saved backup folder re-reads the listing", async () => {
  const { rerender } = render(<BackupsPanel dir="" />);
  await waitFor(() => expect(api.listBackups).toHaveBeenCalledTimes(1));

  rerender(<BackupsPanel dir="/mnt/usb" />);

  await waitFor(() => expect(api.listBackups).toHaveBeenCalledTimes(2));
});

test("sizes read the way a file manager shows them", () => {
  expect(formatSize(0)).toBe("0 B");
  expect(formatSize(999)).toBe("999 B");
  expect(formatSize(1024)).toBe("1.0 KB");
  expect(formatSize(1_572_864)).toBe("1.5 MB");
  expect(formatSize(3 * 1024 ** 3)).toBe("3.0 GB");
});
