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

from . import appearances, calendars, campaigns, chronicle, characters, entities, overlay, pcs, scenes, worlds
from .paths import slugify

# Localized app image URLs (see store.localize): every shape the app writes.
_IMG_URL = re.compile(
    r"/api/(?:worlds|campaigns)/[^/\s]+/(?:"
    r"characters/(?P<char>[^/\s]+)/versions/(?P<vid>[^/\s]+)"
    r"|greetings/(?P<gid>[^/\s]+)"
    r"|(?P<kind>locations|lore)/(?P<eid>[^/\s]+)"
    r")/images/(?P<name>[^/\s?#]+)")

_MD_IMG = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")

EXT_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "gif": "image/gif", "webp": "image/webp"}


class Images:
    """Registry of packed images: disk path -> packed images/ name."""

    def __init__(self):
        self.by_path: dict[Path, str] = {}

    def add(self, p: Path) -> str:
        if p not in self.by_path:
            self.by_path[p] = f"img-{len(self.by_path):03d}{p.suffix.lower()}"
        return self.by_path[p]


def _resolve_image(cid: str, m: re.Match) -> Path | None:
    """Map a localized app URL to a disk file through the campaign overlay:
    campaign tree first, then the campaign's world, with a campaign asset
    tombstone hiding an inherited image (greeting images only live world-side)."""
    if m["char"]:
        rid, vid, base = m["char"], m["vid"], "characters"
    elif m["gid"]:
        rid, vid, base = m["gid"], "default", "greetings"
    else:
        rid, vid, base = m["eid"], "default", m["kind"]
    root = overlay.image_root(cid, rid, vid, m["name"], base=base)
    from . import assets
    return assets.image_path(root, rid, vid, m["name"], base=base)


def rewrite_images(text: str, cid: str, images: Images, prefix: str = "images/") -> str:
    """Point every markdown image at its packed copy under `prefix`; remote or
    missing images degrade to their alt text (a broken img is worse)."""
    def sub(m: re.Match) -> str:
        app = _IMG_URL.match(m["url"])
        if app:
            p = _resolve_image(cid, app)
            if p is not None:
                return f"![{m['alt']}]({prefix}{images.add(p)})"
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
    from . import assets
    root = overlay.image_root(cid, rid, vid, assets.AVATAR, base=base)
    p = assets.image_path(root, rid, vid, assets.AVATAR, base=base)
    return f"{prefix}{images.add(p)}" if p is not None else None


def _actor_sections(croot: Path, kind: str, actor_id: str, vid: str) -> tuple[str, list[dict]]:
    """(display name, labeled sections) — the reader-facing cast_detail field
    set; prompt plumbing is deliberately excluded."""
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


def _chapter(cid: str, provider, sid: str, number: int, images: Images, prefix: str) -> dict:
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
    messages = [{"role": m["role"], "speaker": m.get("speaker"),
                "content": rewrite_images(m["content"], cid, images, prefix)}
                for m in scene["messages"]]
    return {"sid": sid, "number": number, "title": title, "date": date, "location": location,
            "cast": cast, "epigraph": meta.get("one_line") or None, "messages": messages}


