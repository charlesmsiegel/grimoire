# Send post images to the model (#377)

## Intent and scope

A post can carry a picture (#376): a reader inserts one with `PostImagePicker`,
or the narrator writes an art handle that `context.art.resolve_handles` rewrites
to `![description](/api/...)`. Today every one of them reaches the model as its
alt text — `context.story._project_history` runs `export.drop_images` over each
history line. That stays the behaviour by default.

This change adds a setting. When it is on **and** the connection a turn runs on
can read images, the newest images in the history that is actually sent go to
the model as image content parts, beside the alt text they already carry. A
text-only route keeps receiving exactly the text it receives today.

In scope: the scene-turn family of prompts — everything composed through
`context.assemble._assemble` (ordinary turns, director turns, retries and
regenerations, character turns, mechanics follow-ups). Out of scope: absorb,
audit, dossiers, the tracker, summaries, image-description drafts (which already
send a picture of their own), and the opener (it carries no history). Nothing
about how posts store images changes.

Success:

- Off (the default), every composed prompt is byte-identical to today's.
- On, with a vision-capable primary, the last `send_images_limit` images in the
  sent history window go out as `image_url` parts; older ones stay alt text.
- A fallback that cannot read images receives the text-only rendering of the
  same prompt rather than failing or being dropped.
- The packer, the inspector and the budget bar count what the images cost.
- Nothing that persists a prompt (prompt snapshots, character-turn resume
  prompts) ever holds image bytes.

## Settings

Two keys in `store/config.py`'s `_CONFIG_KEYS`, defaults in `read_config`,
reported by `routes.config._public_config`, writable through `ConfigUpdate`:

- `send_images` — `"on"` / `"off"`, default `"off"`. Validated by joining
  `routes.config._ON_OFF_KEYS`. Off by default because an image is real money
  on every turn it stays in the window, and turning that on behind someone's
  back is the same kind of imposition `backup_enabled` refuses to make.
- `send_images_limit` — a non-negative integer, default `"3"`. The newest N
  images in the history window are sent; `0` sends none (equivalent to off).
  Read fail-soft like `context_budget`: a malformed value falls back to the
  default rather than raising. Three because one image is rarely the whole
  scene (a map and the room it shows) and every extra one is paid on every turn
  until it ages out; the number is configuration because the right trade
  depends on the reader's model price. It should be tuned against real play.

`ConfigView` shows both beside the other prompt-composition knobs: a checkbox
("Send post images to models that can read them") and a number input that is
disabled while the checkbox is off.

## Which connections can read images

### The catalog says, where it can

`catalog.entry` gains `vision: bool | None`, read from
`architecture.input_modalities` when the provider publishes it (OpenRouter
does; an OpenAI-compatible endpoint occasionally does through the same field):
`True` when the list contains `"image"`, `False` when the list exists without
it, `None` when there is no such list. `None` is "the provider did not say", in
the same spirit as an unpriced model being `None` rather than `0`.

The frontend `Model` type gains `vision?: boolean | null`. Existing cached
sidecars written before this change simply lack the key and read as unknown.

### The connection can override

`store.llm_connections._FIELDS` gains `vision`, stored as `""` (auto), `"on"`
or `"off"`. Anything else read from disk is treated as auto. `ConnectionCreate`
/ `ConnectionUpdate` accept it and the route refuses any other value with 400.
`ConnectionForm` shows a "Reads images" select (Auto / Yes / No) for
`openrouter` and `openai_compatible` connections, with a hint stating what the
catalog says about the selected model (reads images / text only / not stated).
A `claude` connection does not show it.

### One resolver

`store/post_images.py` (new) owns the decision:

```python
def accepts_images(conn: dict | None) -> bool
```

- `None`, or a kind in `llm.TEXT_ONLY_KINDS` (`claude`) → `False`, whatever
  the override says: that client flattens content to a string and cannot carry
  a part.
- `vision == "on"` → `True`; `vision == "off"` → `False`.
- Auto → the cached catalog entry (`llm_connections.cached_models(conn["id"])`)
  whose id equals `llm.effective_model(conn)` has `vision is True`. No cached
  catalog, no matching row, or `None` → `False`. Sending image parts to a
  text-only endpoint is a 400, not a graceful degradation, so the unknown case
  is off.

Never raises: a catalog that cannot be read is "unknown", which is off.

```python
def images_for(conn: dict | None) -> int
```

returns `send_images_limit` when `send_images` is on and `accepts_images(conn)`,
else `0`. This is the one number composition takes.

## Message content: a third shape

Message content is a string today, except the image-description draft, which
sends a list of OpenAI-style parts. This change adds a grimoire-internal part:

```python
{"type": "image_ref", "url": "/api/campaigns/<cid>/images/<name>", "campaign": "<cid>"}
```

A reference, not bytes, so that everything that copies, snapshots or persists a
composed prompt (`PreparedMessages.snapshot`, `responses.save_resume_prompt`,
`prompt_log`) keeps holding a few dozen bytes per image. Bytes are produced only
at dispatch, per attempt, and never leave the request.

The content rule for a history message with images is: **the text parts,
concatenated, are exactly the string today's code produces** — alt text in place
of each image — and an `image_ref` part sits immediately after the text part
that ends with that image's alt text. So lowering to text is "concatenate the
text parts and drop the rest", and the alt text stays in front of the model
alongside the picture it describes (it names the subject, which the pixels
cannot).

A new leaf module, `grimoire/content_parts.py` (imports nothing from the
package, like `catalog` and `llm_errors`, so both `llm.py` and `store` may use
it), owns the shape:

- `IMAGE_REF = "image_ref"`
- `text_of(content) -> str` — the string itself, or the concatenated text parts.
- `image_refs(content) -> list[dict]` — the `image_ref` parts, in order.
- `lowerable(content) -> bool` — a string, or a list whose parts are all
  `text` / `image_ref`.
- `lower(content, load) -> str | list` — for a route that reads images:
  `image_ref` parts become `{"type": "image_url", "image_url": {"url": <data
  URI>}}` via `load(part)`; a part whose `load` returns `None` or raises is
  dropped (its alt text is already in the text). A result with no image parts
  left collapses to `text_of`. Any other part passes through untouched.
- `as_text(content) -> str | list` — for a route that does not: `text_of` when
  `lowerable`, otherwise unchanged.

Every consumer of a composed message's `content` either receives a string by
construction or goes through `text_of`. The plan enumerates them; the known
ones are `pack.message_cost`, `assemble._breakdown`,
`routes.character_turns._capture`, `openai_compatible._strict_messages` and
`claude_agent` (which only ever sees lowered text).

## Composition

`_assemble(..., images: int = 0)` and `story._project_history(messages,
images=0, cid="")`. With `images == 0` the projection is today's code path,
untouched. Otherwise:

1. Walk the scene's messages from newest to oldest, skipping director notes as
   today, and choose up to `images` markdown images whose URL resolves to a
   file in this campaign (below). Occurrences are counted, not distinct files.
2. In each chosen message, replace each chosen image's markdown with its alt
   text followed by a sentinel (`<n>`, private-use code points
   stripped from the raw content first so a post cannot forge one); every
   unchosen image is reduced to alt text exactly as `drop_images` does now.
3. Render, merge same-role neighbours and run `_expanded` over the strings as
   today — the sentinels pass through Jinja and macro expansion untouched.
4. Split each resulting string on sentinels into `text` / `image_ref` parts.
   A string with no sentinel stays a string.

Choosing newest-first over the *whole* scene and then letting the packer trim
from the front gives exactly "the newest N in the sent window": the window is a
suffix of the history, so an image that survives trimming has no newer
unchosen image behind it.

`compose_turn`, `compose_director_turn`, `build_messages`,
`build_director_messages` and `context_breakdown` take `images: int = 0` and
pass it to `_assemble`. Every scene-turn route that passes
`model=effective_model(conn)` also passes
`images=store.post_images.images_for(conn)`, including the inspector routes, so
the live Context view describes what the next turn would really send.

### Which URLs count

Only the app's own localized image URLs — the shapes `export._IMG_URL` and
`export._COLLECTION_URL` match — and only when they resolve to a file. The
resolution is the export's own, resolved against the campaign being played
rather than the id written in the URL (the fork rule `export._resolve_image`
documents). It is factored out of `export.rewrite_images` into
`export.resolve_url(cid, url) -> Path | None` so the two cannot drift. A remote
`https://` image in a post is never sent: handing a third-party URL to a
provider is a fetch the reader did not ask for, and a broken or private one is
a provider error. It stays alt text.

## Dispatch

`LLMClient` gains two injected callables, on the established pattern
(`timeout`, `retries`, `fallback`, `observer`, `capture`), which keeps the
gateway store-free:

- `vision: Callable[[dict], bool]` — `store.post_images.accepts_images`.
  Default: nothing reads images.
- `load_image: Callable[[dict], str | None]` — `store.post_images.load`, an
  `image_ref` part to a `data:` URI, or `None`.

`_dispatch(messages, conn, usage)` selects the model variant as today, then
lowers every message: `content_parts.lower` when `vision(conn)`, else
`content_parts.as_text`. Lowering loads files and may cold-decode a thumbnail,
so it runs in `asyncio.to_thread` inside an async generator that then delegates
to the provider stream — the event loop is never held by a decode, and the
time counts against the first-delta idle bound like any other slow start. The
number of image parts actually sent is written to the attempt's usage holder as
`images` (see Costs).

`_carries_parts` becomes "carries a part that cannot be lowered": a message of
`text` / `image_ref` parts is not a reason to drop a `claude` fallback, because
that fallback receives the text. The image-description draft's `image_url`
parts still are.

`openai_compatible._strict_messages` learns to fold list content. Joining two
contents where either is a list produces one parts list (`text` parts merged
where adjacent) with the same `"\n\n"` separator the string path uses; two
strings join exactly as today. System messages are always strings.

### Loading an image

`store.post_images.load(part)`:

1. `export.resolve_url(part["campaign"], part["url"])`; `None` → `None`.
2. `thumbs.thumbnail(path, SEND_EDGE)` with `SEND_EDGE = 1024`: providers
   downscale to roughly this size before tokenising anyway, so sending more
   pays for bytes and upload time and buys nothing, and the bound is what keeps
   N images affordable in memory on Android. The thumbnail cache is reused
   across turns, so an image in the window is decoded once, not once per turn.
3. `thumbs.thumbnail` answers `None` for an animated or undecodable file; then
   the original is used if it is at most `MAX_BYTES` (5 MB) and its extension is
   in `image_drafts.MEDIA`, read through one handle with a one-byte-past-cap
   read as `image_drafts.data_uri` does. Otherwise `None`.
4. Encode as a `data:` URI typed from the file actually read.

Never raises; a failure is logged at warning and costs that image, not the
turn. A `data:` URI, not a URL, for `image_drafts`' reason: the provider cannot
reach this machine.

## Budget, inspector and bar

`pack.message_cost(content, count)` charges `count(text_of(content)) +
IMAGE_TOKENS * len(image_refs(content)) + MESSAGE_OVERHEAD`, so the packer
trims history with the images' weight on it. `IMAGE_TOKENS = 1600`, a flat
per-image estimate for an image bounded at `SEND_EDGE`: it errs high against
the common providers' published per-image costs at that size, the same
direction `MESSAGE_OVERHEAD` errs, because this is a ceiling. It is a constant
documented as an estimate, to be tuned against real provider-reported prompt
tokens later.

`_breakdown` reports the history row's text as `text_of` each message and its
tokens without images, and adds an **Images** row (`id: "history_images"`,
`label: "Images (n)"`, tier `history`, text listing each sent image's alt text
and URL, tokens `n * IMAGE_TOKENS`) when the kept history carries any.
`total_tokens` includes it. The budget bar needs no change: a `history` row
already lands in the Conversation bucket, so the images are drawn rather than
silently left out.

A fallback that cannot read images is sent the lowered text but recorded with
the composed breakdown, which still counts the images: the record over-counts
that attempt by the image estimate. Accepted and stated in
`model_guidance`/`prompt_log` docstrings — over-reporting a ceiling is the safe
direction, and re-packing per route would make the fallback's prompt differ
from the primary's in more than its guidance.

## Costs

No new money column. Every money figure stays provider-reported or modelled
from provider-reported tokens, and the OpenAI-shaped providers count image
tokens inside `prompt_tokens`; OpenRouter's billed `cost` includes its image
charges. The `claude` path never sends an image.

The one place this can under-report is the modelled figure, for an endpoint
that bills images per image rather than per token — `store/pricing.py`'s table
has no per-image price. So the attempt's usage holder carries `images` (the
count actually sent after lowering), `usage.record` writes it to the ledger row
when non-zero (absent otherwise, like every optional count), and the
transcript's per-post cost detail says how many images that turn sent. A user
checking why a turn was expensive can see it, rather than reading a modelled
figure that silently omits them.

