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
