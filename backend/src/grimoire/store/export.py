"""Campaign export: a format-neutral collector shared by every export renderer
(EPUB, markdown bundle, HTML page, plain-text transcript, JSON dump). Walks
scenes once, resolves localized image URLs through the campaign overlay, and
hands back render-ready structured data — chapters, appendix, campaign
masthead. Nothing is written into the store; each renderer decides how to
package the result (`epub.py`, and the `build_*` functions below).
"""

from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from pathlib import Path

import markdown as _md_lib
from markupsafe import escape

from . import (
    assets,
    calendars,
    campaign_images,
    characters,
    chronicle,
    covers,
    entities,
    fetch,
    overlay,
    pcs,
    worlds,
)
from .appearances import cast as appearances_cast
from .appearances import paths as appearances_paths
from .campaigns import paths as campaigns_paths
from .campaigns import read as campaigns_read
from .paths import slugify
from .scenes import read as scenes_read
from .scenes import serialize as scenes_serialize

# Localized app image URLs (see store.localize): EVERY shape the app writes. A
# URL shape missing from here is not a rendering bug -- it is a book shipped
# with that image silently degraded to its alt text.
_IMG_URL = re.compile(
    r"/api/(?:"
    # The campaign's own image library: campaign-scoped, and the one shape with
    # no record between the scope and `/images/` -- so it is spelled out as its
    # own alternative rather than as an empty branch below, which would also
    # have matched a `/api/worlds/<wid>/images/<name>` the app never writes.
    r"campaigns/[^/\s]+/(?P<lib>images)"
    r"|(?:worlds|campaigns)/[^/\s]+/(?:"
    # `actor` carries the base as well as matching the segment: characters and
    # PCs address their images identically, differing only in the folder the
    # bytes live under (#219).
    r"(?P<actor>characters|pcs)/(?P<aid>[^/\s]+)/versions/(?P<vid>[^/\s]+)"
    r"|greetings/(?P<gid>[^/\s]+)"
    r"|(?P<kind>locations|lore)/(?P<eid>[^/\s]+)"
    r")/images"
    r")/(?P<name>[^/\s?#]+)")

_MD_IMG = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")

EXT_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "gif": "image/gif", "webp": "image/webp"}


def packed_ext(data: bytes) -> str | None:
    """The extension to pack these bytes under -- None if they cannot be named.

    The bytes decide, and only the bytes: every renderer names a media type from
    the packed suffix, so a name that lies produces a book epubcheck rejects
    (#321). Uploads can no longer be misnamed (`routes.common._upload_image_ext`),
    but stores already on disk hold files that are, and nothing renames them --
    naming from the bytes here is what makes a book exported from such a store
    valid anyway.

    Renaming costs nothing: the packed name is generated (`img-000`), and an app
    image URL addresses the *logical* name -- `.../images/avatar` -- with the
    extension living only in the filename `assets.image_path` globs for. So the
    packed name and the declared type agree, rather than packing `img-000.png`
    and declaring `image/jpeg` (legal, but it reads as a mistake).

    The file's own suffix is never consulted, not even as a fallback. A suffix
    the bytes do not corroborate is the whole defect; substituting it for bytes
    we cannot identify would just re-declare the same guess in a book that has
    to be right. `Images` drops those images instead.
    """
    return fetch.sniff_ext(data)


