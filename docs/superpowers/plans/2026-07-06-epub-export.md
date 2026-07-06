# Campaign EPUB Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export a campaign as an EPUB 3 book — title page, one chapter per scene, embedded images and fonts, appendix of appeared actors and visited locations — via `GET /api/campaigns/{cid}/export.epub` and an Export EPUB link on the campaign page.

**Architecture:** One new store module `backend/src/grimoire/store/epub.py` builds the whole book in memory: markdown bodies → XHTML fragments (the `markdown` package), pages rendered from Jinja templates in `<repo>/templates/epub/`, packed with stdlib `zipfile`. One route in `routes.py`, one anchor in `CampaignView.tsx`. Spec: `docs/superpowers/specs/2026-07-06-epub-export-design.md`.

**Tech Stack:** Python 3.11 / FastAPI / Jinja2 / new dep `markdown>=3.5`; stdlib `zipfile`, `io`, `re`; React + vitest on the frontend.

## Global Constraints

- Only new backend dependency: `markdown>=3.5` (pure Python). No ebooklib, no lxml.
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q` (run from repo root). Tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Frontend tests: run **from `frontend/`**: `npx vitest run` and `npx tsc -b` (never `npx --prefix`).
- EPUB spec invariants: `mimetype` is the FIRST zip entry and STORED (uncompressed); every spine/manifest href exists in the zip; nav item carries `properties="nav"`.
- The store module never writes into the grimoire store; `build_epub` returns bytes.
- All shell steps below use Git Bash syntax (the Bash tool), from the repo root unless stated.

---

### Task 1: Vendor fonts and add the `markdown` dependency

**Files:**
- Create: `backend/src/grimoire/assets/fonts/` (4 `.ttf` files + 2 OFL license files)
- Modify: `backend/pyproject.toml` (add `markdown>=3.5` to `dependencies`)
- Test: `backend/tests/test_epub_store.py` (new file)

**Interfaces:**
- Consumes: nothing.
- Produces: font files at `backend/src/grimoire/assets/fonts/{EBGaramond-Regular,EBGaramond-Italic,EBGaramond-SemiBold,Cinzel-SemiBold}.ttf` plus `OFL-EBGaramond.txt`, `OFL-Cinzel.txt`; importable `markdown` package. Task 3 reads the fonts dir; Task 2 imports `markdown`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_epub_store.py`:

```python
"""Campaign → EPUB export."""

from pathlib import Path

import grimoire

FONTS = Path(grimoire.__file__).parent / "assets" / "fonts"


def test_fonts_vendored():
    ttfs = sorted(p.name for p in FONTS.glob("*.ttf"))
    assert ttfs == ["Cinzel-SemiBold.ttf", "EBGaramond-Italic.ttf",
                    "EBGaramond-Regular.ttf", "EBGaramond-SemiBold.ttf"]
    for p in FONTS.glob("*.ttf"):
        assert p.stat().st_size > 50_000, p.name
        assert p.read_bytes()[:4] == b"\x00\x01\x00\x00", p.name  # TrueType sfnt magic
    assert (FONTS / "OFL-EBGaramond.txt").exists()
    assert (FONTS / "OFL-Cinzel.txt").exists()


def test_markdown_dependency():
    import markdown
    assert markdown.markdown("**hi**") == "<p><strong>hi</strong></p>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: FAIL — `test_fonts_vendored` (empty glob) and `test_markdown_dependency` (ModuleNotFoundError).

- [ ] **Step 3: Add the dependency**

In `backend/pyproject.toml`, in the `dependencies` list after `"jinja2>=3.1",` add:

```toml
    "markdown>=3.5",
```

Then install: `backend/.venv/Scripts/python.exe -m pip install "markdown>=3.5"`

- [ ] **Step 4: Fetch the fonts**

Write this to the scratchpad (NOT the repo) as `fetch_fonts.py` and run it with `backend/.venv/Scripts/python.exe <scratchpad>/fetch_fonts.py` from the repo root:

```python
"""One-off: fetch static TTFs + OFL licenses for the EPUB export.

Google's css2 API serves static TTF URLs to clients whose User-Agent it does
not recognize as woff2-capable (an empty UA qualifies).
"""
import re
import urllib.request
from pathlib import Path

DEST = Path("backend/src/grimoire/assets/fonts")
CSS = [
    ("https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,600;1,400", "EBGaramond"),
    ("https://fonts.googleapis.com/css2?family=Cinzel:wght@600", "Cinzel"),
]
OFL = [
    ("https://raw.githubusercontent.com/google/fonts/main/ofl/ebgaramond/OFL.txt", "OFL-EBGaramond.txt"),
    ("https://raw.githubusercontent.com/google/fonts/main/ofl/cinzel/OFL.txt", "OFL-Cinzel.txt"),
]
SUFFIX = {"400normal": "Regular", "400italic": "Italic", "600normal": "SemiBold"}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ""})
    return urllib.request.urlopen(req).read()


DEST.mkdir(parents=True, exist_ok=True)
for url, family in CSS:
    css = get(url).decode()
    assert ".ttf" in css, f"css2 did not serve TTF for {family}; see fallback note"
    for block in re.findall(r"@font-face\s*\{[^}]*\}", css):
        style = re.search(r"font-style:\s*(\w+)", block).group(1)
        weight = re.search(r"font-weight:\s*(\d+)", block).group(1)
        src = re.search(r"url\((https://[^)]+\.ttf)\)", block).group(1)
        name = f"{family}-{SUFFIX[weight + style]}.ttf"
        data = get(src)
        (DEST / name).write_bytes(data)
        print(name, len(data))
