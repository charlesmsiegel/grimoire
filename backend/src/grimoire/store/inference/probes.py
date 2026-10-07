"""What a model test sends, one probe per capability (spec 6.4). Pure.

A test call is the one thing in the app that spends money to learn a fact, so
every probe is as small as a request can be and still answer its question:

- `generate` -- one user message asking for one word, the reply capped at
  `MAX_TOKENS`.
- `vision` -- the same cap, a small solid-colour PNG built here (no file is
  read) and a one-word question about it. `PROBE_EDGE` square rather than
  1x1: a picture some provider may refuse as too small would be a refusal of
  the probe, not an answer about the model, and this size is still one tile
  in the common providers' per-image charging.
- `embed` -- one short fixed string.

A probe succeeds when the provider accepted the request and the response
completed. Text is not required: a thinking model can spend a small cap
thinking and say nothing, and that is still a model that takes the request.

The cap travels as a sampler parameter (`sampling`), so
`llm_sampling.effective` translates it per adapter exactly as it would a
preset's -- `max_completion_tokens` at the OpenAI API, the required
`max_tokens` on the Anthropic API -- rather than this module knowing any wire.

`decide_native` has no probe until the native adapters land (slice H), and the
capabilities a preset states outright (`stream`, ...) are not probed at all:
`PROBES` is the whole list of what can be tested.
"""

from __future__ import annotations

import base64
import math
import struct
import zlib
from collections.abc import Iterable
from typing import NamedTuple

from ... import content_parts
from .capabilities import NAMES

#: The reply cap on every chat probe.
MAX_TOKENS = 64

GENERATE_PROMPT = "Reply with the single word: ok"
VISION_PROMPT = "What colour is this image? One word."
EMBED_TEXT = "A short sentence to embed."
#: The vision probe's picture is this many pixels on a side.
PROBE_EDGE = 64


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def _solid_png(rgb: tuple[int, int, int] = (255, 0, 0), edge: int = PROBE_EDGE) -> bytes:
    """A valid `edge` x `edge` 8-bit RGB PNG of one colour: signature, IHDR,
    IDAT, IEND. Built here, so the probe reads no file and needs no imaging
    library."""
    header = struct.pack(">IIBBBBB", edge, edge, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * edge   # filter type 0, then the row's pixels
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(row * edge)) + _chunk(b"IEND", b""))


PROBE_PNG = _solid_png()
PROBE_DATA_URI = "data:image/png;base64," + base64.b64encode(PROBE_PNG).decode("ascii")


class Probe(NamedTuple):
    capability: str
    #: `generate` goes through `LLMClient.single`; `embed` through the
    #: embeddings client.
    operation: str
    #: A stated GUESS at what one probe costs in tokens, for the preview's
    #: estimate -- not a measurement. The completion side is the cap, so it is
    #: an upper bound; the prompt side is the prompt plus a provider's framing,
    #: and for `vision` the largest per-image charge among the common
    #: providers' published schemes, which bill a small image as one tile.
    #: To be tuned against real ledger rows.
    prompt_tokens: int
    completion_tokens: int


PROBES: dict[str, Probe] = {p.capability: p for p in (
    Probe("generate", "generate", 20, MAX_TOKENS),
    Probe("vision", "generate", 300, MAX_TOKENS),
    Probe("embed", "embed", 10, 0),
)}


def ordered(caps: Iterable[str]) -> tuple[str, ...]:
    """`caps` deduplicated, in `capabilities.NAMES` order -- the order the
    probes run in and the preview lists them."""
    asked = set(caps)
    return tuple(c for c in NAMES if c in asked)


def messages(cap: str) -> list[dict]:
    """The chat messages a `generate`-operation probe sends. A fresh list per
    call: the facade and the adapters may annotate what they are handed."""
    if cap == "generate":
        return [{"role": "user", "content": GENERATE_PROMPT}]
    if cap == "vision":
        return [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": PROBE_DATA_URI}},
        ]}]
    raise ValueError(f"{cap!r} is not a chat probe")


def sampling() -> dict:
    """The `sampling` block a chat probe's connection is lowered with: the cap
    and nothing else, so no preset of the connection's own reaches a test. Named
    so a provider refusing the cap is reported as refusing a setting of the
    test's (`llm._preset_refusal`), not of a preset the user chose -- and,
    raised as `llm.PresetRefusalError`, is never filed as a verdict on the model."""
    return {"preset_id": "", "preset_name": "model test", "scope": "none",
            "params": {"max_tokens": MAX_TOKENS}}


def describe(cap: str, capped: bool = True) -> str:
    """One sentence saying what `cap`'s probe sends, for the confirmation.

    `capped` is whether the connection can be sent the reply cap at all -- the
    Claude subscription path takes no sampling options, and a confirmation
    that promised a cap it cannot send would be the one untrue line on it."""
    cap_clause = (f", with the reply capped at {MAX_TOKENS} tokens" if capped
                  else "; this connection cannot be sent a reply cap")
    if cap == "generate":
        return f"One chat message, “{GENERATE_PROMPT}”{cap_clause}."
    if cap == "vision":
        return (f"One chat message carrying a small solid-colour PNG "
                f"({PROBE_EDGE}x{PROBE_EDGE}) and “{VISION_PROMPT}”{cap_clause}.")
    if cap == "embed":
        return f"One embeddings request for the text “{EMBED_TEXT}”."
    raise ValueError(f"no probe for {cap!r}")


def _rate(value: object) -> float | None:
    """A per-token price from a catalog row, or None when it states none.

    Negative is a sentinel some catalogs use for "varies" (a router's own
    pseudo-model), never a price."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        rate = float(value)
    except ValueError:
        return None
    return rate if math.isfinite(rate) and rate >= 0 else None


def estimate_usd(row: dict | None, caps: Iterable[str]) -> float | None:
    """What probing `caps` should cost, from the model's catalog row.

    None whenever a price the estimate needs is not stated -- "a price nobody
    reported is never rendered as zero". A row stating `0` is a free model,
    which is a reported price and estimates to 0.0."""
    if not isinstance(row, dict):
        return None
    total = 0.0
    for cap in caps:
        probe = PROBES[cap]
        prompt = _rate(row.get("prompt"))
        if prompt is None:
            return None
        total += probe.prompt_tokens * prompt
        if probe.completion_tokens:
            completion = _rate(row.get("completion"))
            if completion is None:
                return None
            total += probe.completion_tokens * completion
    return total


def scrub(detail: str, secrets: Iterable[str] = ()) -> str:
    """A provider's error text as it may be stored and shown: every base64
    payload elided (the vision probe's picture can be quoted back) and any of
    `secrets` -- the connection's key -- removed."""
    out = content_parts.scrub(detail)
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[key]")
    return out
