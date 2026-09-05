"""A model-drafted first pass at what a picture shows.

The author writes the description; this only offers a starting point, and
nothing here writes to the store. `routes` calls `build_prompt`, sends it, and
hands `parse_output` back to the browser as a *preview* — the same shape
`taglines` and `voice_anchors` already use, and for the same reason: a draft
nobody has read must never become stored content (#59).

## Only two of the three connection kinds can carry an image

`llm.LLMClient` passes `messages` straight into the provider payload for
`openrouter` and `openai_compatible`, so OpenAI-style content parts work with
no client change at all. `claude_agent` cannot: it joins ``m["content"]`` as a
string to build its prompt, so a list would raise deep inside the SDK path and
surface as a 500.

Hence `SUPPORTED_KINDS`, checked in the route. Widening `claude_agent` to
multimodal is a real change with its own testing, and it is not this feature's
to make in passing — a clear refusal is worth more than a crash, and worth much
more than a silently-wrong prompt.

## The bytes go in the request, not a URL

A `data:` URI, because the provider cannot reach this machine: the store is
local (often a synced folder), the app is served on localhost, and the image
URL this app would hand over resolves to nothing from the outside. That makes
the request large — an image is megabytes — which is exactly why this runs once
per button press, on one image, and never on a sweep.
"""

from __future__ import annotations

import base64
from pathlib import Path

from .. import prompts

#: Connection kinds whose client passes content parts through untouched.
SUPPORTED_KINDS: tuple[str, ...] = ("openrouter", "openai_compatible")

#: Shown to the user when the active connection is the one that cannot.
UNSUPPORTED = ("this connection cannot read images — switch to an OpenRouter "
               "or OpenAI-compatible connection to draft a description")

#: The media types `assets` will store, by stored extension. Not
#: `mimetypes.guess_type`: that reads the *name*, and the name here is a stem
#: the store chose, while the extension is what `routes.common._upload_image_ext`
#: derived from the bytes themselves.
MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
         "gif": "image/gif", "webp": "image/webp"}


#: Ceiling on an image this will encode, and the same number and the same
#: reason as ``image_library.MAX_BYTES``: the backend is packaged verbatim
#: into the Android app (Chaquopy), and one draft holds the file THREE times
#: over -- the bytes, their base64 buffer, and the ~4/3-sized string that then
#: sits in the request payload. Only the campaign library caps its uploads, so
#: a record image (or any file a sync client dropped into the store) can be
#: arbitrarily large, and on a phone that is a killed process rather than an
#: error anyone can act on (PR review). Checked from ``stat`` BEFORE the read,
#: because a cap enforced after reading protects nothing.
MAX_BYTES = 25 * 1024 * 1024

#: What the route turns into a 413 when the picture is past `MAX_BYTES`.
TOO_LARGE = "image is too large to describe (max 25 MB)"


class ImageTooLargeError(Exception):
    """`path` is bigger than `MAX_BYTES` (HTTP 413).

    ``image_library.ImageTooLarge``'s idea under the name the lint gate wants
    from new code: that one predates the widened ruff selection and sits in the
    baseline, and renaming it would be a public-API change for another module.
    """


def data_uri(path: Path) -> str:
    """`path`'s bytes as a `data:` URI, typed by its stored extension."""
    ext = path.suffix.lstrip(".").lower()
    media = MEDIA.get(ext)
    if media is None:
        raise ValueError(f"unsupported image type: {ext}")
    # ONE open, sized and read through the same handle. `stat()` then a separate
    # `read_bytes()` is a check-then-act: a sync client replacing the file in
    # between gets the replacement read with no bound at all, which is the whole
    # thing the cap exists to prevent (PR review). The read asks for one byte
    # past the cap rather than trusting the size it just measured, so a file
    # growing under an append still cannot get past this.
    with path.open("rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ImageTooLargeError(TOO_LARGE)
    return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"


def build_prompt(path: Path, subject: str = "") -> list[dict]:
    """The messages one draft is asked for.

    `subject` is what the record is called — the character, the location, the
    campaign — and it is passed because a caption that can use the right name
    is worth far more for retrieval than one that says "a woman". It is not
    presented as ground truth: the template asks the model to describe what it
    can see and offers the name only as a label for the subject if one is
    there.
    """
    return [
        {"role": "system", "content": prompts.render("image_description/system.j2")},
        {"role": "user", "content": [
            {"type": "text",
             "text": prompts.render("image_description/user.j2", subject=subject)},
            {"type": "image_url", "image_url": {"url": data_uri(path)}},
        ]},
    ]


def parse_output(text: str) -> str:
    """The draft as one paragraph of plain text.

    Joined rather than first-line-only (`taglines.parse_output`'s rule): a
    description is allowed to be two or three sentences, and a model that puts
    them on separate lines has not made a mistake worth discarding half of.
    """
    return " ".join(ln.strip() for ln in text.strip().splitlines() if ln.strip())