for url, name in OFL:
    (DEST / name).write_bytes(get(url))
    print(name)
```

Expected output: four `.ttf` lines (each > 50000 bytes) and two OFL lines.

*Fallback if the assert fires (css2 served woff2):* download the variable TTFs from the google/fonts repo instead — `https://github.com/google/fonts/raw/main/ofl/ebgaramond/EBGaramond%5Bwght%5D.ttf` (save as both `EBGaramond-Regular.ttf`; for Italic use `EBGaramond-Italic%5Bwght%5D.ttf`; for SemiBold copy the Regular file) and `https://github.com/google/fonts/raw/main/ofl/cinzel/Cinzel%5Bwght%5D.ttf` as `Cinzel-SemiBold.ttf`. Variable TTFs render at their default instance in readers that ignore the weight axis, which is acceptable.

- [ ] **Step 5: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/assets/fonts backend/pyproject.toml backend/tests/test_epub_store.py
git commit -m "feat(epub): vendor EB Garamond + Cinzel TTFs, add markdown dependency"
```

---

### Task 2: `store/epub.py` helpers — markdown conversion, speaker labels, image resolution

**Files:**
- Create: `backend/src/grimoire/store/epub.py`
- Modify: `backend/src/grimoire/store/__init__.py` (import + `__all__`)
- Test: `backend/tests/test_epub_store.py` (append)

**Interfaces:**
- Consumes: `assets.image_path(root, cid, vid, name, base=...) -> Path | None`; `prompts.TEMPLATES_DIR`.
- Produces (used by Tasks 3–4):
  - `_md(text: str) -> str` — markdown → XHTML fragment (tables extension).
  - `_message_html(speaker: str | None, content: str) -> str` — one message's fragment; named messages get `<span class="speaker">Name</span>` run into the first paragraph.
  - `class _Images` with `add(p: Path) -> str` (registry: disk path → `img-NNN.ext`) and `by_path: dict[Path, str]`.
  - `_rewrite_images(text: str, croot: Path, wroot: Path | None, images: _Images) -> str` — markdown-source rewrite: localized app URLs → `../images/img-NNN.ext`; remote/missing → alt text.
  - `_render(name: str, **vars) -> str` — Jinja render of `templates/epub/<name>` with `autoescape=True`.
  - Module constant `FONTS_DIR`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_epub_store.py`:

```python
from grimoire.store import assets, campaigns, epub, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run One", wid)
    return wid, cid


def test_message_html_speaker_label():
    html = epub._message_html("Seraphine", "\"Welcome,\" she says.")
    assert html.startswith('<p><span class="speaker">Seraphine</span> ')
    assert epub._message_html(None, "The docks reek.") == "<p>The docks reek.</p>"
    # speaker names are escaped
    assert "&lt;b&gt;" in epub._message_html("<b>", "hi")


def test_rewrite_images_maps_local_and_drops_remote(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(wid)
    from grimoire.store import entities
    docks = entities.create_entity(croot, "locations", "The Docks", body="piers")
    assets.put_image(croot, docks, "default", "pier", b"pierbytes", "png", base="locations")
    images = epub._Images()
    text = (f"Look: ![The docks](/api/campaigns/{cid}/locations/{docks}/images/pier) "
            "and ![lost](https://example.com/x.png)")
    out = epub._rewrite_images(text, croot, wroot, images)
    assert "![The docks](../images/img-000.png)" in out
    assert "lost" in out and "example.com" not in out
    # same file referenced again reuses the entry
    epub._rewrite_images(f"![again](/api/campaigns/{cid}/locations/{docks}/images/pier)",
                         croot, wroot, images)
    assert len(images.by_path) == 1


def test_rewrite_images_world_fallback(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(wid)
    # greeting images only ever live world-side
    assets.put_image(wroot, "g1", "default", "vista", b"vistabytes", "jpg", base="greetings")
    images = epub._Images()
    out = epub._rewrite_images(f"![v](/api/worlds/{wid}/greetings/g1/images/vista)",
                               croot, wroot, images)
    assert "![v](../images/img-000.jpg)" in out
    # missing file degrades to alt text
    out2 = epub._rewrite_images(f"![gone](/api/worlds/{wid}/greetings/g1/images/nope)",
                                croot, wroot, images)
    assert out2 == "gone"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'epub'`.

- [ ] **Step 3: Write the module**

Create `backend/src/grimoire/store/epub.py`:

