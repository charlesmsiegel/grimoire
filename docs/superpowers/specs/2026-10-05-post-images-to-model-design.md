# Send post images to the model (#377)

## Intent and scope

A post can carry a picture (#376): a reader inserts one with `PostImagePicker`,
or the narrator writes an art handle that `context.art.resolve_handles` rewrites
to `![description](/api/...)`. Today every one of them reaches the model as its
alt text — `context.story._project_history` runs `export.drop_images` over each
history line. That stays the behaviour by default.

This change adds a setting. When it is on **and** the connection an attempt
runs on can read images, the newest images in the history that is actually sent
go to the model as image content parts, beside the alt text they already carry.
A route that cannot read images receives exactly the text it receives today.

In scope: the scene-turn family of prompts — everything composed through
`context.assemble._assemble` (ordinary turns, director turns, retries and
regenerations, character turns, mechanics follow-ups), and the frozen snapshots
those turns are replayed from. Out of scope: absorb, audit, dossiers, the
tracker, summaries, image-description drafts (which already send a picture of
their own), and the opener (it carries no history). Nothing about how posts
store images changes.

Success:

- Off (the default), every composed prompt is byte-identical to today's.
- On, with a vision-capable route, the newest `send_images_limit` images in the
  sent history window go out as `image_url` parts, always inside a **user**
  message; older ones stay alt text.
- Any route that cannot read images — a text-only primary, a text-only
  fallback, or the primary again after a provider rejected an image — receives
  the identical text today's code would send, with the identical packing.
- The packer, the inspector and the budget bar count what the images cost, and
  images are the first thing given up under budget pressure.
- Nothing that persists a prompt (prompt snapshots, character-turn resume
  prompts, logs, error records) ever holds image bytes.

Stated limits:

- The decision to compose with images is made for the **primary** connection.
  A vision-capable fallback behind a text-only primary receives text.
- A turn replayed from a frozen snapshot (reroll, resume) sends images only if
  the setting is on *now*, capped by the limit *now*.

## Settings

Two keys in `store/config.py`'s `_CONFIG_KEYS`, defaults in `read_config`,
reported by `routes.config._public_config`, writable through `ConfigUpdate`:

- `send_images` — `"on"` / `"off"`, default `"off"`. Validated by joining
  `routes.config._ON_OFF_KEYS`. Off by default because an image is real money
  on every turn it stays in the window, and turning that on behind someone's
  back is the same kind of imposition `backup_enabled` refuses to make.
- `send_images_limit` — a non-negative integer, default `"3"`. `0` sends none
  (equivalent to off). Read fail-soft like `context_budget`: a malformed or
  negative value reads as the default rather than raising. Three because one
  image is rarely the whole scene (a map and the room it shows) and every extra
  one is paid on every turn until it ages out; it is configuration because the
  right trade depends on the reader's model price, and the default should be
  tuned against real play.

`_public_config` also reports `send_images_reach`, the effective answer for the
connection the **global `chat` route** resolves to (what a turn in a campaign
with no route of its own runs on, through `store/routing.py`; the active
connection when chat is not routed): `"off"` (setting off or limit 0), `"yes"`,
`"no"` (the
connection or its catalog says text-only), or `"unknown"` (auto, and the
catalog does not say — no cached list, no matching row, or a row without the
field). `ConfigView` shows a checkbox ("Send post images to models that can
read them") and the limit (disabled while the checkbox is off) in the
**Context** section, and under them a hint for `"no"` / `"unknown"` saying
images are not being sent and why ("the active connection's model is not known
to read images — open it under Connections to refresh its model list, or set
Reads images there"). Turning the setting on is otherwise silent when nothing
can read images; this line is what makes it not silent. A campaign whose chat
is routed elsewhere gets its per-turn answer from the scene inspector, whose
Images row is present exactly when images would be sent to that scene's routed
connection. (The inspector does not carry `send_images_reach` itself: its live
payload is pinned to the shape of a frozen prompt snapshot, which records what
was sent, not a setting.)

## Which connections can read images

### The catalog says, where it can

`catalog.entry` gains `vision: bool | None`, read from
`architecture.input_modalities` when the provider publishes it (OpenRouter
does; an OpenAI-compatible endpoint occasionally does through the same field):
`True` when the list contains `"image"`, `False` when it is a list without it,
`None` otherwise. `None` is "the provider did not say", in the same spirit as an
unpriced model being `None` rather than `0`. The frontend `Model` type gains
`vision?: boolean | null`. Sidecars written before this change lack the key and
read as unknown.

### The connection can override

`store.llm_connections._FIELDS` gains `vision`, stored as `""` (auto), `"on"`
or `"off"`; anything else read from disk is auto. `ConnectionCreate` /
`ConnectionUpdate` accept it as `Literal["", "on", "off"]`, the same way they
already constrain `post_process`, so any other value is refused at validation.
`ConnectionForm` shows a "Reads images" select (Auto / Yes / No) for
`openrouter` and `openai_compatible` connections, with a hint stating what the
catalog says about the selected model (reads images / text only / not stated).
A `claude` connection does not show it.

Editing a connection clears its catalog sidecar (`_write_raw`), so a connection
on auto reads as unknown until its list is fetched again; the editor already
refetches an OpenRouter list when opened, and `send_images_reach` is what tells
the reader when auto has nothing to go on. That is accepted rather than
building a second, model-keyed cache.

### One resolver

`store/post_images.py` (new) owns the decision. It does not import the gateway
(`llm.py`); the store never does (#239), and `image_drafts.SUPPORTED_KINDS` is
the store-side statement of which kinds can carry a part.

```python
def capability(conn: dict | None) -> str   # "yes" | "no" | "unknown"
```

- `None`, or a kind not in `image_drafts.SUPPORTED_KINDS` (i.e. `claude`) →
  `"no"`, whatever the override says: that client flattens content to a string.
- `vision == "on"` → `"yes"`; `vision == "off"` → `"no"`.
- Auto → the cached catalog row (`llm_connections.cached_models(conn["id"])`)
  whose `id` equals `conn["model"]`: `vision is True` → `"yes"`, `False` →
  `"no"`, anything else (no list, no row, `None`) → `"unknown"`.

Never raises: a sidecar that cannot be read is `"unknown"`.

```python
def limit() -> int                        # send_images_limit, or 0 when off
def images_for(conn: dict | None) -> int  # limit() if capability(conn) == "yes" else 0
```

`images_for` is the one number composition takes, and — re-evaluated per
attempt — the one number dispatch takes. Sending image parts to a text-only
endpoint is a 400, not a graceful degradation, so `"unknown"` sends nothing.

## Message content: a third shape

Message content is a string today, except the image-description draft, which
sends a list of OpenAI-style parts. This change adds a grimoire-internal part:

```python
{"type": "image_ref", "url": "/api/campaigns/<cid>/images/<name>", "alt": "…",
 "carried": False}
```

A **reference**, never bytes, so everything that copies, snapshots or persists
a composed prompt (`PreparedMessages.snapshot`, `responses.save_resume_prompt`,
`prompt_log`) holds a few dozen bytes per image. It names **no campaign**: a
fork copies `response-prompts/` verbatim (`store/fork.py`), and a ref that
named its campaign would make a reroll in the fork load the source's picture —
or a stranger's, once the source's slug is reused. The campaign a ref resolves
against is the one running the attempt (below), which is the fork rule
`export._resolve_image` already follows.

### Images only ever sit in user messages

OpenAI's chat format, Anthropic's (directly and through OpenRouter) and the
common OpenAI-compatible servers accept image parts in **user** content only;
an assistant message carrying one is a 400. Narrator art lives in assistant
posts, so:

- An image in a **user** history message stays in that message: an `image_ref`
  part immediately after the text part that ends with its alt text.
- An image in an **assistant** history message is *carried*: its `image_ref`
  (`carried: True`) is placed at the **start** of the next user message in the
  projected history. When the assistant message is the last one in the history
  window, the refs ride on a **carrier**: a user message appended after the
  history, marked `"carrier": True`, whose content is only those refs. When the
  prompt then appends a user message of its own before the post-history block
  (a director note, `before_post`), the carried refs are prepended to *that*
  message instead and no carrier is emitted, so the carrier never sits beside
  another user message.

The content rule that makes lowering trivial: **the text parts of a message,
concatenated, are exactly the string today's code produces for it** (alt text in
place of each image, in its own message; an empty string stays an empty `text`
part rather than vanishing), and a carrier has no text parts at all. Lowering
to text is therefore "concatenate text parts; drop messages marked as
carriers" — the carrier vanishes and every other message becomes today's
string, an empty one included. Lowering for a route that reads images emits, for a carried ref, a
short text part naming it as the image from the previous reply
(`[Image from the previous reply: <alt>]`) before the picture, so the model
knows whose picture it is; an inline ref needs none, since its alt text is
already right in front of it.

### The leaf module

`grimoire/content_parts.py` (imports nothing from the package, like `catalog`
and `llm_errors`, so `llm.py`, the adapters and `store` may all use it) owns the
shape:

- `IMAGE_REF = "image_ref"`
- `text_of(content) -> str` — the string itself, or the concatenated `text`
  parts.
- `image_refs(content) -> list[dict]` — the `image_ref` parts, in order.
- `has_refs(messages) -> bool`.
- `lowerable(content) -> bool` — a list of only `text` / `image_ref` parts.
- `needs_lowering(messages) -> bool` — any message is a carrier or has
  lowerable list content. This, not `has_refs`, is what makes dispatch lower:
  a list left with only text parts must still become a string before it
  reaches a provider (`claude_agent._flatten` would f-string a list).
- `as_text(messages) -> list[dict]` — a NEW list of NEW message dicts: carrier
  messages dropped, every lowerable list content becomes `text_of`, anything
  else copied as is. Never drops a non-carrier message, however empty.
- `as_images(messages, keep, load) -> tuple[list[dict], int]` — a new list:
  the newest `keep` `image_ref` parts (counted across all messages from the end)
  become `{"type": "image_url", "image_url": {"url": load(part)}}` (with the
  carried label before a carried one); older refs, and refs whose `load`
  returns `None` or raises, are dropped. A content left with no image part
  collapses to `text_of`; a carrier left with no image is dropped. The
  `carrier` marker is removed from what is returned (it is ours, not the
  provider's). Returns how many images were actually included. Never mutates
  its input.
- `scrub(text) -> str` — replaces every `data:<type>;base64,<payload>` run with
  `data:<type>;base64,[elided]`.

Every consumer of a composed message's `content` either receives a string by
construction or goes through `text_of`: `pack.message_cost` and `pack.pack`'s
`hist_costs`, `assemble._breakdown`, `assemble._token_memo` (whose key must be
the text, not the list), `routes.character_turns._capture`,
`openai_compatible._strict_messages`, and `claude_agent` (which only ever
receives lowered text). The plan re-derives this list by search rather than
trusting it.

## Composition

`_assemble(..., images: int = 0)`. With `images == 0` it calls
`story._project_history` exactly as today, untouched. Otherwise it calls a
sibling, `story._project_history_refs(messages, *, images, cid)`, and:

1. **Choose.** Walk the scene's messages newest to oldest, skipping director
   notes as today, and choose up to `images` markdown images whose URL is one of
   the app's own image URLs, resolves to a file in this campaign, and whose
   first bytes sniff as an image format we can label (below). Occurrences are
   counted, not distinct files.
2. **Mark.** In each chosen message, replace each chosen image's markdown with
   its alt text followed by a sentinel, `⟦img:<nonce>:<n>⟧`, where `<nonce>` is
   a fresh `secrets.token_hex(8)` per composition — a post cannot forge one, so
   no character ever has to be stripped from the text. Every unchosen image is
   reduced to alt text exactly as `drop_images` does today. `_project_history`
   returns the projected messages (strings, today's merge rules) and the ref
   table `{n: {"url", "alt", "carried"}}`.
3. **Expand.** `_assemble` runs `_expanded` over the strings as today; macro
   expansion only touches `{{…}}`, so a sentinel passes through. A sentinel that
   sat *inside* a macro and was consumed by it is simply gone, and so is that
   image (its alt text went the same way it goes today).
4. **Split.** `_assemble` splits each string on this composition's sentinels
   into `text` / `image_ref` parts (removing the sentinel text, so the text
   parts concatenate to the `images=0` string), moves carried refs from
   assistant messages to the start of the next user message, and appends a
   carrier if refs are left over at the end. A string with no surviving
   sentinel stays a string.

Choosing newest-first over the whole scene and then letting the packer trim
from the front gives exactly "the newest N in the sent window": the packer only
ever removes projected messages from the front, so the window is a suffix and an
image that survives has no newer unchosen image behind it.

`compose_turn`, `compose_director_turn`, `build_messages`,
`build_director_messages` and `context_breakdown` take `images: int = 0` and
pass it to `_assemble`. Every scene-turn route that passes
`model=effective_model(conn)` also passes
`images=store.post_images.images_for(conn)`, the inspector routes included, so
the live Context view describes what the next turn would send.

### Which URLs count

Only the app's own localized image URLs — the shapes `export._IMG_URL` and
`export._COLLECTION_URL` match — and only when they resolve to a file. The
resolution is the export's own, against the campaign being played rather than
the id written in the URL. It is factored out of `export.rewrite_images` into
`export.resolve_url(cid, url) -> Path | None`, which both now call, so the two
cannot drift. The sniff at step 1 is `export.packed_ext` over the first bytes,
the same detector the export names a packed image with, so an image the export
would refuse is not given a slot — narrowed further to the formats this build's
Pillow can decode (`post_images.decodable`): a WebP is only eligible where
`PIL.features.check("webp")` holds, since Chaquopy's Android Pillow has no
libwebp, and a slot given to a picture that cannot be decoded is a slot an
older, sendable one was denied. A remote `https://` image is never sent:
handing a third-party URL to a provider is a fetch the reader did not ask for.
It stays alt text.

## Packing: images give way first

`pack.message_cost(content, count)` charges `count(text_of(content)) +
IMAGE_TOKENS * len(image_refs(content)) + MESSAGE_OVERHEAD`.

`pack.pack` gets a step before every other: **while over budget and any image
remains, the oldest remaining `image_ref` is removed**. A content left with no
ref collapses to `text_of` (a string, exactly today's), and a carrier left with
none is removed outright — so the packed history never holds a list that is
only text, nor an empty carrier. `pack` edits **copies**: `_prepare` packs the
same history once per guidance profile, so a message `pack` changes is replaced
by a new dict in its own output list, never edited in place. Carrier removal
here is image removal, counted in `dropped_tokens` and never in
`history_trimmed`. Only then does today's algorithm run, on costs that are by
then exactly today's text costs. So under budget pressure a picture is given up
before a section or a line of history, and a route that receives the text
lowering gets byte-for-byte the packing today's code produces: if every image
was removed, the rest of the run is today's; if removing some was enough, the
text alone was already under budget, so today's run would have dropped nothing
either. The removed images' tokens count toward `dropped_tokens`.

`IMAGE_TOKENS = 1600` is a flat **estimate** for one image bounded at
`SEND_EDGE` pixels on its longer side, in the range the common providers'
published per-image costs fall in at that size. It is not a guarantee in either
direction; it is a constant to be tuned against real provider-reported prompt
tokens, and its docstring says so.

## The inspector and the bar

`_breakdown` reports the history row with `text_of` each kept non-carrier
message as its text (a carrier contributes no blank entry to the join) and `sum(count(text_of) + MESSAGE_OVERHEAD)` as its tokens, and adds an
**Images** row — `id: "history_images"`, `label: "Images (n)"`, tier `history`,
text listing each kept ref's alt text and URL, tokens `n * IMAGE_TOKENS` — when
the kept history carries any. `total_tokens` is computed as today, from
`message_cost`, which already includes the images; the Images row is a split of
that history figure, never added beside it. The budget bar needs no change: a
`history` row lands in its Conversation bucket, so the images are drawn rather
than left out.

What the record cannot say ahead of time, and states rather than hides:

- A route that receives the text lowering is recorded with the composed
  breakdown, which counts images it was not sent.
- An image that fails to load at dispatch (below) is counted but not sent.

The ledger's `images` count (below) is the after-the-fact truth for both.

## Dispatch

`LLMClient` gains two injected callables, on the established pattern
(`timeout`, `retries`, `fallback`, `observer`, `capture`), which keeps the
gateway store-free:

- `images: Callable[[dict], int]` — `store.post_images.images_for`: how many
  images this connection may be sent right now (0 = none).
- `load_image: Callable[[str, dict], str | None]` — `store.post_images.load`,
  given the campaign id and an `image_ref`, a `data:` URI or `None`.

Both are guarded like `_fallback`: a resolver that raises is logged and read as
0 / `None`, because `_resilient` only catches `LLMError` and a broken resolver
must not fail a generation the text would have served.

The campaign comes from the prompt: `PreparedMessages` gains a `campaign`
attribute set by the scene composers (and by `from_snapshot(..., campaign=)`,
which every snapshot restore passes the running campaign's id to), carried
through `with_appended`. A plain message list has none and never carries refs.

`_dispatch(messages, conn, usage)` reads `messages.campaign` first (the
variant `for_model` returns is a plain list), selects the model variant as
today, then:

- `needs_lowering` false → the messages go to the provider unchanged, with no
  thread hop (absorb, judges and drafts pay nothing for this feature);
- otherwise it returns an async generator that runs
  `images(conn)` and then `as_images(..., keep, load)` or `as_text` in
  `asyncio.to_thread` (the resolver reads a sidecar; loading decodes), writes
  the included count to the holder as `images`, opens the provider stream, and
  delegates to it inside `try/finally: await inner.aclose()`, so a caller's
  close or a timeout still reaches httpx exactly as `_guard` and `_resilient`
  intend.

Loading happens inside the attempt, so its time counts against the first-delta
idle bound. With the encode cache (below) a warm image is a dictionary hit; a
cold one is one bounded decode. A pathological stall would be recorded as that
connection's timeout — accepted, and stated in the docstring.

### A rejected image degrades to text on the same route

A provider can refuse an image for its format, size or content policy, and in a
role-play library the last is not hypothetical. Without a degrade, that one
picture fails every turn until it ages out of the window.

`LLMError` gains an optional `status: int | None` — the HTTP status of the
response that produced it, `None` for anything that was not one — set by both
adapters where they already map a status to a kind. `bad_response` alone cannot
carry this decision: it is also what a 500, 502 or 503 maps to, and re-sending
as text on a server error would drop the pictures from a turn that a plain
retry would have served. `REJECTED_STATUSES = {400, 404, 413, 415, 422}` is
the provider saying "not this request" -- 404 because OpenRouter answers "No
endpoints found that support image input" with one when no endpoint it may
route to takes images; a 404 for a genuinely missing model costs one extra text
attempt that fails the same way.

When the messages carry refs (`has_refs`, a pure check — no resolver runs while
routes are built, so nothing reads a sidecar on the event loop),
`_usable_routes` follows **every** route with a **degrade sibling**: the same
connection, marked to be sent the text lowering, with zero retries. `_resilient`
attempts a degrade sibling only when the attempt just before it, on the same
connection, actually sent images (the holder's `images > 0`) and failed with a
status in `REJECTED_STATUSES`; otherwise it moves past it without an attempt.
So a fallback that reads images and refuses one is degraded too.

Moving onto or past a degrade sibling is not falling back: it does not set
`fell_back`, and the error that connection reports is the **last** one it
raised (the degrade's, when it ran — so its `retry_after` survives), which is
then combined with the other connection's exactly as today. A lone primary
with a degrade sibling still raises its own error, never "the fallback failed
too". The degrade is logged at warning (`images refused by <connection>;
retried as text`) so the cause is findable.

A known limit, pre-existing and not widened here: OpenRouter reports an error
that arises after streaming has begun as an SSE `{"error": …}` frame on an HTTP
200, which `openrouter.stream` does not read, so such a refusal ends a turn
empty rather than degrading. Image refusals are overwhelmingly pre-stream
validation, which arrives as an HTTP status and is covered.

`_carries_parts` becomes "carries a part that cannot be lowered": a message of
`text` / `image_ref` parts is not a reason to drop a `claude` fallback, because
that fallback receives the text. The image-description draft's `image_url`
parts still are.

### Strict folding

`openai_compatible._strict_messages` learns to fold list content (fixing, in
passing, the image-description draft over a strict connection, which raises
today). Joining two contents where either is a list produces one parts list —
adjacent `text` parts merged — with the same `"\n\n"` separator the string path
uses; two strings join exactly as today. System messages are always strings.

### Loading an image

`store.post_images.load(cid, part)`:

1. `export.resolve_url(cid, part["url"])`; `None` → `None`.
2. Read through one handle with the one-byte-past-the-cap read
   `image_drafts.data_uri` uses, capped at `image_drafts.MAX_BYTES`.
3. Decode with Pillow (`draft` for JPEG), take the first frame, apply EXIF
   orientation, fit within `SEND_EDGE = 1024` px (never upscaled), and encode
   as **JPEG** (quality 85), or **PNG** when the image has alpha. Never WebP:
   `thumbs.thumbnail` writes WebP where it can, and not every OpenAI-compatible
   server decodes it — so sending has its own encode rather than reusing that
   cache. Typed from what was encoded, not from a file extension.
4. Refuse an encoding over `MAX_SEND_BYTES = 3_750_000` (base64 of it is just
   under the 5 MB per-image limit Anthropic applies).
5. Cache the encoded bytes in a small in-process LRU keyed by
   `(path, mtime_ns, size)` — a picture stays in the window for many turns,
   and re-decoding it every turn is pointless. Bounded by **total bytes**
   (`CACHE_BYTES = 16 MB`, evicting least recently used), not entry count,
   because one PNG may be close to `MAX_SEND_BYTES` and the app also runs on a
   phone; guarded by a lock, because it is filled from concurrent
   `to_thread` workers.

`SEND_EDGE` because providers downscale to roughly this size before tokenising
anyway, so more pixels buy nothing but upload, and it is what keeps N images
affordable in memory on Android. Never raises: any failure (including Pillow's
decompression-bomb refusal) is logged at warning and costs that image, not the
turn. A `data:` URI rather than a URL, for `image_drafts`' reason: the provider
cannot reach this machine.

### No image bytes in logs or errors

A provider's 4xx body can echo the request back (a validation error quoting
the offending field), and the error detail flows to the log, the error store
and the reader's toast. Both adapters' `_extract_error` and the
`http_error_body` capture run `content_parts.scrub` over the text, so a `data:`
payload never leaves the request.

## Costs

No new money column. Every money figure stays provider-reported, or modelled
from provider-reported tokens; the OpenAI-shaped providers count image tokens
inside `prompt_tokens`, and OpenRouter's billed `cost` includes its image
charges. The `claude` path never sends an image.

The one place the money can under-report is the *modelled* figure, for an
endpoint that bills images per image rather than per token — `store/pricing.py`
has no per-image price. So:

- `_dispatch` writes the count actually sent to the attempt's holder as
  `images` (0 for a text lowering).
- `usage.Meter.done` copies it, and `usage.record(images=…)` writes it to the
  ledger row when non-zero (absent otherwise, like every optional count).
- The per-post cost rollup sums it, and the transcript's per-post cost detail
  (`components/cost.tsx`) says how many images the turn sent.

A reader checking why a turn was expensive sees that it carried pictures, rather
than a modelled figure that silently leaves them out.

## Prompt caching

An image is part of the prompt prefix a provider caches. While the set of the
newest N images is stable, nothing changes between turns. When a new image
arrives, the oldest sent image reverts to alt text (and, if it was carried, its
carrier user message changes), which invalidates the cache from that message
onward. How far back that is depends on how sparse the images are: with images
in every few posts it is near the tail; with N images spread across a long
scene, the oldest can sit near the start of the window and a new image
invalidates nearly the whole cached history once. Turning the setting on,
changing N, or a budget forcing an image out has the same one-off effect. This
is documented in `store/post_images.py`; nothing tries to pin images to keep a
prefix stable.

## Testing

- `content_parts`: `text_of`, `as_text` (drops carriers, never mutates),
  `as_images` (keep bound counted from the end, load `None`/raise, collapse,
  carried label, count), `lowerable`, `scrub`.
- `_project_history` + `_assemble` with `images=0` byte-identical to today over
  a transcript with images; with `images=N`: the newest N resolvable images,
  remote/unresolvable/unsniffable ones alt text, director notes skipped, an
  assistant image carried to the next user message and to a trailing carrier,
  macros expanded, `as_text(result)` equal to the `images=0` output, a forged
  `⟦img:…⟧` in a post left as text.
- `pack`: images removed oldest-first before any section drop or history trim;
  with every image removed the result equals the text-only pack.
- `_breakdown`: Images row, total equal to the message-cost sum (no double
  count).
- `catalog.entry` maps `input_modalities`.
- `post_images.capability` / `limit` / `images_for`; `load` encodes JPEG/PNG
  within `SEND_EDGE`, refuses oversize, caches, never raises.
- `LLMClient`: a vision route receives `image_url` parts with `data:` URIs only
  in user messages, the holder's `images` count; a text route the identical
  `images=0` string; a claude fallback kept and sent text; a draft's
  `image_url` parts still exclude it; a 400/404/413/415/422 after images were sent
  retried once on the same connection as text (on the fallback too), a 5xx or
  a 429 not, a rejection on an attempt that sent no image not, no "fallback
  failed too" for a lone primary, the degrade's own error reported when it
  fails; a text-only list (every ref packed away) still lowered to a string
  before a `claude` route; a carrier merged into a director note's user
  message rather than emitted beside it; the setting turned off
  between compose and dispatch sends text; the wrapper closes the provider
  stream on caller close; no thread hop without refs.
- `_strict_messages` folds list content and folds strings exactly as before.
- Adapters scrub `data:` payloads from error details and captures.
- Snapshots and resume prompts hold `image_ref` parts and no `data:` URI; a
  snapshot restored in a fork resolves against the fork.
- Ledger: `images` recorded and summed per post.
- Config round trip for both keys, on/off validation, `send_images_reach`;
  connection `vision` round trip and validation.
- Frontend: ConfigView toggle, limit, reach hint; ConnectionForm select shown
  for openrouter/openai_compatible, hidden for claude, catalog hint; per-post
  cost detail shows the image count.
- LLM calls use `tests/llm_fakes.py`; a test turning images on uses scripted
  fakes (the cassette matchers read string content).
