"""Message content as parts, and the one part type that is grimoire's own (#377).

A leaf module, like `catalog` and `llm_errors`: the gateway (`llm.py`), the
provider adapters and the store all need it, and the gateway may not import
the store. Nothing here may import from the rest of the package.

## The image reference

A scene prompt that may show the model a picture carries it as

    {"type": "image_ref", "url": "/api/...", "alt": "...", "carried": bool}

-- a REFERENCE, never bytes. Everything that copies, snapshots or persists a
composed prompt (`model_guidance.PreparedMessages.snapshot`, the character-turn
resume prompts, the prompt log) holds a few dozen bytes per image, and the
bytes exist only for the length of one provider attempt. It names no campaign:
a fork copies stored prompts verbatim, so the campaign a reference resolves
against is the one running the attempt, supplied at dispatch.

## The content rule

The `text` parts of a composed message, concatenated, are EXACTLY the string
today's text-only composition produces for it -- the alt text stands in place
of each image, and a reference sits beside it rather than replacing it. A
`carrier` (a user message holding only references carried forward from an
assistant post, marked by the message key `CARRIER`) has no text at all. So
lowering for a route that cannot read images is "concatenate the text parts,
drop the carriers" (`as_text`), and that route receives today's prompt byte for
byte. Lowering for a route that can (`as_images`) turns the newest references
into OpenAI-style `image_url` parts.

Any other part type -- the image-description draft's own `image_url` parts --
is not ours to lower and passes through both functions untouched.
"""

from __future__ import annotations

import re
from collections.abc import Callable

IMAGE_REF = "image_ref"

#: The message key marking a carrier: a user message that exists only to hold
#: references moved forward from an assistant post, because the providers that
#: read images accept them in user content only.
CARRIER = "carrier"

#: What a route that reads images is told about a carried picture, so the model
#: knows it is the previous reply's rather than the player's.
CARRIED_LABEL = "[Image from the previous reply: {alt}]"

#: A `data:` URI's base64 payload, however the text around it is escaped: a
#: provider quoting the request back in JSON may write `\/` (PHP) or `\u002b`
#: (.NET), so the payload is everything up to the next delimiter rather than a
#: strict base64 class that would stop at the first escape.
_DATA_URI = re.compile(r"data:([\w/\\+.-]+);base64,[^\"'\s<>)\]]+")


def ref(url: str, alt: str, carried: bool) -> dict:
    """One image reference part."""
    return {"type": IMAGE_REF, "url": url, "alt": alt, "carried": carried}


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def text_of(content: str | list) -> str:
    """The string itself, or the concatenated `text` parts of a list."""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content
                   if isinstance(p, dict) and p.get("type") == "text")


def image_refs(content: str | list) -> list[dict]:
    """The image references in `content`, in order."""
    if isinstance(content, str):
        return []
    return [p for p in content if isinstance(p, dict) and p.get("type") == IMAGE_REF]


def has_refs(messages: list[dict]) -> bool:
    return any(image_refs(m.get("content", "")) for m in messages)


def lowerable(content: str | list) -> bool:
    """A list of only `text` and `image_ref` parts. A string needs no lowering."""
    return isinstance(content, list) and all(
        isinstance(p, dict) and p.get("type") in ("text", IMAGE_REF) for p in content)


def needs_lowering(messages: list[dict]) -> bool:
    """Whether any message must be lowered before a provider may see it.

    Not `has_refs`: a list whose references were all packed away still has to
    become a string, or a provider that flattens content would print the list.
    """
    return any(m.get(CARRIER) or lowerable(m.get("content", "")) for m in messages)


def collapse(content: str | list) -> str | list:
    """A lowerable list with no reference left, as its string; else unchanged."""
    if lowerable(content) and not image_refs(content):
        return text_of(content)
    return content


def _without_marker(message: dict, content: str | list) -> dict:
    return {k: v for k, v in message.items() if k != CARRIER} | {"content": content}


def as_text(messages: list[dict]) -> list[dict]:
    """The prompt for a route that cannot read images: a new list of new
    messages, carriers dropped and every lowerable content reduced to its text.
    Never drops any other message, however empty its text."""
    out = []
    for m in messages:
        if m.get(CARRIER):
            continue
        content = m.get("content", "")
        out.append({**m, "content": text_of(content)} if lowerable(content) else dict(m))
    return out


def _chosen(messages: list[dict], keep: int) -> set[int]:
    """`id()`s of the newest `keep` references, counted from the end."""
    refs = [p for m in messages for p in image_refs(m.get("content", ""))]
    return {id(p) for p in refs[max(0, len(refs) - keep):]} if keep > 0 else set()


def _load(load: Callable[[dict], str | None], part: dict) -> str | None:
    try:
        return load(part)
    except Exception:  # noqa: BLE001 - one picture is never worth a turn
        return None


def _lower_parts(content: list, chosen: set[int],
                 load: Callable[[dict], str | None]) -> tuple[list, int]:
    parts: list = []
    sent = 0
    for p in content:
        if not (isinstance(p, dict) and p.get("type") == IMAGE_REF):
            parts.append(p)
            continue
        url = _load(load, p) if id(p) in chosen else None
        if url is None:
            continue
        if p.get("carried"):
            parts.append(_text(CARRIED_LABEL.format(alt=p.get("alt", ""))))
        parts.append({"type": "image_url", "image_url": {"url": url}})
        sent += 1
    return parts, sent


def as_images(messages: list[dict], keep: int,
              load: Callable[[dict], str | None]) -> tuple[list[dict], int]:
    """The prompt for a route that can read images, and how many it carries.

    The newest `keep` references become `image_url` parts with the data URI
    `load` returns; older ones, and any `load` cannot produce (None or an
    exception), are dropped -- their alt text is already in the text. A content
    left with no image collapses to its string; a carrier left with none is
    dropped. Never mutates `messages`.
    """
    chosen = _chosen(messages, keep)
    out: list[dict] = []
    total = 0
    for m in messages:
        content = m.get("content", "")
        if not lowerable(content):
            if not m.get(CARRIER):
                out.append(dict(m))
            continue
        parts, sent = _lower_parts(content, chosen, load)
        total += sent
        if sent:
            out.append(_without_marker(m, parts))
        elif not m.get(CARRIER):
            out.append(_without_marker(m, text_of(content)))
    return out, total


def scrub(text: str) -> str:
    """`text` with every base64 `data:` payload elided.

    A provider's 4xx body can quote the request back, and an error detail goes
    to the log, the error store and the reader -- none of which may hold the
    bytes of a private picture.
    """
    return _DATA_URI.sub(lambda m: f"data:{m.group(1)};base64,[elided]", text)
