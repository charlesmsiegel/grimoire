# Send post images to the model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `send_images` is on and the route reads images, send the newest N transcript images to the model as user-message image parts; everything else stays today's text.

**Architecture:** Composition emits grimoire-internal `image_ref` parts (references, never bytes) in user messages — assistant images carried forward — and the packer gives images up first. The LLM facade lowers per attempt: `image_url` data URIs for a route that reads images, today's exact strings for one that does not, with a text-degrade sibling for a provider that rejects an image.

**Tech Stack:** Python 3.11 / FastAPI / Pillow / pytest; React + TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-post-images-to-model-design.md`

## Global Constraints

- Off (`send_images: "off"`, the default) → every composed prompt byte-identical to today's.
- `llm.py`, `catalog.py`, `llm_errors.py`, `content_parts.py` import nothing from `store`; `store` never imports `llm`.
- Imports at module scope, graph acyclic (`test_import_guard.py`); inside `store/`, cross-package imports bind submodules.
- Every store write through `store.atomic`; filesystem access through the resolvers (`test_paths_guard.py`); markers need a reason.
- pydantic: plain `BaseModel` fields, `Literal` allowed, no `Field`/validators.
- Persisted prompts never hold `data:` URIs; logs/errors scrub them.
- `send_images_limit` default `"3"`; `SEND_EDGE = 1024`; `IMAGE_TOKENS = 1600`; `MAX_SEND_BYTES = 3_750_000`; `CACHE_BYTES = 16 * 1024 * 1024`; `REJECTED_STATUSES = frozenset({400, 404, 413, 415, 422})`.
- Carried label text, verbatim: `[Image from the previous reply: {alt}]`.
- Lint gates are ratcheted: after fixing or adding findings run `make baseline` only if counts drop; never add findings.
- Backend tests: `PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend/tests/<file> -q`. Frontend: `cd frontend && npx vitest run <file>`.
- Fake LLMs come from `backend/tests/llm_fakes.py` (scripted fakes; cassettes read string content). Extend that module rather than writing an inline fake.
- New functions stay under ruff's complexity limit (C901): factor helpers rather than adding findings to files that have none.
- `store/__init__.py` exports are pinned by `test_store_api_baseline.py`: adding `post_images` means adding it to `__all__` and regenerating `backend/tests/store_api_baseline.json` deliberately in the same commit.
- Injected callables are late-bound (`lambda c: store.post_images.images_for(c)`), so a test patching the module attribute intercepts.

## Review Focus

1. A continuation or character turn whose last history message is narrator art (no user post after it) → a trailing carrier user message sits before post-history, and on a strict `openai_compatible` vision route the folded request still alternates user/assistant. Composition tested in Task 6, strict folding in Task 7.
2. An image deleted/replaced between compose and dispatch → that image is skipped, alt text stays, the holder's `images` counts only what went. Test in Task 7.
3. A reroll from a snapshot frozen with images, after the setting was turned off → text only. Test in Task 7.
4. A post with `![](url)` (empty alt) as its whole content, on a text route → still sent as `""`, not dropped. Test in Task 1.
5. A budgeted prompt where images push it over → images removed oldest-first, no section dropped, and packing one profile leaves the input history and every other variant unchanged. Test in Task 5 (pack) and Task 6 (two profiles).

---

### Task 1: `content_parts` leaf module

**Files:**
- Create: `backend/src/grimoire/content_parts.py`
- Test: `backend/tests/test_content_parts.py`

**Interfaces:**
- Produces: `IMAGE_REF = "image_ref"`, `CARRIER = "carrier"` (message key), `CARRIED_LABEL = "[Image from the previous reply: {alt}]"`;
  `text_of(content: str | list) -> str`; `image_refs(content) -> list[dict]`; `has_refs(messages: list[dict]) -> bool`;
  `lowerable(content) -> bool` (a list of only text/image_ref parts; a str is NOT lowerable);
  `needs_lowering(messages) -> bool` (any carrier or any lowerable list);
  `as_text(messages) -> list[dict]`; `as_images(messages, keep: int, load: Callable[[dict], str | None]) -> tuple[list[dict], int]`;
  `ref(url: str, alt: str, carried: bool) -> dict`; `collapse(content) -> str | list` (list with no image_ref → `text_of`, else unchanged);
  `scrub(text: str) -> str`.

- [ ] **Step 1: Write failing tests**

```python
from grimoire import content_parts as cp

