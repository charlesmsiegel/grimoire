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
import json
import re
import zipfile
from pathlib import Path

import markdown as _md_lib
from markupsafe import Markup, escape

from . import appearances, assets, calendars, campaigns, characters, entities, overlay, pcs, scenes, worlds
from ..prompts import templates_dir
from .paths import now_iso

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

_EXT_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
              "gif": "image/gif", "webp": "image/webp"}


@functools.lru_cache(maxsize=1)
def _env():
    # A separate environment from prompts._env: book pages need autoescape.
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    return Environment(loader=FileSystemLoader(str(templates_dir())),
                       undefined=StrictUndefined, autoescape=True)


def _render(template: str, **vars) -> str:
    return _env().get_template(f"epub/{template}").render(**vars)


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
            location = overlay.read_entity(cid, "locations", hist[0])["meta"].get("name")
        except entities.EntityNotFound:
            pass  # deleted location: header line silently omitted
    cast = [a["name"] for a in appearances.scene_cast(cid, sid)]
    body = "\n".join(
        _message_html(m.get("speaker"), _rewrite_images(m["content"], croot, wroot, images))
        for m in scene["messages"])
    doc = _render("chapter.xhtml", title=title, date=date, location=location,
                  cast=cast, epigraph=meta.get("one_line") or None, body=Markup(body))
    return {"file": f"chapter-{number:03d}.xhtml", "title": title, "doc": doc}


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
        except (json.JSONDecodeError, characters.CharacterNotFound,
                characters.VersionNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
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
            ent = overlay.read_entity(cid, "locations", eid)
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