## Prompt caching

An image is part of the prompt prefix a provider caches. While the set of the
newest N images is stable, nothing changes between turns. When a new image
arrives, the oldest sent image falls back to alt text, which changes the message
it sits in and invalidates the cache from that message onward. Because only the
newest N are ever sent, that message is always near the tail, so the
invalidated suffix is short; turning the setting on or changing N invalidates
everything once. This is documented in `store/post_images.py`; nothing tries to
pin images to keep a prefix stable.

## Testing

- `content_parts`: `text_of`, `lower` (load success, `None`, raise, collapse to
  string), `as_text`, unknown parts passing through.
- `_project_history` with `images=0` is byte-identical to today's output over a
  transcript with images; with `images=N` picks the newest N resolvable
  images, leaves remote and unresolvable ones as alt text, skips director
  notes, and its `text_of` equals the `images=0` output.
- `catalog.entry` maps `input_modalities` to `True` / `False` / `None`.
- `accepts_images`: claude never; override on/off; auto with and without a
  cached catalog row.
- `images_for` respects the toggle and limit, and a malformed limit.
- `LLMClient`: an image-capable primary receives `image_url` parts with a
  `data:` URI and the holder's `images` count; a text-only route receives the
  identical string the `images=0` composition produces; a claude fallback is
  kept and receives text; a draft's `image_url` parts still exclude it.
- `_strict_messages` folds list content and still folds strings exactly.
- `pack` / `_breakdown`: image tokens count toward the budget and appear as the
  Images row; the total includes them.
- Persisted snapshots contain `image_ref` parts and no `data:` URI.
- Config round trip for both keys and the on/off validation; connection
  `vision` round trip and validation.
- Frontend: ConfigView toggle and limit; ConnectionForm select shown for
  openrouter/openai_compatible and hidden for claude, hint from the catalog.
- `test_llm_fakes.py`-style fakes are used for every LLM call.
