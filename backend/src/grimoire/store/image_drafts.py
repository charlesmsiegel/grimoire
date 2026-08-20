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


def data_uri(path: Path) -> str:
    """`path`'s bytes as a `data:` URI, typed by its stored extension."""
    ext = path.suffix.lstrip(".").lower()
    media = MEDIA.get(ext)
    if media is None:
        raise ValueError(f"unsupported image type: {ext}")
    return f"data:{media};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


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
