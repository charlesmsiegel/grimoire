# Scene workspace

## Local reference intent (070)

The chat reference-intent panel stages a sketch or a typed description beside the existing composer. Import a PNG, JPEG or WebP of at most 2 MiB and 4096 × 4096 decoded pixels. No external image URL is fetched. Choose rectangle or ellipse and use the labeled normalized x, y, width and height fields to select a contained positive region; the overlay previews those coordinates. Typed intention and public region description work without an image.

Source/provenance, rights/permission and private notes/corrections stay with the local annotation. **Use as draft** appends only the reviewed action and public region description to the current composer through its normal input event. It neither posts nor changes a map, actor, canonical record or evidence. Ordinary posting sends composer text; the image and annotation metadata remain local. Repeating an unchanged intent leaves the player’s edited composer alone. Cancel closes the panel without changing the composer. Clear reference discards the retained annotation and image without changing the composer.

References are unsynced tab-memory drafts, scoped to the exact chat reference. They survive leaving and revisiting within the tab and disappear on reload. At most ten chats may retain annotations and one validated image per chat; clear an existing annotation to make room. No localStorage/sessionStorage image persistence or provider/backend request occurs. Preview object URLs are revoked on replacement, clear and page abort; revisiting recreates a preview from the retained file. Invalid import, invalid region and late cancelled decoding cannot replace a valid image or overwrite composer text. Import/decode failures and capacity errors appear in the panel’s live status.

This slice performs no image interpretation or inferred canonical acceptance. Sync/revision integration for annotations, inferred map positions/objects, gesture and broader campaign workspace integration remain later owners.
