# Campaign EPUB export — design

2026-07-06. Approved via brainstorming.

## Goal

Export a whole campaign as an EPUB 3 book: a typographic title page, one chapter
per scene, embedded images and fonts, and an appendix with an entry for every
entity that appeared (cast actors and visited locations).

## Architecture

New store module `backend/src/grimoire/store/epub.py` with one public function:

```python
def build_epub(cid: str) -> tuple[bytes, str]:   # (epub bytes, suggested filename)
```

Raises `campaigns.CampaignNotFound` for a bad id. Nothing is written into the
store; the book is built in memory and returned.

Data flow:

1. **Gather** — `campaigns.read_campaign` (title, world), `scenes.list_scenes`
   sorted by scene id **ascending** (scene-number order — not the updated-desc
   order the API uses), `scenes.read_scene` per scene, `appearances` for cast,
   `entities.read_entity` for visited locations, the campaign's primary calendar
   provider for friendly dates.
2. **Convert** — each markdown body goes through the `markdown` package
   (`output_format="xhtml"`, `tables` extension to mirror remark-gfm) into an
   XHTML fragment.
3. **Render** — Jinja templates in `<repo>/templates/epub/` (loaded through the
   existing `prompts` Jinja environment) produce `container.xml`, `package.opf`,
   `nav.xhtml`, `titlepage.xhtml`, `chapter.xhtml`, `appendix-actor.xhtml`,
   `appendix-location.xhtml`, and `stylesheet.css`.
4. **Pack** — stdlib `zipfile`: `mimetype` first and STORED (spec requirement),
   everything else DEFLATED.

Book metadata: `dc:title` = campaign name, `dc:language` = `en`,
`dc:identifier` = `urn:grimoire:campaign:<cid>`, `dc:date` = campaign `updated`.
Filename: `<cid>.epub`.

New backend dependency: `markdown>=3.5` (pure Python).

## Book structure

**Spine:** title page → chapters in scene-number order → appendix divider →
actor entries (players first, then NPCs, alphabetical within each) → location
entries (alphabetical by name). The nav has two top-level sections, *Scenes*
and *Appendix*.

**Title page:** campaign name (Cinzel), world name, and the in-world date range
(first scene's start date — last scene's latest date, friendly form) when dates
exist.

**Chapter header:** scene title as heading; a metadata block with the in-world
start date (friendly form of `time_history[0]`), starting location name
(`location_history[0]` resolved via `entities.read_entity`; silently omitted if
deleted), and cast names from `appearances.scene_cast` (plain text, no internal
links in v1). An absorbed `one_line` renders as an italic epigraph between
header and body.

**Transcript (styled):**
- Narrator messages (no speaker) render as plain prose.
- Named messages get the speaker's name as a small-caps run-in label
  (`<span class="speaker">`) on the first paragraph, message markdown rendered
  after it.
- App-written transition lines (`*The scene moves to …*`, `*Time passes…*`) are
  narrator messages and render as the italic asides they already are.

**Appendix entries:**
- *Character:* avatar (if present) at the top, name as heading, then
  **Description / Personality / Scenario** as labeled markdown sections (the
  `cast_detail` field set). Prompt plumbing (first message, examples, system
  prompt, lorebook) is excluded.
- *PC:* name as heading, persona summary and description.
- *Location:* name as heading, avatar if present, full entity body.

**Appendix scope:** actors present in `appearances.json` (at their locked
version, from the campaign copy) plus locations occurring in any scene's
`location_history`. Lore is omitted — it never "appears" in a scene.

## Images

Bodies reference images through localized app URLs
(`/api/worlds/{wid}/greetings/{gid}/images/{name}`,
`/api/campaigns/{cid}/{kind}/{eid}/images/{name}`, character-version URLs, …).
One resolver pattern-matches these URL shapes and maps each to a disk file via
`assets.image_path` — campaign tree first, then the campaign's world (greeting
images only live world-side). Per body, before markdown conversion, each
matched URL is rewritten to a book-internal path.

- Each distinct resolved file is packed once as `images/img-<n>.<ext>` and
  manifested; duplicates share the entry.
- Remote `http(s)` URLs and refs whose file is missing degrade to alt text.
- Appendix portraits: `assets.image_path(..., AVATAR)` in the campaign copy,
  falling back to the world copy.
- No cover image — campaigns have no cover-art field today.

## Fonts

Static TTFs vendored at `backend/src/grimoire/assets/fonts/` (package data),
committed with their OFL license file:

- EB Garamond Regular / Italic / SemiBold — body text
- Cinzel SemiBold — title page, chapter headings, speaker labels

TTF (not WOFF2) because older readers don't reliably support WOFF2 in EPUBs.
~2 MB per export, accepted. `stylesheet.css` owns the `@font-face` rules; no
font obfuscation (OFL permits embedding).

## API and UI

**Route:** `GET /api/campaigns/{cid}/export.epub` →
`store.epub.build_epub(cid)` returned as `Response` with
`media_type="application/epub+zip"` and
`Content-Disposition: attachment; filename="<cid>.epub"`. `CampaignNotFound`
→ 404. Synchronous build — no job/streaming machinery.

**UI:** an "Export EPUB" action on the campaign page — a plain
`<a href="/api/campaigns/<cid>/export.epub" download>` styled as a button,
placed with the campaign's other page-level actions per the existing layout.
No loading state; the browser handles the download.

## Error handling / edge cases

- Zero scenes: still exports (title page + appendix). Never an error.
- Scene missing messages / date / location: chapter renders with whatever
  header pieces exist.
- Actor whose locked version is unreadable: entry skipped; never fails the book.
- All file reads go through existing store functions, inheriting path safety.

## Known limitation

`markdown` passes raw inline HTML through untouched. In-app text never contains
raw HTML (react-markdown drops it, so nobody writes it), so v1 escapes nothing;
a hand-imported body containing raw HTML could yield non-valid XHTML in that
chapter. Most readers parse leniently. Accepted for v1.

## Testing

**Backend (pytest, `GRIMOIRE_HOME` tmp store):** fixture campaign — two scenes
with speakers/dates/locations/an image reference, a locked character with an
avatar, a PC, one visited and one unvisited location. Assert on the built bytes
via `zipfile` + `xml.etree`:

- `mimetype` is the first zip entry and STORED; `container.xml` points at the
  OPF; every manifest/spine ref exists in the zip.
- Chapters are in scene-number order; speaker labels and epigraph appear in the
  chapter XHTML.
- The referenced image is packed once and the body URL rewritten; fonts present.
- Appendix contains exactly the appeared actors and the visited location.

Route tests: 200 with the right media type; 404 for an unknown campaign.

**Frontend (vitest):** the campaign page renders the Export EPUB link with the
right href.