class Images:
    """Registry of packed images: disk path -> packed `images/` name.

    Every registered image is named from its own bytes (`packed_ext`), and an
    image whose bytes name no format we can declare is **not registered at
    all** -- `add` returns None and the caller degrades it exactly as it
    degrades a remote or missing image, to its alt text.

    Dropping is the point rather than a limitation. The alternative is packing
    a file we cannot label and declaring something -- the stored suffix, or
    `application/octet-stream` -- which is how a book fails epubcheck, and a
    reader that refuses to render the image gets the same nothing the alt text
    gives, minus the caption. The bytes stay in the store either way; only the
    book declines to carry them (#321). The formats this drops are the ones the
    store cannot serve honestly in the first place: a BMP or an AVIF a
    downloader named `.png` (`fetch.download_url`'s last-resort ext), a
    truncated file, a file we could not read.

    That last case is a small robustness win: an unreadable image used to
    register fine and then raise at zip time, failing the whole export. Now it
    is simply absent from the book.

    One window this does not close: the header is read while the export is
    being composed and the bytes are packed later, so a writer that swaps a
    file's FORMAT in place, under the same name, mid-export still gets one
    wrongly-declared image. No app path can do that -- an upload of a different
    format lands on a different suffix and drops the old file, which makes the
    pack fail loudly instead -- so it takes a sync client or a hand edit landing
    inside those seconds. Closing it means either holding every image in memory
    from collect to zip, or deriving the type at zip time and threading it back
    into a manifest that is already built; neither is worth it for a race that
    replaces a defect which used to be unconditional.
    """

    def __init__(self):
        self.by_path: dict[Path, str] = {}
        # Paths already rejected, so a scene referencing one broken image forty
        # times opens it once rather than forty times.
        self._unnamable: set[Path] = set()

    def add(self, p: Path) -> str | None:
        if p in self.by_path:
            return self.by_path[p]
        if p in self._unnamable:
            return None
        try:
            with p.open("rb") as f:
                header = f.read(12)  # every signature `sniff_ext` knows fits in 12
        except OSError:
            header = b""  # unreadable, a directory, gone: unnamable, so dropped
        ext = packed_ext(header)
        if ext is None:
            self._unnamable.add(p)
            return None
        self.by_path[p] = f"img-{len(self.by_path):03d}.{ext}"
        return self.by_path[p]


def _resolve_image(cid: str, m: re.Match) -> Path | None:
    """Map a localized app URL to a disk file.

    Record images resolve through the campaign overlay: campaign tree first,
    then the campaign's world, with a campaign asset tombstone hiding an
    inherited image (greeting images only live world-side). The campaign's own
    library resolves campaign-side and only there -- it inherits nothing."""
    if m["lib"]:
        # No overlay: the campaign library is campaign-local and inherits
        # nothing (`store.campaign_images`). Resolved against the campaign being
        # EXPORTED, not against the id written in the URL -- which is what makes
        # a forked campaign's book carry the fork's own copy of the image rather
        # than reaching back into the campaign it branched from.
        return campaign_images.image_path(cid, m["name"])
    if m["actor"]:
        rid, vid, base = m["aid"], m["vid"], m["actor"]
    elif m["gid"]:
        rid, vid, base = m["gid"], "default", "greetings"
    else:
        rid, vid, base = m["eid"], "default", m["kind"]
    root = overlay.image_root(cid, rid, vid, m["name"], base=base)
    return assets.image_path(root, rid, vid, m["name"], base=base)


def rewrite_images(text: str, cid: str, images: Images, prefix: str = "images/") -> str:
    """Point every markdown image at its packed copy under `prefix`; remote,
    missing, and unnamable images degrade to their alt text (a broken img is
    worse) -- see `Images` for why an image we cannot declare is dropped rather
    than packed under a guess."""
    def sub(m: re.Match) -> str:
        app = _IMG_URL.match(m["url"])
        if app:
            p = _resolve_image(cid, app)
            packed = images.add(p) if p is not None else None
            if packed is not None:
                return f"![{m['alt']}]({prefix}{packed})"
        return m["alt"]
    return _MD_IMG.sub(sub, text)


def drop_images(text: str) -> str:
    """Strip every markdown image down to its alt text (no image kept at all)."""
    return _MD_IMG.sub(lambda m: m["alt"], text)


def _friendly_or_none(provider, native: str) -> str | None:
    try:
        return calendars.friendly(provider, native)
    except calendars.CalendarError:
        return None