```python
"""Campaign → EPUB 3 export: one chapter per scene, embedded images and fonts,
and an appendix for every entity that appeared (cast actors + visited locations).

The book is assembled in memory: markdown bodies become XHTML fragments (the
`markdown` package), pages render from Jinja templates in <repo>/templates/epub/,
and everything packs with stdlib zipfile — `mimetype` first and uncompressed,
per the EPUB OCF spec. Nothing is written into the store.
"""

from __future__ import annotations

import functools
import io
import re
import zipfile
from pathlib import Path

import markdown as _md_lib
from markupsafe import Markup, escape

from . import appearances, assets, calendars, campaigns, characters, entities, pcs, scenes, worlds
from ..prompts import TEMPLATES_DIR
from .paths import now_iso

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

_EXT_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
              "gif": "image/gif", "webp": "image/webp"}


@functools.lru_cache(maxsize=1)
def _env():
    # A separate environment from prompts._env: book pages need autoescape.
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)),
                       undefined=StrictUndefined, autoescape=True)


def _render(name: str, **vars) -> str:
    return _env().get_template(f"epub/{name}").render(**vars)


def _md(text: str) -> str:
    return _md_lib.markdown(text, extensions=["tables"], output_format="xhtml")


def _message_html(speaker: str | None, content: str) -> str:
    """One message as an XHTML fragment; a named message carries its speaker
    as a run-in label on the first paragraph."""
    html = _md(content)
    if speaker is None:
        return html
    label = f'<span class="speaker">{escape(speaker)}</span> '
    if html.startswith("<p>"):
        return "<p>" + label + html[3:]
    return f"<p>{label}</p>\n{html}"


# Localized app image URLs (see store.localize): every shape the app writes.
_IMG_URL = re.compile(
    r"/api/(?:worlds|campaigns)/[^/\s]+/(?:"
    r"characters/(?P<char>[^/\s]+)/versions/(?P<vid>[^/\s]+)"
    r"|greetings/(?P<gid>[^/\s]+)"
    r"|(?P<kind>locations|lore)/(?P<eid>[^/\s]+)"
    r")/images/(?P<name>[^/\s?#]+)")

_MD_IMG = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")


class _Images:
    """Registry of packed images: disk path -> zip-internal images/ name."""

    def __init__(self):
        self.by_path: dict[Path, str] = {}

    def add(self, p: Path) -> str:
        if p not in self.by_path:
            self.by_path[p] = f"img-{len(self.by_path):03d}{p.suffix.lower()}"
        return self.by_path[p]


def _resolve_image(croot: Path, wroot: Path | None, m: re.Match) -> Path | None:
    """Map a localized app URL to a disk file: campaign tree first, then the
    campaign's world (greeting images only live world-side)."""
    if m["char"]:
        rid, vid, base = m["char"], m["vid"], "characters"
    elif m["gid"]:
        rid, vid, base = m["gid"], "default", "greetings"
    else:
        rid, vid, base = m["eid"], "default", m["kind"]
    for root in (croot, wroot):
        if root is None:
            continue
        p = assets.image_path(root, rid, vid, m["name"], base=base)
        if p is not None:
            return p
    return None


def _rewrite_images(text: str, croot: Path, wroot: Path | None, images: _Images) -> str:
    """Point every markdown image at its packed copy; remote or missing images
    degrade to their alt text (readers can't fetch, and a broken img is worse)."""
    def sub(m: re.Match) -> str:
        app = _IMG_URL.match(m["url"])
        if app:
            p = _resolve_image(croot, wroot, app)
            if p is not None:
                return f"![{m['alt']}](../images/{images.add(p)})"
        return m["alt"]
    return _MD_IMG.sub(sub, text)
```

(`io`, `zipfile`, `Markup`, `now_iso`, and most store imports are consumed by Tasks 3–4; keep them.)

- [ ] **Step 4: Register the module in `store/__init__.py`**

In `backend/src/grimoire/store/__init__.py`, the `from . import (...)` block lists modules alphabetically — add `epub` between `entities` and `fetch`:

```python
from . import (
    absorb, appearances, assets, campaigns, cards, changes, characters, chronicle,
    chub, context, dossiers, entities, epub, fetch, greetings, image_subjects, localize,
    lorebook, migrations, pcs, playing, playstate, plot, relationships, scene_ids,
    scene_refs, scenes, suggest, sync, tags, taglines, thumbs, worlds,
)
```