R = cp.ref("/api/campaigns/c/images/a", "a map", False)
C = cp.ref("/api/campaigns/c/images/b", "the hall", True)

def test_text_of_concatenates_text_parts_only():
    assert cp.text_of("plain") == "plain"
    assert cp.text_of([{"type": "text", "text": "x a map"}, R, {"type": "text", "text": " y"}]) == "x a map y"

def test_as_text_drops_carriers_keeps_empty_messages_and_never_mutates():
    msgs = [{"role": "user", "content": [{"type": "text", "text": ""}, R]},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": [C], "carrier": True}]
    before = repr(msgs)
    assert cp.as_text(msgs) == [{"role": "user", "content": ""}, {"role": "assistant", "content": "hi"}]
    assert repr(msgs) == before

def test_as_images_keeps_newest_n_counts_and_labels_carried():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "old a map"}, R]},
            {"role": "user", "content": [C, {"type": "text", "text": "new"}]}]
    out, n = cp.as_images(msgs, 1, lambda p: "data:image/png;base64,AA")
    assert n == 1
    assert out[0]["content"] == "old a map"
    assert out[1]["content"] == [
        {"type": "text", "text": "[Image from the previous reply: the hall]"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
        {"type": "text", "text": "new"}]

def test_as_images_load_failure_drops_image_and_empty_carrier():
    msgs = [{"role": "user", "content": [C], "carrier": True}]
    def boom(p): raise OSError("gone")
    assert cp.as_images(msgs, 3, boom) == ([], 0)
    assert cp.as_images(msgs, 3, lambda p: None) == ([], 0)

def test_unknown_parts_pass_through_and_are_not_lowerable():
    draft = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}]}]
    assert not cp.needs_lowering(draft)
    assert cp.as_text(draft) == draft

def test_needs_lowering_true_for_text_only_list():
    assert cp.needs_lowering([{"role": "user", "content": [{"type": "text", "text": "a"}]}])

def test_scrub_elides_base64_payloads():
    assert cp.scrub('bad "data:image/png;base64,iVBORw0KGgo=" x') == 'bad "data:image/png;base64,[elided]" x'