def _avatar(cid: str, rid: str, vid: str, base: str, images: Images, prefix: str) -> str | None:
    """The packed portrait URL, or None -- an actor with no avatar and one whose
    avatar cannot be named are the same thing to a book: no portrait."""
    root = overlay.image_root(cid, rid, vid, assets.AVATAR, base=base)
    p = assets.image_path(root, rid, vid, assets.AVATAR, base=base)
    packed = images.add(p) if p is not None else None
    return f"{prefix}{packed}" if packed is not None else None


def _actor_sections(aroot: Path, kind: str, actor_id: str, vid: str) -> tuple[str, list[dict]]:
    """(display name, labeled sections) — the reader-facing cast_detail field
    set; prompt plumbing is deliberately excluded.

    `aroot` is an `appearances.locked_actor_root`; callers walk the roster."""
    if kind == "characters":
        data = characters.read_card(aroot, actor_id, vid).get("data", {})
        name = data.get("name") or actor_id
        labelled = [("Description", "description"), ("Personality", "personality"),
                    ("Scenario", "scenario")]
        sections = [{"label": lbl, "text": data[f]} for lbl, f in labelled
                    if isinstance(data.get(f), str) and data[f].strip()]
    else:
        p = pcs.read_persona(aroot, actor_id, vid)
        name = p.get("name") or actor_id
        sections = [{"label": None, "text": t}
                    for t in (p.get("summary", "").strip(), p.get("description", "").strip()) if t]
    return name, sections


def _chapter(cid: str, provider, sid: str, number: int, images: Images, prefix: str) -> dict:
    scene = scenes_read.read_scene(cid, sid)
    meta = scene["meta"]
    title = meta.get("title", sid)
    times = scenes_read.get_time_history(cid, sid)
    date = _friendly_or_none(provider, times[0]) if times else None
    location = None
    hist = scenes_read.get_location_history(cid, sid)
    if hist:
        try:
            location = overlay.read_entity(cid, "locations", hist[0])["meta"].get("name")
        except entities.EntityNotFound:
            pass  # deleted location: header line silently omitted
    cast = [a["name"] for a in appearances_cast.scene_cast(cid, sid)]
    # The transition tag is internal metadata (drift measurement treats these as
    # turn separators); it is never a speaker the reader should see. Dropping it
    # here keeps HTML, plain text and EPUB — all of which build from collect() —
    # rendering a transition as the unlabelled narration it has always been, and
    # makes pre-tag and post-tag transitions look identical in a book.
    messages = [{"role": m["role"],
                 "speaker": None if m.get("speaker") == scenes_serialize.TRANSITION_SPEAKER
                            else m.get("speaker"),
                 "content": rewrite_images(m["content"], cid, images, prefix)}
                for m in scene["messages"]]
    return {"sid": sid, "number": number, "title": title, "date": date, "location": location,
            "cast": cast, "epigraph": meta.get("one_line") or None, "messages": messages}


