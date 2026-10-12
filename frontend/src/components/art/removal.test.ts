import { ApiError } from "../../api/client";
import { IN_COLLECTION, removalError, removalQuestion, slotPhrase } from "./removal";

test("slotPhrase names the slot the way the panel does", () => {
  expect(slotPhrase("avatar", "Avatar")).toBe("avatar");
  expect(slotPhrase("avatar")).toBe("primary image");
  expect(slotPhrase("gallery_3")).toBe("gallery 3");
  expect(slotPhrase("cover_art")).toBe("cover art");
});

test("removalQuestion says what the server will do", () => {
  expect(removalQuestion({ surface: "record", scope: "world", record: "Mara", name: "gallery_3" }))
    .toBe("Remove Mara's gallery 3?");
  expect(removalQuestion({ surface: "record", scope: "campaign", record: "Mara", name: "gallery_3" }))
    .toBe("Hide Mara's gallery 3 only in this campaign? Any inherited world image in this slot is hidden too.");
  expect(removalQuestion({ surface: "campaign-library", inherited: true, name: "harbour" }))
    .toBe("Hide harbour in this campaign? The world keeps it.");
  expect(removalQuestion({ surface: "campaign-library", inherited: false, name: "harbour" }))
    .toBe("Remove harbour from this campaign's library?");
  expect(removalQuestion({ surface: "world-library", name: "harbour" }))
    .toBe("Delete harbour from the world library? Every campaign on this world loses it.");
});

test("removalQuestion reads the record's primary label", () => {
  expect(removalQuestion({ surface: "record", scope: "world", record: "Mara", name: "avatar", primaryLabel: "Avatar" }))
    .toBe("Remove Mara's avatar?");
});

test("removalError never shows a bare code", () => {
  expect(removalError(new ApiError(409, "image_in_collection"))).toBe(IN_COLLECTION);
  expect(removalError(new ApiError(409, "Some sentence.", "image_in_collection"))).toBe(IN_COLLECTION);
  expect(removalError(new ApiError(404, "That picture is gone.", "not_found"))).toBe("That picture is gone.");
  expect(removalError(new Error("boom"))).toBe("Could not remove this picture.");
  expect(removalError("nope")).toBe("Could not remove this picture.");
});
