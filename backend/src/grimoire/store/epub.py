"""Campaign → EPUB 3 export: one chapter per scene, embedded images and fonts,
and an appendix for every entity that appeared (cast actors + visited locations).

The book is assembled in memory: `export.collect` walks scenes/appendix once
into format-neutral data (shared with the other export renderers), markdown
bodies become XHTML fragments (the `markdown` package), pages render from
Jinja templates in <repo>/templates/epub/, and everything packs with stdlib
zipfile — `mimetype` first and uncompressed, per the EPUB OCF spec. Nothing is
written into the store.
"""

from __future__ import annotations

import functools
import io
import zipfile
from pathlib import Path

import markdown as _md_lib
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup, escape

from ..prompts import templates_dir
from . import export as _export
from .export import EXT_MEDIA as _EXT_MEDIA
from .paths import now_iso

FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"  # paths-ok: package-relative, so the fonts ship inside the wheel


@functools.lru_cache(maxsize=1)
def _env():
    # A separate environment from prompts._env: book pages need autoescape.
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


def _chapter_doc(ch: dict) -> dict:
    body = "\n".join(_message_html(m["speaker"], m["content"]) for m in ch["messages"])
    doc = _render("chapter.xhtml", title=ch["title"], date=ch["date"], location=ch["location"],
                  cast=ch["cast"], epigraph=ch["epigraph"], body=Markup(body))
    return {"file": f"chapter-{ch['number']:03d}.xhtml", "title": ch["title"], "doc": doc}


def _appendix_doc(e: dict) -> dict:
    doc = _render("appendix.xhtml", name=e["name"], portrait=e["portrait"], role=e["role"],
                  sections=[{"label": s["label"], "html": Markup(_md(s["text"]))} for s in e["sections"]])
    file = (f"actor-{e['kind']}-{e['id']}.xhtml" if e["kind"] in ("characters", "pcs")
            else f"location-{e['id']}.xhtml")
    return {"file": file, "title": e["name"], "doc": doc}


def build_epub(cid: str) -> tuple[bytes, str]:
    """The whole campaign as an EPUB 3 book: (bytes, suggested filename)."""
    data = _export.collect(cid, image_prefix="../images/")  # raises CampaignNotFound
    images = data["images"]
    chapters = [_chapter_doc(c) for c in data["chapters"]]
    appendix = [_appendix_doc(e) for e in data["appendix"]]

    title = data["title"]
    docs = [("text/titlepage.xhtml",
             _render("titlepage.xhtml", title=title, world=data["world_name"],
                    date_range=data["date_range"]))]
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
                  modified=data["updated"] or now_iso(),
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