def _appendix_entries(cid: str, aroot: Path, sids: list[str], images: Images, prefix: str) -> list[dict]:
    entries: list[dict] = []
    roster = sorted(appearances_cast.roster(cid),
                    key=lambda a: (a["role"] != "player", a["kind"], a["id"]))
    for a in roster:
        try:
            name, sections = _actor_sections(aroot, a["kind"], a["id"], a["version"])
        except (json.JSONDecodeError, characters.CharacterNotFound,
                characters.VersionNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            continue  # unreadable actor: skip the entry, never fail the export
        # `kind` IS the asset base for both actor kinds, so a PC's portrait
        # packs like a character's rather than being dropped (#219).
        portrait = _avatar(cid, a["id"], a["version"], a["kind"], images, prefix)
        entries.append({
            "kind": a["kind"], "id": a["id"], "name": name, "portrait": portrait,
            "role": "Player character" if a["role"] == "player" else None,
            "sections": [{"label": s["label"], "text": rewrite_images(s["text"], cid, images, prefix)}
                        for s in sections],
        })

    visited: dict[str, None] = {}  # insertion-ordered de-dupe
    for sid in sids:
        for eid in scenes_read.get_location_history(cid, sid):
            visited.setdefault(eid, None)
    locs = []
    for eid in visited:
        try:
            ent = overlay.read_entity(cid, "locations", eid)
        except entities.EntityNotFound:
            continue
        locs.append((ent["meta"].get("name", eid), eid, ent["body"]))
    for name, eid, body in sorted(locs):
        entries.append({
            "kind": "locations", "id": eid, "name": name,
            "portrait": _avatar(cid, eid, "default", "locations", images, prefix),
            "role": None,
            "sections": [{"label": None, "text": rewrite_images(body, cid, images, prefix)}],
        })
    return entries


def collect(cid: str, image_prefix: str = "images/") -> dict:
    """The whole campaign as render-neutral data: (campaign masthead, chapters,
    appendix, packed-image registry). Raises `campaigns.CampaignNotFound`."""
    campaign = campaigns_read.read_campaign(cid)
    croot = campaigns_paths.campaign_root(cid)
    wid = campaign["meta"].get("world", "")
    # through world_root_of, not world_root: a campaign can carry a world
    # reference the guard refuses, and an export must degrade to "no world"
    # rather than 500 (#259 review)
    wroot = campaigns_read.world_root_of(cid)
    if not wroot.exists():
        wroot = None
    world_name = worlds.read_world(wid)["meta"].get("name", "") if wroot is not None else ""
    provider = calendars.get_provider(calendars.read_calendar(croot)["primary"])
    images = Images()

    sids = [s["id"] for s in sorted(scenes_read.list_scenes(cid), key=lambda s: s["id"])]
    chapters = [_chapter(cid, provider, sid, i, images, image_prefix)
                for i, sid in enumerate(sids, start=1)]
    appendix = _appendix_entries(cid, appearances_paths.locked_actor_root(cid), sids, images, image_prefix)

    # in-world date range: first dated scene's start — last dated scene's latest
    histories = [h for sid in sids if (h := scenes_read.get_time_history(cid, sid))]
    date_range = None
    if histories:
        first = _friendly_or_none(provider, histories[0][0])
        last = _friendly_or_none(provider, histories[-1][-1])
        if first and last:
            date_range = first if first == last else f"{first} — {last}"

    return {
        "title": campaign["meta"].get("name", cid),
        "world_name": world_name,
        # The cover is a PATH, deliberately NOT registered in `images`: every
        # renderer packs everything in that registry (`build_markdown_bundle`
        # zips it, `build_html` base64-inlines it), so registering the cover
        # would ship it into exports that never reference it -- and would
        # renumber every other packed image. Only `epub.build_epub` uses this.
        "cover": covers.cover_path(cid),
        "date_range": date_range,
        "updated": campaign["meta"].get("updated", ""),
        "chapters": chapters,
        "appendix": appendix,
        "images": images,
    }


def chapter_anchor(ch: dict) -> str:
    """A scene's stable id: the EPUB chapter document's basename, and the
    fragment a single-document format links its table of contents at.

    Numbered rather than slugified on purpose -- `collect` numbers chapters in
    scene order, and two scenes may well share a title (the app titles a scene
    for you), which would give a slug-derived anchor two definitions and send
    every link to the first one."""
    return f"chapter-{ch['number']:03d}"


def appendix_anchor(e: dict) -> str:
    """The same, for an appendix entry -- record ids are already unique within
    a kind, so these need no numbering."""
    if e["kind"] == "locations":
        return f"location-{e['id']}"
    return f"actor-{e['kind']}-{e['id']}"


def chapter_filename(ch: dict, ext: str) -> str:
    return f"{ch['number']:03d}-{slugify(ch['title'])}.{ext}"


def appendix_filename(e: dict, ext: str) -> str:
    return f"{appendix_anchor(e)}.{ext}"


def toc_label(ch: dict) -> str:
    """A scene's table-of-contents label, in the formats that write their own
    numbering (EPUB nav/NCX, the plain-text contents block, the markdown
    index). Formats whose own markup numbers the list -- an HTML `<ol>` -- link
    the bare title instead, and get the same reading.

    The number is what makes the entry usable: scene titles repeat, so an
    unnumbered contents page can list "Arrival" three times with nothing to
    tell the reader which one they are about to open."""
    return f"{ch['number']}. {ch['title']}"


def _header_lines(ch: dict) -> list[str]:
    lines = []
    meta_bits = [b for b in (ch["date"], ch["location"]) if b]
    if meta_bits:
        lines.append(f"*{' — '.join(meta_bits)}*")
    if ch["cast"]:
        lines.append(f"**Cast:** {', '.join(ch['cast'])}")
    if ch["epigraph"]:
        lines.append(f"> {ch['epigraph']}")
    return lines


def build_markdown_bundle(cid: str) -> tuple[bytes, str]:
    """A zip of one markdown file per scene plus an appendix and packed
    images/, reusing the campaign's own `**Speaker:** content` transcript
    convention — a portable, human-editable bundle.

    `index.md` is the table of contents; one file per scene, numbered in its
    name and its heading, is the chapter marker this format has."""
    data = collect(cid)  # raises CampaignNotFound; image_prefix="images/"

    # index.md is the bundle's table of contents -- the one file a reader opens
    # first, and the only thing tying a directory of `001-arrival.md` back
    # together. The scene list is an ordered list carrying explicit numbers, so
    # it reads the same in a renderer and in a plain editor, and matches both
    # the chapter headings and the numeric filename prefix.
    index = [f"# {data['title']}"]
    if data["world_name"]:
        index.append(f"*{data['world_name']}*")
    if data["date_range"]:
        index.append(data["date_range"])
    if data["chapters"]:
        index.append("## Scenes")
        index.append("\n".join(f"{c['number']}. [{c['title']}]({chapter_filename(c, 'md')})"
                               for c in data["chapters"]))
    if data["appendix"]:
        index.append("## Appendix")
        index.append("\n".join(f"- [{e['name']}]({appendix_filename(e, 'md')})"
                               for e in data["appendix"]))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.md", "\n\n".join(index) + "\n")
        for c in data["chapters"]:
            # The heading carries the scene's number as well as its title: it is
            # the chapter marker a markdown reader sees, and numbering it is what
            # lets a reader landing in one file know where in the run they are.
            lines = [f"# {toc_label(c)}", *_header_lines(c),
                     chronicle.transcript_text(c["messages"])]
            z.writestr(chapter_filename(c, "md"), "\n\n".join(lines) + "\n")
        for e in data["appendix"]:
            lines = [f"# {e['name']}"]
            if e["role"]:
                lines.append(f"*{e['role']}*")
            if e["portrait"]:
                lines.append(f"![{e['name']}]({e['portrait']})")
            for s in e["sections"]:
                if s["label"]:
                    lines.append(f"## {s['label']}")
                lines.append(s["text"])
            z.writestr(appendix_filename(e, "md"), "\n\n".join(lines) + "\n")
        for p, name in data["images"].by_path.items():
            z.writestr(f"images/{name}", p.read_bytes())
    return buf.getvalue(), f"{cid}-markdown.zip"


_HTML_CSS = """
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.5; margin: 2em auto; max-width: 46em; padding: 0 1em; }
h1, h2, h3 { font-weight: 600; line-height: 1.2; }
img { max-width: 100%; }
.titlepage { text-align: center; margin-bottom: 3em; }
.titlepage .world { font-style: italic; }
nav.toc { margin: 0 0 4em; }
nav.toc h2 { margin-bottom: 0.6em; }
nav.toc h3 { font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.1em; color: #666; margin: 1.2em 0 0.3em; }
nav.toc ol, nav.toc ul { margin: 0; padding-left: 1.6em; }
nav.toc li { margin: 0.2em 0; }
section.chapter, section.appendix { margin-bottom: 3em; }
.scene-meta { font-size: 0.85em; color: #555; margin-bottom: 1.5em; }
.scene-meta p { margin: 0.15em 0; }
.epigraph { font-style: italic; margin: 1em 2em 1.5em; }
.speaker { font-weight: 600; font-size: 0.82em; letter-spacing: 0.04em; }
.appendix .portrait { max-width: 40%; float: right; margin: 0 0 1em 1em; }
.actor-role { font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.1em; color: #666; margin-top: -0.5em; }
"""


def _html_md(text: str) -> str:
    return _md_lib.markdown(text, extensions=["tables"])


def _html_message(speaker: str | None, content: str) -> str:
    html = _html_md(content)
    if not speaker:
        return html
    label = f'<span class="speaker">{escape(speaker)}</span> '
    if html.startswith("<p>"):
        return "<p>" + label + html[3:]
    return f"<p>{label}</p>\n{html}"


def _html_toc(data: dict) -> str:
    """The one-page export's table of contents — "" for a campaign with nothing
    in it, rather than an empty `<nav>`.

    A single scrolling document needs this more than the multi-file formats do:
    nothing else in it tells a reader how many scenes there are, or lets them
    jump to one. The scene list is an `<ol>`, so the browser numbers it and the
    link text stays the bare title; the anchors are the ids `build_html` puts on
    every section."""
    parts = []
    if data["chapters"]:
        parts.append("<h3>Scenes</h3><ol>" + "".join(
            f"<li><a href=\"#{chapter_anchor(c)}\">{escape(c['title'])}</a></li>"
            for c in data["chapters"]) + "</ol>")
    if data["appendix"]:
        parts.append("<h3>Appendix</h3><ul>" + "".join(
            f"<li><a href=\"#{appendix_anchor(e)}\">{escape(e['name'])}</a></li>"
            for e in data["appendix"]) + "</ul>")
    if not parts:
        return ""
    return "<nav class=\"toc\" id=\"contents\"><h2>Contents</h2>" + "".join(parts) + "</nav>"


def build_html(cid: str) -> tuple[bytes, str]:
    """A single self-contained HTML page: every image inlined as a base64
    data URI so the file has no external dependencies.

    A `<nav class="toc">` after the title page is the table of contents, and
    each scene is a `<section class="chapter">` with the id that nav links —
    the chapter marker a one-document format can carry."""
    data = collect(cid)  # raises CampaignNotFound; image_prefix="images/"

    sections = [f"<section class=\"titlepage\"><h1>{escape(data['title'])}</h1>"
                + (f"<p class=\"world\">{escape(data['world_name'])}</p>" if data["world_name"] else "")
                + (f"<p class=\"daterange\">{escape(data['date_range'])}</p>" if data["date_range"] else "")
                + "</section>"]
    toc = _html_toc(data)
    if toc:
        sections.append(toc)
    for ch in data["chapters"]:
        meta = []
        if ch["date"]:
            meta.append(f"<p class=\"scene-date\">{escape(ch['date'])}</p>")
        if ch["location"]:
            meta.append(f"<p class=\"scene-location\">{escape(ch['location'])}</p>")
        if ch["cast"]:
            meta.append(f"<p class=\"scene-cast\">{escape(' · '.join(ch['cast']))}</p>")
        epigraph = f"<p class=\"epigraph\">{escape(ch['epigraph'])}</p>" if ch["epigraph"] else ""
        body = "\n".join(_html_message(m["speaker"], m["content"]) for m in ch["messages"])
        sections.append(f"<section class=\"chapter\" id=\"{chapter_anchor(ch)}\">"
                        f"<h2>{escape(ch['title'])}</h2>"
                        f"{''.join(meta)}{epigraph}{body}</section>")
    if data["appendix"]:
        sections.append("<section class=\"divider\" id=\"appendix\">"
                        "<h2>Appendix</h2></section>")
        for e in data["appendix"]:
            role = f"<p class=\"actor-role\">{escape(e['role'])}</p>" if e["role"] else ""
            portrait = (f'<img class="portrait" src="{e["portrait"]}" alt="{escape(e["name"])}"/>'
                        if e["portrait"] else "")
            secs = "".join((f"<h3>{escape(s['label'])}</h3>" if s["label"] else "") + _html_md(s["text"])
                          for s in e["sections"])
            sections.append(f"<section class=\"appendix\" id=\"{appendix_anchor(e)}\">"
                            f"<h2>{escape(e['name'])}</h2>"
                            f"{role}{portrait}{secs}</section>")

    doc = (f"<!doctype html><html><head><meta charset=\"utf-8\"/>"
          f"<title>{escape(data['title'])}</title><style>{_HTML_CSS}</style></head>"
          f"<body>{''.join(sections)}</body></html>")

    for p, name in data["images"].by_path.items():
        # From the PACKED name, whose extension is what the bytes are (#321),
        # not from the file's own suffix, which may lie. A browser survives a
        # wrong data-URI mime by sniffing; that is not a reason to write one.
        # Registered names are always sniffed, so the default never fires.
        mime = EXT_MEDIA.get(name.rsplit(".", 1)[-1], "application/octet-stream")
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        doc = doc.replace(f'"images/{name}"', f'"data:{mime};base64,{b64}"')
    return doc.encode("utf-8"), f"{cid}.html"


def build_text(cid: str) -> tuple[bytes, str]:
    """A single plain-text transcript: speaker-prefixed message blocks,
    images dropped to their alt text (nothing to embed in a .txt file).

    A CONTENTS block lists every scene up front, and each chapter opens after a
    form feed (U+000C) — the plain-text page break, which is what a printer and
    a pager treat as "new chapter here", and which replaces the `---` rule this
    used to separate scenes with. The numbered title line under it is the
    visible half of the same marker, and matches the contents block."""
    data = collect(cid)  # raises CampaignNotFound

    parts = [data["title"]]
    if data["world_name"]:
        parts.append(data["world_name"])
    if data["date_range"]:
        parts.append(data["date_range"])
    header = "\n".join(parts)
    if data["chapters"]:
        header += "\n\nCONTENTS\n\n" + "\n".join(f"  {toc_label(c)}" for c in data["chapters"])

    chapters = []
    for ch in data["chapters"]:
        lines = [toc_label(ch)]
        meta_bits = [b for b in (ch["date"], ch["location"]) if b]
        if meta_bits:
            lines.append(" — ".join(meta_bits))
        if ch["cast"]:
            lines.append("Cast: " + ", ".join(ch["cast"]))
        if ch["epigraph"]:
            lines.append(ch["epigraph"])
        messages = [{**m, "content": drop_images(m["content"])} for m in ch["messages"]]
        lines.append(chronicle.transcript_text(messages))
        chapters.append("\n\n".join(lines))

    sep = "\n\n\f\n"
    body = sep + sep.join(chapters) if chapters else ""
    return (header + body + "\n").encode("utf-8"), f"{cid}.txt"


def build_json(cid: str) -> tuple[bytes, str]:
    """Machine-readable dump: scene metas + messages verbatim, chronicle, and
    roster — nearest to the on-disk data, no image resolution or rendering.

    `contents` is this format's table of contents: the scene order the book
    formats number their chapters by, stated rather than left implicit in the
    order of a JSON array a consumer may well re-sort."""
    campaign = campaigns_read.read_campaign(cid)  # raises CampaignNotFound
    sids = [s["id"] for s in sorted(scenes_read.list_scenes(cid), key=lambda s: s["id"])]
    scene_docs = [scenes_read.read_scene(cid, sid) for sid in sids]
    payload = {
        "campaign": {"id": cid, "name": campaign["meta"].get("name", cid),
                    "world": campaign["meta"].get("world", "")},
        "contents": [{"number": n, "id": sid, "title": doc["meta"].get("title", sid)}
                    for n, (sid, doc) in enumerate(zip(sids, scene_docs, strict=True), start=1)],
        "scenes": scene_docs,
        "chronicle": chronicle.read_chronicle(cid),
        "roster": appearances_cast.roster(cid),
    }
    blob = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return blob, f"{cid}.json"