def _appendix_entries(cid: str, croot: Path, sids: list[str], images: Images, prefix: str) -> list[dict]:
    entries: list[dict] = []
    roster = sorted(appearances.roster(cid),
                    key=lambda a: (a["role"] != "player", a["kind"], a["id"]))
    for a in roster:
        try:
            name, sections = _actor_sections(croot, a["kind"], a["id"], a["version"])
        except (json.JSONDecodeError, characters.CharacterNotFound,
                characters.VersionNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            continue  # unreadable actor: skip the entry, never fail the export
        portrait = (_avatar(cid, a["id"], a["version"], "characters", images, prefix)
                    if a["kind"] == "characters" else None)
        entries.append({
            "kind": a["kind"], "id": a["id"], "name": name, "portrait": portrait,
            "role": "Player character" if a["role"] == "player" else None,
            "sections": [{"label": s["label"], "text": rewrite_images(s["text"], cid, images, prefix)}
                        for s in sections],
        })

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
    campaign = campaigns.read_campaign(cid)
    croot = campaigns.campaign_root(cid)
    wid = campaign["meta"].get("world", "")
    wroot = worlds.world_root(wid) if wid else None
    if wroot is not None and not wroot.exists():
        wroot = None
    world_name = worlds.read_world(wid)["meta"].get("name", "") if wroot is not None else ""
    provider = calendars.get_provider(calendars.read_calendar(croot)["primary"])
    images = Images()

    sids = [s["id"] for s in sorted(scenes.list_scenes(cid), key=lambda s: s["id"])]
    chapters = [_chapter(cid, provider, sid, i, images, image_prefix)
                for i, sid in enumerate(sids, start=1)]
    appendix = _appendix_entries(cid, croot, sids, images, image_prefix)

    # in-world date range: first dated scene's start — last dated scene's latest
    histories = [h for sid in sids if (h := scenes.get_time_history(cid, sid))]
    date_range = None
    if histories:
        first = _friendly_or_none(provider, histories[0][0])
        last = _friendly_or_none(provider, histories[-1][-1])
        if first and last:
            date_range = first if first == last else f"{first} — {last}"

    return {
        "title": campaign["meta"].get("name", cid),
        "world_name": world_name,
        "date_range": date_range,
        "updated": campaign["meta"].get("updated", ""),
        "chapters": chapters,
        "appendix": appendix,
        "images": images,
    }


def chapter_filename(ch: dict, ext: str) -> str:
    return f"{ch['number']:03d}-{slugify(ch['title'])}.{ext}"


def appendix_filename(e: dict, ext: str) -> str:
    if e["kind"] == "locations":
        return f"location-{e['id']}.{ext}"
    return f"actor-{e['kind']}-{e['id']}.{ext}"


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
    convention — a portable, human-editable bundle."""
    data = collect(cid)  # raises CampaignNotFound; image_prefix="images/"

    index = [f"# {data['title']}"]
    if data["world_name"]:
        index.append(f"*{data['world_name']}*")
    if data["date_range"]:
        index.append(data["date_range"])
    if data["chapters"]:
        index.append("## Scenes")
        index += [f"- [{c['title']}]({chapter_filename(c, 'md')})" for c in data["chapters"]]
    if data["appendix"]:
        index.append("## Appendix")
        index += [f"- [{e['name']}]({appendix_filename(e, 'md')})" for e in data["appendix"]]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.md", "\n\n".join(index) + "\n")
        for c in data["chapters"]:
            lines = [f"# {c['title']}", *_header_lines(c), chronicle.transcript_text(c["messages"])]
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


def build_html(cid: str) -> tuple[bytes, str]:
    """A single self-contained HTML page: every image inlined as a base64
    data URI so the file has no external dependencies."""
    data = collect(cid)  # raises CampaignNotFound; image_prefix="images/"

    sections = [f"<section class=\"titlepage\"><h1>{escape(data['title'])}</h1>"
                + (f"<p class=\"world\">{escape(data['world_name'])}</p>" if data["world_name"] else "")
                + (f"<p class=\"daterange\">{escape(data['date_range'])}</p>" if data["date_range"] else "")
                + "</section>"]
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
        sections.append(f"<section class=\"chapter\"><h2>{escape(ch['title'])}</h2>"
                        f"{''.join(meta)}{epigraph}{body}</section>")
    if data["appendix"]:
        sections.append("<section class=\"divider\"><h2>Appendix</h2></section>")
        for e in data["appendix"]:
            role = f"<p class=\"actor-role\">{escape(e['role'])}</p>" if e["role"] else ""
            portrait = (f'<img class="portrait" src="{e["portrait"]}" alt="{escape(e["name"])}"/>'
                        if e["portrait"] else "")
            secs = "".join((f"<h3>{escape(s['label'])}</h3>" if s["label"] else "") + _html_md(s["text"])
                          for s in e["sections"])
            sections.append(f"<section class=\"appendix\"><h2>{escape(e['name'])}</h2>"
                            f"{role}{portrait}{secs}</section>")

    doc = (f"<!doctype html><html><head><meta charset=\"utf-8\"/>"
          f"<title>{escape(data['title'])}</title><style>{_HTML_CSS}</style></head>"
          f"<body>{''.join(sections)}</body></html>")

    for p, name in data["images"].by_path.items():
        ext = p.suffix.lower().lstrip(".")
        mime = EXT_MEDIA.get(ext, "application/octet-stream")
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        doc = doc.replace(f'"images/{name}"', f'"data:{mime};base64,{b64}"')
    return doc.encode("utf-8"), f"{cid}.html"


def build_text(cid: str) -> tuple[bytes, str]:
    """A single plain-text transcript: speaker-prefixed message blocks,
    images dropped to their alt text (nothing to embed in a .txt file)."""
    data = collect(cid)  # raises CampaignNotFound

    parts = [data["title"]]
    if data["world_name"]:
        parts.append(data["world_name"])
    if data["date_range"]:
        parts.append(data["date_range"])
    header = "\n".join(parts)

    chapters = []
    for ch in data["chapters"]:
        lines = [ch["title"]]
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

    body = "\n\n" + "\n\n---\n\n".join(chapters) if chapters else ""
    return (header + body + "\n").encode("utf-8"), f"{cid}.txt"


def build_json(cid: str) -> tuple[bytes, str]:
    """Machine-readable dump: scene metas + messages verbatim, chronicle, and
    roster — nearest to the on-disk data, no image resolution or rendering."""
    campaign = campaigns.read_campaign(cid)  # raises CampaignNotFound
    sids = [s["id"] for s in sorted(scenes.list_scenes(cid), key=lambda s: s["id"])]
    payload = {
        "campaign": {"id": cid, "name": campaign["meta"].get("name", cid),
                    "world": campaign["meta"].get("world", "")},
        "scenes": [scenes.read_scene(cid, sid) for sid in sids],
        "chronicle": chronicle.read_chronicle(cid),
        "roster": appearances.roster(cid),
    }
    blob = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return blob, f"{cid}.json"
