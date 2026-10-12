# 08-S4 Transcript expansion and its guard rule — Plan

**Spec:** [08](../specs/2026-10-09-roadmap-08-history-search-documents-design.md), sections 10 and 12, slice 08-S4. **Delivers:** 08-C3b in full. No caller in the app until 09 and 12.

## Tasks

- [x] **`store/searchdocs/expand.py`** (`store/searchdocs/__init__.py` is docstring-only, the `continuity` precedent). `expand(cid, sid, *, phase, terms, identity=None, radius, max_excerpts, max_posts, max_bytes, fallback="none", allow_whole=False) -> Expansion | None`:
  - `phase` is keyword-only with no default; anything but `prompt` / `display` (the store phase included) is `ValueError`, as is a `fallback` other than `none` / `tail`.
  - The identity is checked before the read (a renamed scene is followed through `find_by_identity`) and after it; a gone or replaced scene, or any failure to read, is `None`.
  - The whole transcript is viewed once (`regex_view.view`, one constant phase per branch), so depth counts as the context builder counts it.
  - Eligible posts drop hidden posts and director notes in both phases; transitions (no speaker) and rolls stay; indices are absolute. Keys are `r-<response_id>`, else `p-<post_id>`, else `""`, with `response_part` as `part`. Prompt-phase images are alt text (`export.drop_images`).
  - Terms are case-folded, whitespace-collapsed, de-duplicated, at least two characters, at most 16. A post scores by distinct terms in its phased content or speaker, then occurrences capped at 5 per term, then lower index.
  - Hits in score order claim `radius` posts either side; touching windows merge; a window that does not fit shrinks to its hit; a hit that cannot get `MIN_POST_BYTES` stops the run. Hits are framed on their rarest term, neighbours cut from the end nearer the hit; every cut ends on whitespace except a single overlong token, cut at a character boundary.
  - Matches that fit nothing give `mode="empty"`, `budget_exhausted=True`. No match gives `empty`, or `tail` with `fallback="tail"`. `allow_whole` returns the whole eligible transcript when it fits.
  - Each excerpt's `text` is `chronicle.transcript_text` over a slice of the viewed list, in the function that viewed it.
- [x] **Guard** (`test_regex_prompt_guard.py`): scans `store/searchdocs/` and `store/history/` (09's; absent, so harmless); accepts a slice of a viewed list (`shown[start:end]`); new rule: every `expand.expand(...)` / `<...>.searchdocs.expand.expand(...)` call in a function that renders a template, or in a pinned reader, passes the constant `phase="prompt"`. Planted cases: a display phase, a variable phase and no phase fail; the constant prompt phase, a display read that renders nothing, and a local callable named `expand` pass. No `# regex-ok` marker spent.
- [x] **Tests** (`test_searchdocs_expand.py`): spec section 13's 08-C3b list.

## Deviations from the spec

- **Budget accounting.** `max_bytes` bounds the rendered text: each post is charged its label, its content and the template's framing (`**`, `:** ` and the blank line that joins two), so `Expansion.bytes` (the sum of the excerpt texts) can never exceed it.
- **Elision is marked.** A cut end carries `…`, charged inside the post's budget, so a reader (or a model) can tell a cut post from a short one.
- **The guard rule matches the module binding only.** A bare `expand(...)` is not this function: the context builder's macro expansion is a local callable of that name, and the import guard only allows `from ..searchdocs import expand`.

## Verification

`pytest tests/test_searchdocs_expand.py tests/test_regex_prompt_guard.py`, the import and paths guards, `make check-lint check-mypy`.