```

- [ ] **Step 2: Run** `pytest backend/tests/test_content_parts.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `content_parts.py` per the Interfaces. `as_images` walks refs from the last message backwards to pick the newest `keep`; a carried ref emits the label text part immediately before its `image_url`; a lowered message is a new dict without the `carrier` key; adjacent text parts may stay separate. `scrub` regex: `data:([\w/+.-]+);base64,[A-Za-z0-9+/=]+` → `data:\1;base64,[elided]`. Module docstring states the content rule (text parts concatenate to today's string) and why refs are references.
- [ ] **Step 4: Run** the test file → PASS; `pytest backend/tests/test_import_guard.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(llm): content_parts leaf module for image references (#377)`.

### Task 2: Provider adapters — status on errors, scrub, strict folding of lists

**Files:**
- Modify: `backend/src/grimoire/llm_errors.py` (`LLMError.__init__`)
- Modify: `backend/src/grimoire/openrouter.py`, `backend/src/grimoire/openai_compatible.py` (`_extract_error`, every `raise …(_status_kind(resp.status_code), …)`, the `http_error_body` emit, `_strict_messages`)
- Test: `backend/tests/test_llm_image_adapters.py`

**Interfaces:**
- Consumes: `content_parts.scrub`, `content_parts.text_of`.
- Produces: `LLMError(kind, detail="", retry_after=None, status: int | None = None)` with `.status`.

- [ ] **Step 1: Failing tests:** `LLMError("bad_response", "x", status=422).status == 422` and default `None`; an `httpx.MockTransport` returning 422 with body `{"detail":[{"input":"data:image/png;base64,QUJD"}]}` raises an error whose `.status == 422` and whose `detail` contains `[elided]` and not `QUJD` (both adapters), and the `http_error_body` capture event is scrubbed too; `_strict_messages([{"role":"system","content":"S"},{"role":"user","content":[{"type":"text","text":"u"},{"type":"image_url","image_url":{"url":"d"}}]}])` returns one user message whose content is `[{"type":"text","text":"S\n\nu"},{"type":"image_url","image_url":{"url":"d"}}]`; two consecutive user lists merge with a `"\n\n"` text join; string-only inputs give exactly the pre-change result (reuse an existing case from `test_openai_compatible.py` as a regression).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `status` stored as given. Both adapters pass `status=resp.status_code` as a keyword wherever they raise from a status (`openrouter.py` and `openai_compatible.py`, both the stream and the catalog/probe raises); `_extract_error` returns `content_parts.scrub(...)`; the capture emits `content_parts.scrub(resp.text)`. `_strict_messages`: a `_join(a, b)` that returns `a + "\n\n" + b` for two strings, otherwise a parts list (strings become text parts; adjacent text parts merged with the separator).
- [ ] **Step 4: Run** the new file plus `test_openai_compatible.py test_openrouter.py test_llm.py test_image_description_draft.py` → PASS.
- [ ] **Step 5: Commit** `feat(llm): carry HTTP status on LLMError, scrub data URIs, fold part lists (#377)`.

### Task 3: Capability, settings, and the resolver

**Files:**
- Modify: `backend/src/grimoire/catalog.py` (`entry`)
- Modify: `backend/src/grimoire/store/llm_connections.py` (`_FIELDS`)
- Modify: `backend/src/grimoire/routes/models.py` (`ConnectionCreate`, `ConnectionUpdate`, `ConfigUpdate`)
- Modify: `backend/src/grimoire/store/config.py` (`_CONFIG_KEYS`, defaults, `DEFAULT_SEND_IMAGES = "off"`, `DEFAULT_SEND_IMAGES_LIMIT = "3"`)
- Modify: `backend/src/grimoire/routes/config.py` (`_ON_OFF_KEYS`, `_public_config`)
- Create: `backend/src/grimoire/store/post_images.py` (capability half); add it to `store/__init__.py`'s imports and `__all__`, and regenerate `backend/tests/store_api_baseline.json`
- Modify (expected dicts gain `"vision": None`): `backend/tests/test_model_catalog.py`, `test_openrouter.py`, `test_openai_compatible.py`, `test_routes.py`
- Modify: `backend/src/grimoire/store/locks.py` only if `test_lock_domain_guard.py` demands a classification (it mutates nothing; expect none)
- Test: `backend/tests/test_post_images.py`, additions to `backend/tests/test_routes.py`-style config tests in the same new file

**Interfaces:**
- Produces: `catalog.entry(raw)["vision"] -> bool | None`; connection dicts carry `"vision"`;
  `post_images.capability(conn: dict | None) -> str` (`"yes"|"no"|"unknown"`); `post_images.limit() -> int`; `post_images.images_for(conn) -> int`; `post_images.reach(conn) -> str` (`"off"|"yes"|"no"|"unknown"`); `GET /config` field `send_images_reach`, plus `send_images`, `send_images_limit`.

- [ ] **Step 1: Failing tests:** `entry({"id":"m","architecture":{"input_modalities":["text","image"]}})["vision"] is True`; `["text"]` → `False`; missing → `None`; capability: claude connection with `vision:"on"` → `"no"`; openrouter `vision:"on"` → `"yes"`, `"off"` → `"no"`; auto with `set_cached_models(id, [{"id":"m","vision":True}], rev)` and `model:"m"` → `"yes"`, row `vision: None` → `"unknown"`, no sidecar → `"unknown"`; `limit()` → 0 when off, 3 when on by default, 3 for `send_images_limit: "abc"` or `"-2"`, 0 for `"0"`; `images_for` = limit only when yes; `PUT /config {"send_images":"maybe"}` → 400; round trip of both keys through `GET /config`; `send_images_reach` is `"off"` by default and `"unknown"` after turning on with an uncatalogued active openrouter connection; `POST /llm-connections` with `vision:"sometimes"` → 422 and with `"on"` round-trips through `GET /llm-connections/{id}`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `catalog.entry` reads `raw.get("architecture")` defensively (`isinstance` dict, `input_modalities` a list). `post_images` imports `config`, `llm_connections`, `image_drafts` (for `SUPPORTED_KINDS`); `capability` reads `llm_connections.cached_models(conn["id"])["models"]`; `reach(conn)` = `"off"` if `limit()==0` else `capability(conn)`. `_public_config` computes reach for `_standing_connection("chat", "")[0]` (imported from `.common`, which `routes/config.py` already imports), wrapped so any failure answers `"unknown"`. Module docstring carries the spec's "Prompt caching" paragraph and the primary-only limit.
- [ ] **Step 4: Run** new file + `test_routes.py test_llm_connections_store.py test_model_catalog.py test_openrouter.py test_openai_compatible.py test_store_api_baseline.py` → PASS (the two `_public_config`/`ConfigUpdate` agreement tests in `test_routes.py` included).
- [ ] **Step 5: Commit** `feat: send_images settings and per-connection vision capability (#377)`.

### Task 4: Resolving and encoding an image for sending

**Files:**
- Modify: `backend/src/grimoire/store/export.py` (extract `resolve_url(cid: str, url: str) -> Path | None` from `rewrite_images.sub`; `rewrite_images` calls it)
- Modify: `backend/src/grimoire/store/post_images.py` (loading half)
- Test: `backend/tests/test_post_images.py`

**Interfaces:**
- Produces: `export.resolve_url(cid, url) -> Path | None`; `post_images.eligible(cid: str, url: str) -> bool` (resolves, sniffs via `export.packed_ext` on the first 64 bytes, and `decodable(ext)`); `post_images.decodable(ext: str) -> bool` (webp only if `PIL.features.check("webp")`); `post_images.load(cid: str, part: dict) -> str | None`; constants `SEND_EDGE`, `MAX_SEND_BYTES`, `CACHE_BYTES`.

- [ ] **Step 1: Failing tests** (campaign fixture with a library image written through `campaign_images.put_image`): `export` tests still pass unchanged; `resolve_url` resolves the library URL and returns `None` for `https://x/y.png`; `eligible` false for a text file named `.png`; `load` on a 3000×2000 RGB PNG returns `data:image/jpeg;base64,…` decoding to a 1024×683 image; an RGBA PNG returns `data:image/png`; an EXIF-rotated JPEG comes back upright (swap of width/height); a second `load` of the same unchanged file does not reopen it (patch `PIL.Image.open` to count); after rewriting the file the cache misses; a file over `image_drafts.MAX_BYTES` (patch the cap small) → `None`; an encode over `MAX_SEND_BYTES` (patch small) → `None`; `load` never raises when `Image.open` raises.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** One-handle capped read as `image_drafts.data_uri`; `Image.open(BytesIO(data))`, `draft("RGB", (SEND_EDGE, SEND_EDGE))` for JPEG, `seek(0)`, `ImageOps.exif_transpose`, `thumbnail((SEND_EDGE, SEND_EDGE))`; alpha (`"A" in im.getbands()` or `im.info.get("transparency")`) → PNG, else convert RGB → JPEG quality 85. Cache: `OrderedDict` keyed `(str(path), st.st_mtime_ns, st.st_size)` → `(media, bytes)`, total-bytes bounded, `threading.Lock`. Failures logged at warning via the module logger.
- [ ] **Step 4: Run** → PASS, plus `test_export_store.py test_paths_guard.py test_atomic_guard.py`. Split `load` into helpers (read, decode/encode, cache) to stay under C901.
- [ ] **Step 5: Commit** `feat(store): resolve and encode post images for sending (#377)`.

### Task 5: Packing and the inspector

**Files:**
- Modify: `backend/src/grimoire/store/context/pack.py` (`IMAGE_TOKENS`, `message_cost`, `pack`)
- Modify: `backend/src/grimoire/store/context/assemble.py` (`_breakdown`, `_token_memo`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`_capture` uses `text_of`)
- Test: `backend/tests/test_post_images_pack.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `pack.IMAGE_TOKENS = 1600`; `pack.message_cost(content: str | list, count=None) -> int`; `pack.pack` result gains `"images_dropped_tokens": int` (also `0` on the unbounded early return), its `history` holding new dicts for edited messages; breakdown row `{"id": "history_images", "label": f"Images ({n})", "tier": "history", …}`.

- [ ] **Step 1: Failing tests:** `message_cost([text "abcd", ref], count=len) == 4 + 1600 + 5`; unbounded budget returns history untouched (lists included); with a budget that fits text but not images, two refs over two messages: the older ref goes first, its message becomes a plain string equal to `text_of`, no section is dropped, `history_trimmed == 0`, `dropped_tokens` includes the 1600s; a carrier whose ref is removed disappears; with every image removed the result equals `pack` over the `as_text` history (sections, history, trimmed, trimmed_tokens); the input history list and its dicts are unchanged afterwards (pack twice, same output — Review Focus 5); `_breakdown` with one kept ref has an Images row of 1600 tokens, the history row's tokens exclude it, and `total_tokens` equals system + `sum(message_cost)` + post-history; a carrier contributes no `"\n\n\n\n"` to the history text; `_capture` on a list-content message counts `text_of`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** In `pack`, after the first `hist_costs`/`total` computation and before the tier loop (only reached when `budget > 0`), a helper `_drop_images(hist, hist_costs, ...)` that, while `total > budget` and refs remain, removes the oldest ref (first message from the front holding one, first ref in it), replaces that message with a new dict whose content is `content_parts.collapse(...)` — or removes the message when it is a carrier left empty — then recomputes that message's cost, `hist_total` and `total`. `images_dropped_tokens` is the total cost removed this way, carrier `MESSAGE_OVERHEAD` included. `_breakdown` adds `p.get("images_dropped_tokens", 0)` to `dropped_tokens` and never to `trimmed`. `_token_memo` keys on the string it is given (callers pass `text_of`).
- [ ] **Step 3b: Re-derive the consumer list.** `grep -rn '\["content"\]\|get("content"' backend/src/grimoire/{store/context,routes,llm.py,claude_agent.py,openai_compatible.py,openrouter.py,model_guidance.py}` and confirm every hit that can see a composed message either receives a string by construction or goes through `content_parts.text_of` (both `_capture` lines: text and token count). Record any new hit in this task.
- [ ] **Step 4: Run** → PASS plus `test_context.py test_prompt_log.py test_character_turns.py`.
- [ ] **Step 5: Commit** `feat(context): images give way first when packing; Images row in the inspector (#377)`.

### Task 6: Composing image references

**Files:**
- Modify: `backend/src/grimoire/store/context/story.py` (`_project_history`)
- Modify: `backend/src/grimoire/store/context/assemble.py` (`_assemble`, `_prepare`, `compose_turn`, `compose_director_turn`, `build_messages`, `build_director_messages`, `context_breakdown`, `context_sections`)
- Modify: `backend/src/grimoire/model_guidance.py` (`PreparedMessages.campaign`, `from_snapshot(..., campaign="")`, `with_appended`)
- Test: `backend/tests/test_post_images_compose.py`

**Interfaces:**
- Consumes: Task 1 (`ref`, `CARRIER`), Task 4 (`post_images.eligible`), Task 5.
- Produces: `story._project_history(messages)` unchanged (list return, today's path); a sibling `story._project_history_refs(messages, *, images: int, cid: str) -> tuple[list[dict], dict[int, dict], str]` returning (projected strings carrying sentinels, ref table `{n: {"url", "alt", "role"}}`, nonce) — `_assemble` calls it only when `images > 0`; `compose_*`/`build_*`/`context_breakdown`/`context_sections` gain keyword `images: int = 0`; `PreparedMessages.campaign: str` (constructor kwarg `campaign=""`), `from_snapshot(snapshot, model, campaign="")`.

- [ ] **Step 1: Failing tests** (fixture A, in order: user post containing `![a map](<library url>)`, narrator post ending with `![the hall](<library url 2>)`, a remote `![x](https://e.com/x.png)`, a director note with an image, an unresolvable `/api/campaigns/<cid>/images/missing`; fixture B: narrator post with `![the hall](...)` then a user post `go on`):
  - `compose_turn(..., images=0)` equals the pre-change output (snapshot both through `as_text` and directly — identical lists of strings).
  - `compose_turn(..., images=3)`: `as_text(messages)` equals the `images=0` messages exactly; the user message holds a ref for "a map" right after the text part ending `a map`; "the hall" is a carried ref in a trailing carrier user message (`"carrier": True`) placed after history and before post-history (Review Focus 1); remote/missing/director images are not refs.
  - `images=1` keeps only "the hall"; two images in one message under `images=1` keep the later one in the text.
  - Fixture B: the carried ref is the first part of the `go on` user message, the narrator message is a plain string again, and no carrier exists.
  - Two guidance profiles (write two `scene/model_guidance/*.j2` templates in a tmp `GRIMOIRE_TEMPLATES` copy, or patch `model_guidance.freeze_profiles`): with a director note, each variant's note message carries the carried ref exactly once, and the breakdown's Images row still counts it.
  - A post containing a literal `⟦img:deadbeef:0⟧` stays text and yields no ref.
  - `compose_director_turn(..., images=3)` prepends the carried ref to the note's user message and emits no carrier.
  - `messages.campaign == cid`; `from_snapshot(messages.snapshot(), "m", campaign="fork").campaign == "fork"`; `with_appended(...)` keeps it; `deepcopy` keeps it; `json.dumps(messages.snapshot())` contains no `data:`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Sentinel `f"⟦img:{nonce}:{n}⟧"` with `nonce = secrets.token_hex(8)`; choosing walks `messages` newest→oldest, and within a message its `export._MD_IMG` matches last→first (skip director notes), keeping `post_images.eligible(cid, url)` up to `images`. `_assemble` gains `images: int = 0`; with `images > 0` it calls `_project_history_refs`, runs `_expanded` over the strings as today, then a helper `_split_refs(history, table, nonce)` splits on this nonce's sentinels and moves refs from assistant messages (`carried=True`) to the start of the next user message or into a trailing carrier (`{"role": "user", "content": [refs], "carrier": True}`); an assistant message left with only text collapses to its string. In `_prepare`, the carrier→`before_post` merge happens **inside `variant()`, after `pack.pack` and `_dedupe_runs`, on the outgoing `messages` list only**: when the packed history ends in a carrier and `before_post` starts with a user message, build a new list without the carrier and a NEW dict for that note (`content = [refs…, {"type": "text", "text": note}]`); never mutate `before_post` or `packed["history"]`; `_breakdown` reads the unmerged `packed`. `PreparedMessages` gets `campaign=cid`.
- [ ] **Step 4: Run** → PASS plus `test_context.py test_context_art.py test_character_turns.py test_model_guidance*.py test_prompt_log.py` and `python scripts/verify_templates.py`.
- [ ] **Step 5: Commit** `feat(context): compose newest post images as references in user turns (#377)`.

### Task 7: Dispatch — lowering, degrade siblings, wiring

**Files:**
- Modify: `backend/src/grimoire/llm.py` (`LLMClient.__init__`, `_carries_parts`, `_usable_routes`, `_dispatch`, `_resilient`)
- Modify: `backend/src/grimoire/routes/common.py` (LLMClient construction: `images=lambda c: store.post_images.images_for(c)`, `load_image=lambda cid, p: store.post_images.load(cid, p)`)
- Test: `backend/tests/test_post_images_dispatch.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 6.
- Produces: `LLMClient(..., images: Callable[[dict], int] | None = None, load_image: Callable[[str, dict], str | None] | None = None)`; usage holder key `"images"`; route dicts may carry `"_degrade": True` (never persisted, never sent).

- [ ] **Step 1: Failing tests** (providers from `llm_fakes.py` — extend it with a per-call scripted provider that records each call's messages and can raise a given `LLMError(..., status=)` on chosen calls; a `PreparedMessages` built by hand with `campaign` set):
  - vision primary (`images=lambda c: 3`, `load_image` returning a fixed data URI): provider receives `image_url` parts only in user messages; holder `images == n`; no `carrier` key reaches the provider.
  - text primary (`images=lambda c: 0`): provider receives exactly `as_text(messages)`; holder `images == 0`.
  - `images` resolver raising → treated as 0; `load_image` raising → that image dropped.
  - claude fallback is kept in `_usable_routes` for ref-bearing messages and receives strings; a draft's `image_url` list still drops it.
  - primary raises `LLMError("bad_response", status=422)` after sending images → second attempt on the same connection receives text; no "fallback failed too" in a raised error when the degrade also fails, and the raised error is the degrade's (`status`/`retry_after` from it).
  - refs present, P 422 with images, P's degrade fails with `rate_limit` (retry_after 7), fallback F fails → raised kind `rate_limit`, `retry_after == 7`, and the message mentions the fallback.
  - P fails with `rate_limit` (no degrade run), F fails → kind/retry_after are P's, as the existing `test_the_kind_is_the_primary_connections` pins.
  - Strict folding (Review Focus 1): a strict `openai_compatible` vision route sent [system, user, assistant, carrier, post-history system] receives alternating roles with the carrier's image and the post-history text in one user message.
  - everything above also passes with `client.stream(msgs, conn)` and no usage holder and capture off.
  - status 500 or kind `rate_limit` → degrade sibling skipped (provider called once per retry budget only).
  - 422 on an attempt that sent zero images → degrade skipped.
  - fallback that reads images and 422s → its own degrade runs.
  - no refs → `_dispatch` returns the provider stream itself (assert no `asyncio.to_thread` call via patch).
  - caller closes the facade stream mid-way → the provider generator's `aclose` ran.
  - Review Focus 2: `load_image` returning `None` for one of two refs → one `image_url`, holder `images == 1`.
  - Review Focus 3: snapshot restored with `images=lambda c: 0` → text only.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `_resilient` allocates a holder whenever `usage is None` (not only when capturing). `_carries_parts` → any list content that is not `lowerable`. `_usable_routes`: after filtering, when `content_parts.has_refs(messages)`, interleave `({**conn, "_degrade": True}, 0)` after each route. `_dispatch`: take `campaign = getattr(messages, "campaign", "")` before `for_model`; strip `_degrade` from the conn passed to providers (dispatch from a copy); if not `needs_lowering` → today's path; else return an async generator that in `asyncio.to_thread` resolves `keep = 0 if degrade else guarded images(conn)` and lowers (`as_images` with `lambda p: guarded load(campaign, p)` when `keep > 0`, else `as_text`), sets `usage["images"]`, opens the provider stream and delegates in `try/finally: await inner.aclose()`. `_resilient`: read `sent_images = usage.get("images", 0)` straight after each `except LLMError`. A degrade route is skipped with a `continue` placed before `tries += 1` and `_stamp` unless the previous route's last error has `status in REJECTED_STATUSES` and its `sent_images > 0`; when it runs, log `images refused by %r; retried as text`. `fell_back` becomes True only when an attempt is about to run on a non-degrade route with index > 0. Track `primary_err` = the last error raised on the primary or its degrade sibling, separately from `last`; the combined error uses `primary_err.kind`, `primary_err.detail`, `primary_err.retry_after` plus `last.detail`; the lone-route raise is `primary_err`. The "falling back to %r" log names the next non-degrade route. Put the lowering wrapper in a helper beside `_dispatch` to keep C901 counts flat. This changes the combined message from the primary's first error to its last — say so in the commit.
- [ ] **Step 4: Run** → PASS plus `test_llm.py test_model_guidance_dispatch.py test_image_description_draft.py test_llm_fakes.py` and the full backend suite.
- [ ] **Step 5: Commit** `feat(llm): lower image references per route, degrade refused images to text (#377)`.

### Task 8: Routes pass `images`; inspector reach

**Files:**
- Modify: `backend/src/grimoire/routes/scenes.py` (compose sites at the ordinary turn, director turn, retry, regenerate, staged turn; `get_scene_context` and the prompt-compare live branch)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`_compose` kwargs, both `from_snapshot` calls pass `campaign=cid`)
- Modify: `backend/src/grimoire/routes/mechanics.py` (both `compose_turn` calls; the helper takes the conn's `images`)
- Test: `backend/tests/test_post_images_routes.py`

**Interfaces:**
- Consumes: Tasks 3, 6, 7.
- Produces: `GET /campaigns/{cid}/scenes/{sid}/context` adds `send_images_reach` for the scene's routed chat connection.

- [ ] **Step 1: Failing tests** (TestClient, `app.dependency_overrides[routes.get_llm]` with a recording fake from `llm_fakes.py`, connection with `vision:"on"`, `send_images:"on"`, a scene with a library image in a user post): `POST .../chat` hands the fake a message list where `as_text` matches the off case and an `image_ref` is present before lowering (assert on the composed `PreparedMessages` the fake receives); the same with `send_images:"off"` has no ref; `GET .../context` has an `history_images` row when on and none when off, and `send_images_reach` is `"yes"` / `"off"`; a character-turn reroll from snapshot after forking resolves against the fork id (assert `messages.campaign`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** At each site add `images=store.post_images.images_for(conn)` beside `model=effective_model(conn)`; inspector routes compute it from the conn they already resolve (0 when `None`).
- [ ] **Step 4: Run** → PASS plus `test_scene_freeze.py test_routing_guard.py test_character_turns.py test_response_mechanics.py`.
- [ ] **Step 5: Commit** `feat(routes): compose scene turns with post images where the route reads them (#377)`.

### Task 9: Ledger image count

**Files:**
- Modify: `backend/src/grimoire/store/usage.py` (`record(images: int = 0)`, `Meter.done`, `_add`; `_ZERO` deliberately unchanged, so the empty summary, the persisted `usage_rollup` aggregate and the shell's Costs tail keep their shape)
- Test: `backend/tests/test_post_images_usage.py`

**Interfaces:**
- Produces: ledger row key `images` (absent when 0); a bucket gains `images` only once a row carrying one is folded in; `usage.scene_usage(...)["by_post"][i]["images"]`.

- [ ] **Step 1: Failing tests:** `record(task="chat", images=2)` row has `"images": 2`; `images=0` row lacks the key; a `Meter` whose holder has `images: 1` writes it; `scene_usage`'s by-post bucket sums images across a post and its reroll; the empty summary is unchanged (`test_usage_store.py` stays green); a persisted rollup aggregate written before the change (bucket without `images`) still folds a new row (no `KeyError`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `_add` folds only when the row has one: `if (n := _int(row.get("images"))): bucket["images"] = bucket.get("images", 0) + n`.
- [ ] **Step 4: Run** → PASS plus `test_usage_store.py test_usage_rollup*.py test_metrics*.py` (name the exact files with `ls backend/tests | grep -E 'usage|metrics'` first).
- [ ] **Step 5: Commit** `feat(usage): record how many images a call sent (#377)`.

### Task 10: Frontend

**Files:**
- Modify: `frontend/src/api/types.ts` (`Model.vision?: boolean | null`; `Config.send_images`, `send_images_limit`, `send_images_reach`; connection detail and `LLMConnectionDraft` `vision?: "" | "on" | "off"`; `UsagePostBucket.images?: number`; `SceneContext.send_images_reach?`)
- Modify: `frontend/src/components/ConnectionForm.tsx`'s `BLANK_CONNECTION` (`vision: ""`), which `SetupWizard.tsx` also renders
- Modify: `frontend/src/routes/ConfigView.tsx` (`DRAFT_FIELDS`, the `context` section's `fields`, the controls and the reach hint)
- Modify: `frontend/src/components/ConnectionForm.tsx` (+ `ConnectionEditor.tsx` where the form value is built from detail and saved)
- Modify: `frontend/src/components/cost.tsx` (`PostCost` title)
- Test: `frontend/src/routes/ConfigView.test.tsx`, `frontend/src/components/ConnectionEditor.test.tsx`, `frontend/src/components/cost.test.tsx`

**Interfaces:**
- Consumes: the API fields of Tasks 3, 8, 9.

- [ ] **Step 1: Failing tests:** ConfigView: the Context section shows a checkbox labelled "Send post images to models that can read them", checking it marks the section dirty and Save sends `send_images: "on"`; the limit input is disabled while off; with `send_images_reach: "unknown"` the hint text "not known to read images" is shown, with `"no"` a hint containing "cannot read images" is shown, with `"yes"` neither. The scene inspector needs no new control: its Images row is the per-campaign answer (spec), and `SceneContext.send_images_reach` is typed for later use. ConnectionForm: a "Reads images" select (options Auto/Yes/No) for openrouter and openai_compatible, absent for claude; selecting Yes saves `vision: "on"`; the hint reads "Catalog: reads images" when the selected model's row has `vision: true`, "Catalog: text only" for false, "Catalog: not stated" otherwise. PostCost: a bucket with `images: 2` has a title containing "2 images sent".
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/routes/ConfigView.test.tsx src/components/ConnectionEditor.test.tsx src/components/cost.test.tsx` → FAIL.
- [ ] **Step 3: Implement** following the existing `perception_rider` checkbox and `post_process` select patterns; `plural(n, "image")` + " sent" in the PostCost title list.
- [ ] **Step 4: Run** the three files → PASS; `npm run typecheck`.
- [ ] **Step 5: Commit** `feat(web): image sending setting, connection vision override, per-post image count (#377)`.

### Task 11: Gate

- [ ] **Step 1:** `make check PY=$(pwd)/backend/.venv/bin/python` (or each `check-*` target) → all green; if a ratchet reports an improvement, `make baseline` and commit the smaller files; never add findings.
- [ ] **Step 2:** `cd backend && PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep` must leave `snapshot.json` unchanged (off is byte-identical); if it changes, that is a bug, not a regeneration.
- [ ] **Step 3: Commit** any baseline updates: `chore: ratchet baselines after #377`.