And add `"epub",` to `__all__` (after `"entities",`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: all pass. Ruff may flag the not-yet-used imports (`io`, `zipfile`, `Markup`, …); if the project lints on commit and it blocks, add `# noqa: F401` markers and remove them in Task 3.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/epub.py backend/src/grimoire/store/__init__.py backend/tests/test_epub_store.py
git commit -m "feat(epub): markdown conversion, speaker labels, image URL resolution"
```

---

### Task 3: Templates + `build_epub` — title page, chapters, fonts, CSS, zip

**Files:**
- Create: `templates/epub/container.xml`, `templates/epub/package.opf`, `templates/epub/nav.xhtml`, `templates/epub/titlepage.xhtml`, `templates/epub/chapter.xhtml`, `templates/epub/divider.xhtml`, `templates/epub/appendix.xhtml`, `templates/epub/stylesheet.css`
- Modify: `backend/src/grimoire/store/epub.py` (add `_chapter`, `_friendly_or_none`, `build_epub`; appendix stubbed to `[]`), `templates/README.md`
- Test: `backend/tests/test_epub_store.py` (append)

**Interfaces:**
- Consumes: Task 2 helpers; `scenes.list_scenes/read_scene/get_time_history/get_location_history`; `appearances.scene_cast`; `calendars.read_calendar/get_provider/friendly`; `entities.read_entity`; `worlds.read_world`; `campaigns.read_campaign/campaign_root`.
- Produces: `build_epub(cid: str) -> tuple[bytes, str]` (raises `campaigns.CampaignNotFound`); `_chapter(...) -> {"file","title","doc"}`; `_appendix_entries(cid, croot, wroot, sids, images) -> list[dict]` exists as a stub returning `[]` (Task 4 fills it). Template `appendix.xhtml` (rendered only by Task 4 code) takes `name`, `portrait` (internal image name or None), `role` (str or None), `sections` (list of `{"label": str|None, "html": Markup}`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_epub_store.py`:

```python
import io
import xml.etree.ElementTree as ET
import zipfile

from grimoire.store import appearances, characters, entities, pcs, scenes

OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}


def _fixture_campaign(monkeypatch, tmp_path):
    """World + campaign with 2 scenes, cast, dates, locations, images, epigraph."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    wroot = worlds.world_root(wid)
    card = characters.blank_card("Seraphine")
    card["data"]["description"] = "the drowned keeper"
    card["data"]["personality"] = "grim"
    characters.create_character(wroot, "Seraphine", "default", card)
    pcs.create_pc(wroot, "Elara", [], persona={"name": "Elara", "pronouns": "she/her",
                                               "summary": "scholar", "description": "A wanderer."})
    cid = campaigns.create_campaign("Run One", wid)
    croot = campaigns.campaign_root(cid)
    docks = entities.create_entity(croot, "locations", "The Docks", body="Salt-stained piers.")
    entities.create_entity(croot, "locations", "The Keep", body="Never visited.")
    assets.put_image(croot, docks, "default", "pier", b"pierbytes", "png", base="locations")

    sid1 = scenes.create_scene(cid, "Arrival")
    appearances.appear(cid, sid1, "pcs", "elara", "default", "player")
    appearances.appear(cid, sid1, "characters", "seraphine", "default", "npc")
    scenes.append_message(cid, sid1, "user", "I step off the boat.", speaker="Elara")
    scenes.append_message(cid, sid1, "assistant",
                          f"The docks reek. ![The docks](/api/campaigns/{cid}/locations/{docks}/images/pier) "
                          "![lost](https://example.com/x.png)")
    scenes.append_message(cid, sid1, "assistant", "\"Welcome,\" she says.", speaker="Seraphine")
    sid1 = scenes.set_datetime(cid, sid1, "2025-03-01")["id"]  # renames; appearances repoint
    scenes.set_location(cid, sid1, docks)
    scenes.mark_absorbed(cid, sid1, "They arrive.", "A long summary.")

    sid2 = scenes.create_scene(cid, "Below")
    scenes.append_message(cid, sid2, "assistant", "Deeper still.")
    return wid, cid, sid1, sid2


def _open(blob: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(blob))


def test_build_epub_ocf_shape(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, filename = epub.build_epub(cid)
    assert filename == f"{cid}.epub"
    z = _open(blob)
    infos = z.infolist()
    assert infos[0].filename == "mimetype"
    assert infos[0].compress_type == zipfile.ZIP_STORED
    assert z.read("mimetype") == b"application/epub+zip"
    assert "full-path=\"package.opf\"" in z.read("META-INF/container.xml").decode()


def test_build_epub_manifest_and_spine_complete(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    opf = ET.fromstring(z.read("package.opf"))
    items = opf.findall(".//opf:item", OPF_NS)
    by_id = {i.get("id"): i for i in items}
    for i in items:
        assert i.get("href") in z.namelist(), i.get("href")
    for ref in opf.findall(".//opf:itemref", OPF_NS):
        assert ref.get("idref") in by_id
    navs = [i for i in items if i.get("properties") == "nav"]
    assert len(navs) == 1 and navs[0].get("href") == "nav.xhtml"
    title = opf.find(".//{http://purl.org/dc/elements/1.1/}title")
    assert title.text == "Run One"


def test_build_epub_chapters_in_scene_order_with_meta(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    ch1 = z.read("text/chapter-001.xhtml").decode()
    ch2 = z.read("text/chapter-002.xhtml").decode()
    assert "Arrival" in ch1 and "Below" in ch2
    assert '<span class="speaker">Seraphine</span>' in ch1
    assert '<span class="speaker">Elara</span>' in ch1
    assert "The docks reek." in ch1
    assert 'class="epigraph"' in ch1 and "They arrive." in ch1
    assert 'class="scene-date"' in ch1
    assert "The Docks" in ch1          # location line
    assert "Elara" in ch1 and "Seraphine" in ch1  # cast line
    assert 'class="scene-date"' not in ch2  # undated scene has no date line
    # title page
    tp = z.read("text/titlepage.xhtml").decode()
    assert "Run One" in tp and "Saltmarch" in tp and 'class="daterange"' in tp


def test_build_epub_packs_images_and_fonts(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    imgs = [n for n in z.namelist() if n.startswith("images/")]
    assert imgs == ["images/img-000.png"]
    assert z.read("images/img-000.png") == b"pierbytes"
    ch1 = z.read("text/chapter-001.xhtml").decode()
    assert 'src="../images/img-000.png"' in ch1
    assert "example.com" not in ch1 and "lost" in ch1  # remote image degraded to alt
    fonts = sorted(n for n in z.namelist() if n.startswith("fonts/"))
    assert fonts == ["fonts/Cinzel-SemiBold.ttf", "fonts/EBGaramond-Italic.ttf",
                     "fonts/EBGaramond-Regular.ttf", "fonts/EBGaramond-SemiBold.ttf"]
    assert "css/stylesheet.css" in z.namelist()
    assert "@font-face" in z.read("css/stylesheet.css").decode()


def test_build_epub_unknown_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    import pytest
    with pytest.raises(campaigns.CampaignNotFound):
        epub.build_epub("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: new tests FAIL with `AttributeError: module ... has no attribute 'build_epub'`; Task 1–2 tests still pass.

- [ ] **Step 3: Create the templates**

`templates/epub/container.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
```

`templates/epub/package.opf`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">{{ identifier }}</dc:identifier>
    <dc:title>{{ title }}</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">{{ modified }}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
{% for it in items %}    <item id="{{ it.id }}" href="{{ it.href }}" media-type="{{ it.media_type }}"/>
{% endfor %}  </manifest>
  <spine>
{% for idref in spine %}    <itemref idref="{{ idref }}"/>
{% endfor %}  </spine>
</package>
```

`templates/epub/nav.xhtml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
  <title>Contents</title>
  <link rel="stylesheet" type="text/css" href="css/stylesheet.css"/>
</head>
<body>
  <nav epub:type="toc">
    <h1>Contents</h1>
    <ol>
      <li><a href="text/titlepage.xhtml">Title</a></li>
      <li><span>Scenes</span>
        <ol>
{% for ch in chapters %}          <li><a href="text/{{ ch.file }}">{{ ch.title }}</a></li>
{% endfor %}        </ol>
      </li>
{% if appendix %}      <li><a href="text/appendix.xhtml">Appendix</a>
        <ol>
{% for ap in appendix %}          <li><a href="text/{{ ap.file }}">{{ ap.title }}</a></li>
{% endfor %}        </ol>
      </li>
{% endif %}    </ol>
  </nav>
</body>
</html>
```

`templates/epub/titlepage.xhtml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{{ title }}</title>
  <link rel="stylesheet" type="text/css" href="../css/stylesheet.css"/>
</head>
<body class="titlepage">
  <h1>{{ title }}</h1>
{% if world %}  <p class="world">{{ world }}</p>
{% endif %}{% if date_range %}  <p class="daterange">{{ date_range }}</p>
{% endif %}</body>
</html>
```

`templates/epub/chapter.xhtml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{{ title }}</title>
  <link rel="stylesheet" type="text/css" href="../css/stylesheet.css"/>
</head>
<body class="chapter">
  <h1>{{ title }}</h1>
{% if date or location or cast %}  <div class="scene-meta">
{% if date %}    <p class="scene-date">{{ date }}</p>
{% endif %}{% if location %}    <p class="scene-location">{{ location }}</p>
{% endif %}{% if cast %}    <p class="scene-cast">{{ cast | join(" · ") }}</p>
{% endif %}  </div>
{% endif %}{% if epigraph %}  <p class="epigraph">{{ epigraph }}</p>
{% endif %}{{ body }}
</body>
</html>
```

`templates/epub/divider.xhtml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{{ title }}</title>
  <link rel="stylesheet" type="text/css" href="../css/stylesheet.css"/>
</head>
<body class="titlepage">
  <h1>{{ title }}</h1>
</body>
</html>
```

`templates/epub/appendix.xhtml` (rendered by Task 4's code; created here so the template set ships together):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>{{ name }}</title>
  <link rel="stylesheet" type="text/css" href="../css/stylesheet.css"/>
</head>
<body class="appendix">
  <h1>{{ name }}</h1>
{% if role %}  <p class="actor-role">{{ role }}</p>
{% endif %}{% if portrait %}  <img class="portrait" src="../images/{{ portrait }}" alt="{{ name }}"/>
{% endif %}{% for sec in sections %}{% if sec.label %}  <h2>{{ sec.label }}</h2>
{% endif %}{{ sec.html }}
{% endfor %}</body>
</html>
```

`templates/epub/stylesheet.css`:

```css
@font-face { font-family: "EB Garamond"; src: url(../fonts/EBGaramond-Regular.ttf); font-weight: 400; font-style: normal; }
@font-face { font-family: "EB Garamond"; src: url(../fonts/EBGaramond-Italic.ttf); font-weight: 400; font-style: italic; }
@font-face { font-family: "EB Garamond"; src: url(../fonts/EBGaramond-SemiBold.ttf); font-weight: 600; font-style: normal; }
@font-face { font-family: "Cinzel"; src: url(../fonts/Cinzel-SemiBold.ttf); font-weight: 600; font-style: normal; }

body { font-family: "EB Garamond", Georgia, serif; line-height: 1.5; margin: 1em; }
h1, h2 { font-family: "Cinzel", "EB Garamond", Georgia, serif; font-weight: 600; line-height: 1.2; }
h1 { font-size: 1.6em; margin: 1.5em 0 0.5em; }
h2 { font-size: 1.1em; margin: 1.2em 0 0.3em; }
img { max-width: 100%; }

.titlepage { text-align: center; }
.titlepage h1 { font-size: 2.2em; margin-top: 30%; }
.titlepage .world { font-style: italic; margin-top: 1em; }
.titlepage .daterange { font-size: 0.9em; margin-top: 2.5em; }

.scene-meta { font-size: 0.85em; color: #555; margin-bottom: 1.5em; }
.scene-meta p { margin: 0.15em 0; }
.epigraph { font-style: italic; margin: 1em 2em 1.5em; }
.speaker { font-family: "Cinzel", Georgia, serif; font-weight: 600; font-size: 0.82em; letter-spacing: 0.04em; }

.appendix .portrait { max-width: 40%; float: right; margin: 0 0 1em 1em; }
.actor-role { font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.1em; color: #666; margin-top: -0.5em; }
```

- [ ] **Step 4: Implement `build_epub`**

Append to `backend/src/grimoire/store/epub.py` (and remove any `# noqa: F401` markers from Task 2):

```python
def _friendly_or_none(provider, native: str) -> str | None:
    try:
        return calendars.friendly(provider, native)
    except calendars.CalendarError:
        return None


def _chapter(cid: str, croot: Path, wroot: Path | None, provider, sid: str,
             number: int, images: _Images) -> dict:
    scene = scenes.read_scene(cid, sid)
    meta = scene["meta"]
    title = meta.get("title", sid)
    times = scenes.get_time_history(cid, sid)
    date = _friendly_or_none(provider, times[0]) if times else None
    location = None
    hist = scenes.get_location_history(cid, sid)
    if hist:
        try:
            location = entities.read_entity(croot, "locations", hist[0])["meta"].get("name")
        except entities.EntityNotFound:
            pass  # deleted location: header line silently omitted
    cast = [a["name"] for a in appearances.scene_cast(cid, sid)]
    body = "\n".join(
        _message_html(m.get("speaker"), _rewrite_images(m["content"], croot, wroot, images))
        for m in scene["messages"])
    doc = _render("chapter.xhtml", title=title, date=date, location=location,
                  cast=cast, epigraph=meta.get("one_line") or None, body=Markup(body))
    return {"file": f"chapter-{number:03d}.xhtml", "title": title, "doc": doc}


def _appendix_entries(cid: str, croot: Path, wroot: Path | None, sids: list[str],
                      images: _Images) -> list[dict]:
    return []  # Task 4


def build_epub(cid: str) -> tuple[bytes, str]:
    """The whole campaign as an EPUB 3 book: (bytes, suggested filename)."""
    campaign = campaigns.read_campaign(cid)  # raises CampaignNotFound
    croot = campaigns.campaign_root(cid)
    wid = campaign["meta"].get("world", "")
    wroot = worlds.world_root(wid) if wid else None
    if wroot is not None and not wroot.exists():
        wroot = None
    world_name = worlds.read_world(wid)["meta"].get("name", "") if wroot is not None else ""
    provider = calendars.get_provider(calendars.read_calendar(croot)["primary"])
    images = _Images()

    sids = [s["id"] for s in sorted(scenes.list_scenes(cid), key=lambda s: s["id"])]
    chapters = [_chapter(cid, croot, wroot, provider, sid, i, images)
                for i, sid in enumerate(sids, start=1)]
    appendix = _appendix_entries(cid, croot, wroot, sids, images)

    # in-world date range: first dated scene's start — last dated scene's latest
    histories = [h for sid in sids if (h := scenes.get_time_history(cid, sid))]
    date_range = None
    if histories:
        first = _friendly_or_none(provider, histories[0][0])
        last = _friendly_or_none(provider, histories[-1][-1])
        if first and last:
            date_range = first if first == last else f"{first} — {last}"

    title = campaign["meta"].get("name", cid)
    docs = [("text/titlepage.xhtml",
             _render("titlepage.xhtml", title=title, world=world_name, date_range=date_range))]
    docs += [(f"text/{c['file']}", c["doc"]) for c in chapters]
    if appendix:
        docs.append(("text/appendix.xhtml", _render("divider.xhtml", title="Appendix")))
        docs += [(f"text/{e['file']}", e["doc"]) for e in appendix]

    fonts = sorted(FONTS_DIR.glob("*.ttf")) if FONTS_DIR.exists() else []
    items = [{"id": f"doc-{i}", "href": href, "media_type": "application/xhtml+xml"}
             for i, (href, _) in enumerate(docs)]
    spine = [it["id"] for it in items]
    items.append({"id": "css", "href": "css/stylesheet.css", "media_type": "text/css"})
    items += [{"id": f"font-{i}", "href": f"fonts/{f.name}", "media_type": "font/ttf"}
              for i, f in enumerate(fonts)]
    items += [{"id": f"img-{i}", "href": f"images/{name}",
               "media_type": _EXT_MEDIA.get(name.rsplit(".", 1)[-1], "application/octet-stream")}
              for i, name in enumerate(images.by_path.values())]

    opf = _render("package.opf", identifier=f"urn:grimoire:campaign:{cid}", title=title,
                  modified=campaign["meta"].get("updated") or now_iso(),
                  items=items, spine=spine)
    nav = _render("nav.xhtml", chapters=chapters, appendix=appendix)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", _render("container.xml"))
        z.writestr("package.opf", opf)
        z.writestr("nav.xhtml", nav)
        for href, doc in docs:
            z.writestr(href, doc)
        z.writestr("css/stylesheet.css", _render("stylesheet.css"))
        for f in fonts:
            z.writestr(f"fonts/{f.name}", f.read_bytes())
        for p, name in images.by_path.items():
            z.writestr(f"images/{name}", p.read_bytes())
    return buf.getvalue(), f"{cid}.epub"
```

- [ ] **Step 5: Document the template family**

In `templates/README.md`, append at the end:

```markdown
### `epub/` — not prompts: campaign EPUB export pages

The one non-LLM family. `store/epub.py` renders these (its own jinja2
environment, `autoescape=True`, unlike the prompt contract above) into the
book's XHTML/OPF/CSS. `container.xml` and `stylesheet.css` are static;
`package.opf` takes `identifier`/`title`/`modified`/`items`/`spine`;
`nav.xhtml` takes `chapters`/`appendix`; `titlepage.xhtml` takes
`title`/`world`/`date_range`; `chapter.xhtml` takes
`title`/`date`/`location`/`cast`/`epigraph`/`body`; `divider.xhtml` takes
`title`; `appendix.xhtml` takes `name`/`role`/`portrait`/`sections`.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add templates/epub templates/README.md backend/src/grimoire/store/epub.py backend/tests/test_epub_store.py
git commit -m "feat(epub): build_epub — title page, scene chapters, packed images and fonts"
```

---

### Task 4: Appendix entries — cast actors and visited locations

**Files:**
- Modify: `backend/src/grimoire/store/epub.py` (replace the `_appendix_entries` stub, add `_actor_sections`)
- Test: `backend/tests/test_epub_store.py` (append)

**Interfaces:**
- Consumes: `appearances.roster(cid) -> [{"kind","id","version","role","scenes"}]`; `characters.read_card(root, cid, vid)["data"]`; `pcs.read_persona(root, pid, vid)`; `entities.read_entity`; `scenes.get_location_history`; `assets.image_path(..., assets.AVATAR, ...)`; Task 2/3 helpers and the `appendix.xhtml` template.
- Produces: `_appendix_entries(...) -> list[{"file","title","doc"}]` — players first, then NPCs (alphabetical by kind+id within each), then visited locations alphabetical by display name.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_epub_store.py`:

```python
def test_appendix_actors_and_visited_locations(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, "seraphine", "default", assets.AVATAR, b"face", "png")
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    names = z.namelist()
    assert "text/appendix.xhtml" in names  # divider page
    sera = z.read("text/actor-characters-seraphine.xhtml").decode()
    assert "the drowned keeper" in sera and "grim" in sera
    assert "Description" in sera and "Personality" in sera
    assert 'class="portrait"' in sera
    elara = z.read("text/actor-pcs-elara.xhtml").decode()
    assert "A wanderer." in elara and "Player character" in elara
    docks = z.read("text/location-the-docks.xhtml").decode()
    assert "Salt-stained piers." in docks
    assert "text/location-the-keep.xhtml" not in names  # never visited
    # players come before NPCs in the spine
    spine_docs = [n for n in names if n.startswith("text/actor-")]
    assert spine_docs.index("text/actor-pcs-elara.xhtml") >= 0
    import xml.etree.ElementTree as ET
    opf = ET.fromstring(z.read("package.opf"))
    hrefs = [i.get("href") for i in opf.findall(".//opf:item", OPF_NS)]
    a = hrefs.index("text/actor-pcs-elara.xhtml")
    b = hrefs.index("text/actor-characters-seraphine.xhtml")
    c = hrefs.index("text/location-the-docks.xhtml")
    assert a < b < c
    # nav lists the appendix
    nav = z.read("nav.xhtml").decode()
    assert "Appendix" in nav and "text/actor-pcs-elara.xhtml" in nav


def test_appendix_skips_unreadable_actor(monkeypatch, tmp_path):
    _wid, cid, _s1, _s2 = _fixture_campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    (croot / "characters" / "seraphine" / "default.json").unlink()
    blob, _ = epub.build_epub(cid)
    z = _open(blob)
    assert "text/actor-characters-seraphine.xhtml" not in z.namelist()
    assert "text/actor-pcs-elara.xhtml" in z.namelist()  # book still builds


def test_no_appendix_no_divider(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Bare")
    cid = campaigns.create_campaign("Empty Run", wid)
    blob, _ = epub.build_epub(cid)  # zero scenes: title page + nothing else
    z = _open(blob)
    assert "text/appendix.xhtml" not in z.namelist()
    assert "text/titlepage.xhtml" in z.namelist()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: the two appendix tests FAIL (`KeyError` reading `text/actor-...` — stub returns `[]`); `test_no_appendix_no_divider` may already pass.

- [ ] **Step 3: Implement the appendix**

In `backend/src/grimoire/store/epub.py`, replace the `_appendix_entries` stub with:

```python
def _actor_sections(croot: Path, kind: str, actor_id: str, vid: str) -> tuple[str, list[dict]]:
    """(display name, labeled markdown sections) — the reader-facing
    cast_detail field set; prompt plumbing is deliberately excluded."""
    if kind == "characters":
        data = characters.read_card(croot, actor_id, vid).get("data", {})
        name = data.get("name") or actor_id
        labelled = [("Description", "description"), ("Personality", "personality"),
                    ("Scenario", "scenario")]
        sections = [{"label": lbl, "text": data[f]} for lbl, f in labelled
                    if isinstance(data.get(f), str) and data[f].strip()]
    else:
        p = pcs.read_persona(croot, actor_id, vid)
        name = p.get("name") or actor_id
        sections = [{"label": None, "text": t}
                    for t in (p.get("summary", "").strip(), p.get("description", "").strip()) if t]
    return name, sections


def _avatar(croot: Path, wroot: Path | None, rid: str, vid: str, base: str,
            images: _Images) -> str | None:
    for root in (croot, wroot):
        if root is None:
            continue
        p = assets.image_path(root, rid, vid, assets.AVATAR, base=base)
        if p is not None:
            return images.add(p)
    return None


def _appendix_entries(cid: str, croot: Path, wroot: Path | None, sids: list[str],
                      images: _Images) -> list[dict]:
    entries: list[dict] = []
    roster = sorted(appearances.roster(cid),
                    key=lambda a: (a["role"] != "player", a["kind"], a["id"]))
    for a in roster:
        try:
            name, sections = _actor_sections(croot, a["kind"], a["id"], a["version"])
        except (characters.CharacterNotFound, characters.VersionNotFound,
                pcs.PCNotFound, pcs.PCVersionNotFound):
            continue  # unreadable actor: skip the entry, never fail the book
        portrait = (_avatar(croot, wroot, a["id"], a["version"], "characters", images)
                    if a["kind"] == "characters" else None)
        doc = _render("appendix.xhtml", name=name, portrait=portrait,
                      role="Player character" if a["role"] == "player" else None,
                      sections=[{"label": s["label"],
                                 "html": Markup(_md(_rewrite_images(s["text"], croot, wroot, images)))}
                                for s in sections])
        entries.append({"file": f"actor-{a['kind']}-{a['id']}.xhtml", "title": name, "doc": doc})

    visited: dict[str, None] = {}  # insertion-ordered de-dupe
    for sid in sids:
        for eid in scenes.get_location_history(cid, sid):
            visited.setdefault(eid, None)
    locs = []
    for eid in visited:
        try:
            ent = entities.read_entity(croot, "locations", eid)
        except entities.EntityNotFound:
            continue
        locs.append((ent["meta"].get("name", eid), eid, ent["body"]))
    for name, eid, body in sorted(locs):
        doc = _render("appendix.xhtml", name=name,
                      portrait=_avatar(croot, wroot, eid, "default", "locations", images),
                      role=None,
                      sections=[{"label": None,
                                 "html": Markup(_md(_rewrite_images(body, croot, wroot, images)))}])
        entries.append({"file": f"location-{eid}.xhtml", "title": name, "doc": doc})
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_epub_store.py -q`
Expected: all pass.

- [ ] **Step 5: Run the whole backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/epub.py backend/tests/test_epub_store.py
git commit -m "feat(epub): appendix entries for cast actors and visited locations"
```

---

### Task 5: Export route

**Files:**
- Modify: `backend/src/grimoire/routes.py` (one new GET route, placed with the other `/campaigns/{cid}` routes near `_campaign_root_or_404`'s users)
- Test: `backend/tests/test_routes.py` (append)

**Interfaces:**
- Consumes: `store.epub.build_epub(cid) -> (bytes, filename)`; existing `Response`, `HTTPException` imports in `routes.py`; the `client` fixture and `_campaign` helper already defined at the top of `test_routes.py`.
- Produces: `GET /api/campaigns/{cid}/export.epub` → 200 `application/epub+zip` attachment; 404 for unknown campaign.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py`:

```python
# ---- campaign EPUB export ----

def test_export_epub_route(client):
    _wid, cid = _campaign(client)
    r = client.get(f"/api/campaigns/{cid}/export.epub")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/epub+zip"
    assert r.headers["content-disposition"] == f'attachment; filename="{cid}.epub"'
    assert r.content[:2] == b"PK"


def test_export_epub_unknown_campaign_404(client):
    assert client.get("/api/campaigns/nope/export.epub").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k export_epub`
Expected: 2 FAIL (404 on the existing-campaign test — route missing).

- [ ] **Step 3: Add the route**

In `backend/src/grimoire/routes.py`, next to the other campaign-level GET routes (e.g. just after the `get_campaign` handler — search for `def get_campaign(`):

```python
@router.get("/campaigns/{cid}/export.epub")
def export_campaign_epub(cid: str):
    try:
        blob, filename = store.epub.build_epub(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return Response(content=blob, media_type="application/epub+zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```

Route-ordering caveat: FastAPI matches in registration order; if a variable route like `/campaigns/{cid}/{kind}` is registered **before** this one, `export.epub` would be swallowed by it. Register this route above any such catch-all campaign routes (check where `list_campaign_entity_images`-style `/{kind}` routes are defined; place this one earlier in the file).

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: all pass (including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(epub): GET /campaigns/{cid}/export.epub route"
```

---

### Task 6: Frontend — Export EPUB link on the campaign page

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx` (one anchor in the `.sub-actions` div, ~line 308)
- Modify: `frontend/src/index.css` (`.sub-export` rule next to `.sub-changes`, ~line 166)
- Test: `frontend/src/routes/CampaignView.test.tsx` (append)

**Interfaces:**
- Consumes: the backend route from Task 5 (`/api/campaigns/{cid}/export.epub`); the existing `renderCampaign()` helper and api mocks in `CampaignView.test.tsx` (campaign id in tests is `"run"`).
- Produces: a downloadable link labeled "Export EPUB" in the campaign subheader.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/CampaignView.test.tsx`:

```tsx
test("renders an Export EPUB download link", async () => {
  renderCampaign();
  const link = await screen.findByRole("link", { name: /export epub/i });
  expect(link).toHaveAttribute("href", "/api/campaigns/run/export.epub");
  expect(link).toHaveAttribute("download");
});
```

- [ ] **Step 2: Run test to verify it fails**

From `frontend/`: `npx vitest run src/routes/CampaignView.test.tsx`
Expected: the new test FAILS (link not found); all others pass.

- [ ] **Step 3: Add the link**

In `frontend/src/routes/CampaignView.tsx`, inside `<div className="sub-actions">` (before the Changes button):

```tsx
        <div className="sub-actions">
          <a className="sub-export" href={`/api/campaigns/${cid}/export.epub`} download>
            Export EPUB
          </a>
          <button className="sub-changes" onClick={() => setShowChanges((v) => !v)}>
```

In `frontend/src/index.css`, directly after the `.sub-changes` rule:

```css
.sub-export { display: flex; align-items: center; border-left: 1px solid var(--chrome-rule); color: var(--chrome-muted); font-family: var(--fm); font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; padding: 0 16px; text-decoration: none; }
```

- [ ] **Step 4: Run tests to verify they pass**

From `frontend/`: `npx vitest run src/routes/CampaignView.test.tsx`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/index.css frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(epub): Export EPUB link in the campaign subheader"
```

---

### Task 7: Full verification

**Files:** none (verification only).

**Interfaces:** everything above.

- [ ] **Step 1: Backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all pass, 0 failures.

- [ ] **Step 2: Frontend suite + types**

From `frontend/`: `npx vitest run` then `npx tsc -b`
Expected: all tests pass; tsc emits no errors.

- [ ] **Step 3: Smoke-open a real export**

```bash
backend/.venv/Scripts/python.exe - <<'EOF'
import os, tempfile, zipfile, io
d = tempfile.mkdtemp()
os.environ["GRIMOIRE_HOME"] = d
from grimoire.store import worlds, campaigns, scenes, epub
wid = worlds.create_world("Smoke")
cid = campaigns.create_campaign("Smoke Run", wid)
sid = scenes.create_scene(cid, "Only Scene")
scenes.append_message(cid, sid, "assistant", "It *works*.")
blob, name = epub.build_epub(cid)
z = zipfile.ZipFile(io.BytesIO(blob))
assert z.testzip() is None
print(name, len(blob), "bytes,", len(z.namelist()), "entries")
EOF
```

Expected: prints `smoke-run.epub <N> bytes, <M> entries` with no traceback.

- [ ] **Step 4: Report**

No commit. Report results; if anything failed, fix before claiming completion.
