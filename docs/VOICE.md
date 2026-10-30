# Portrayal voice evidence contract

**Status:** the first slice is implemented in the pure package `grimoire.voice`. It is an in-memory contract: no file format, no provider and no persistence. Roadmap: [Characterization and Dialogue](roadmap/sprints/characterization-and-dialogue.md) (sprint 014), [Wave 2 handoffs](roadmap/WAVE2_HANDOFFS.md#providervoicequality-boundaries-013--014--015--027).

A voice is **authored evidence about how one portrayal sounds in text**. It is not a campaign individual's mood, knowledge, beliefs or relationship history. It is also not a physical speaking voice (timbre, TTS) or the narrator's style. V0.2.1's quality baseline is **one independent generation per speaking NPC**, and this contract only offers that shape: one bounded brief and one renderer call per speaker.

## Authored values

| Value | Meaning |
|---|---|
| `VoiceAnchor(anchor_id, aspect, text)` | A descriptive rule that examples can only imply. `aspect` is one of `register`, `cadence`, `diction`, `habit`, `avoid`. |
| `VoiceExample(example_id, text, reveals=())` | One line the portrayal could say. It is a style reference, never text to reproduce. `reveals` names the fact ids the line presupposes. |
| `VoiceProfile(portrayal, anchors, examples, imported=None)` | The voice of exactly one **pinned `character` revision**. Its fields are exactly these four. |
| `VoiceCatalog(profiles)` | Profiles keyed by exact pinned revision. A duplicate is refused. It never follows a head and never matches by name. |
| `ImportedVoiceSource(format, card_version, example_messages, anchor_text, extensions)` | Character Card V3 voice fields kept **exactly**, for loss-aware import/export. **Never rendered.** |

Validation is constructor-enforced and raises `InvalidVoice`:
- Ids follow the [identity](IDENTITY.md) segment grammar.
- Text must contain words. Blank, invisible-only or punctuation-only text is refused.
- `reveals` must be a list or tuple, so audit subjects keep a stable order. Sets and mappings are refused.
- Anchors are capped at 400 characters and examples at 600.
- A profile holds at most 32 anchors and 32 examples. Ids are unique across both, and a profile must have at least one item.

These caps are **provisional** first-slice values.

`extensions` are frozen losslessly, keeping their order:
- A dict becomes a read-only mapping, and a list or tuple becomes a tuple.
- The allowed leaves are str, int, finite float, bool and None.
- `thaw()` returns plain data. A tuple thaws to a list.
- An `ImportedVoiceSource` is not hashable, and neither is a profile that carries one or a `ResolvedVoice` (they hold read-only mappings).
- Nesting is capped at 64 levels.
- A bad value is located **by index path only** (`extensions[0][1]`). Card keys can carry private names, so error messages never echo them. No error message echoes an offending id, marker or text.

How V1 `mes_example` and `voice_anchor.md` content becomes native examples and anchors is decided by the importer (026), not here.

## Campaign binding and resolution

`VoiceBinding(campaign_id, actor_id, portrayal=None, anchors=(), examples=(), suppress=())` is campaign-owned and never edits a profile.

`resolve_voice(actor, catalog, definitions, binding=None) -> ResolvedVoice`:

- **One pin mechanism.** The actor's own `source` pins a revision or follows the head. A binding naming the actor's own entity is `VoiceMismatch("conflicting_pin")`.
- **Alternate portrayal.** A binding may select another entity only through a **direct `alternate_portrayal` link** from `definitions.links_for(actor.source)`. The link must meet all of these:
  - If its actor endpoint is pinned, that endpoint equals the actor's resolved revision.
  - If its portrayal endpoint is pinned, that endpoint equals the requested ref exactly.
  - It is declared by the actor's source or by the portrayal's source. A third-party add-on cannot authorize it.

  `same_individual`, `derived_variant` and `overrides` do not qualify. There is no transitive search and no name lookup. Anything else is `VoiceMismatch("unlinked_portrayal")`. The display name stays the actor's (`local_name`, or its own definition's name).
- **Exact revision.** The catalog must hold the exact resolved revision; otherwise `MissingVoice("no_profile")`. A voice for `r1` is never used for `r2`. A missing definition is `MissingVoice("missing_definition")` with the index reason in `.detail`.
- **Local edits.**
  - A local item with a source item's id replaces it in place, but only an item of the same kind: an example cannot replace an anchor, or the reverse (`InvalidVoice("kind_mismatch")`). Other local items are appended.
  - `suppress` removes source items.
  - Suppressing an unknown id is `InvalidVoice("stale_override")`. Suppressing an id that is also replaced is `InvalidVoice("suppress_replaced")`. The merged result obeys the caps.
- **Campaign-original actors** (no `source`) speak only from local items. A portrayal on one is `VoiceMismatch("campaign_original")`.
- **Other refusals.** A binding for another actor or campaign is `VoiceMismatch("other_actor")`. An actor left with no items is `MissingVoice("no_profile")`.

`ResolvedVoice` validates itself on construction: typed items within caps, pinned revisions, and provenance covering exactly its item ids. It records:
- `actor_revision`: the actor's own definition revision;
- `portrayal`: the profile revision used;
- the merged items;
- `provenance`: `"source"` or `"campaign"` for each item id;
- `imported`, carried unchanged.

## One speaker's fragment

`speaker_fragment(voice, *, secrets, known, max_chars=1200) -> SpeakerFragment`. The epistemic inputs `secrets` and `known` have **no defaults**: forgetting them is a `TypeError`, not an unscreened brief.

`secrets` must be an ordered sequence (sets are refused), and each marker must contain words.

1. **Tag allowlist.** An example is included only if **every** id in `reveals` is in `known`. Otherwise the omission reason is `unknown_fact`, which covers facts that appear in neither `secrets` nor `known`.
2. **Marker denylist.** An anchor or example whose text states a marker of any `SecretFact` not in `known` is omitted as `secret_marker`. Matching uses `grimoire.evals.text.contains_marker`, which is the [story-quality gate](evals/STORY_QUALITY.md)'s normaliser and whole-phrase matcher: curly quotes, zero-width spaces and case all fold, and `duke` does not match `dukedom`.
3. **Greedy budget.** Anchors come first, then examples, each in authored order. Each item is either included whole while the text stays within `max_chars`, or omitted as `budget`. The examples header counts together with the first example. Items are never cut. `max_chars` must be at least 200, and a name too long for it is `InvalidVoice("name_exceeds_budget")`.

The text is deterministic. Each item is one line, with all whitespace and line separators collapsed:

```text
Voice of <name>. Write only <name>'s own words.
- <aspect>: <anchor>
Style examples (reference only; never repeat them verbatim):
> <example>
```

`SpeakerFragment.omitted` lists `Omission(kind, item_id, reason, subject)`: ids and fact ids only, for audit. The speaker's own name is **not screened**: it is authored or director-assigned. Only `speaker_fragment` can construct a fragment. A seal passed as an `InitVar` makes direct construction and `dataclasses.replace` raise `InvalidVoice("unsealed_fragment")`. This guards against accidental hand-built briefs, not a hostile caller.

## Rendering contract

`render_speakers(speakers, renderer, controls) -> tuple[SpokenLine, ...]` takes `(fragment, cue)` pairs.

- **Before any call:**
  - Every pair must be a sealed fragment and a text cue.
  - Each actor appears once; a repeat is `VoiceMismatch("duplicate_speaker")`.
  - Each actor's controller (`core.authority.controller_of`) must be `DIRECTOR`. A player character, an unassigned actor or ambiguous control raises `AuthorityError`. Delegated PC speech is out of scope here.
- **The calls.** The renderer is invoked **once per speaker** with `SpeakerRequest(actor, fragment_text, cue)` and nothing else. It never sees omissions, item ids, fact ids, `reveals`, imported card text or another speaker's brief.
- **The results.**
  - A non-text result is `InvalidVoice("renderer_result")`.
  - A blank or punctuation-only result is `InvalidVoice("silent_render")`: a silent NPC would be a paid orphan call.
  - Any exception, from the renderer or from these checks, means nothing is returned.

**The cue is not screened.** `render_speakers` is not an epistemic boundary for it. The caller (027 with 012) must build each cue from that speaker's own perspective. A test pins that the gate catches a leaky cue.

## Helpers

- `example_copies(text, voices)` returns `(actor, example_id)` for every example of every given voice that `text` reproduces: at least `SAMPLE_COPY_CHARS` (24, imported from the gate) contiguous normalised characters. Screened-out examples count too. Given the scene's voices, it agrees with the gate's `voice_sample_copy`.
- `voice_overlap(a, b)` is the character-trigram cosine of two voices' authored text.
- `flattened(voices, threshold=0.85)` returns the sorted actor-ref pairs whose overlap is at or above the threshold. Giving the same actor twice is `InvalidVoice`. It is a provisional authoring warning: it catches near-duplicate voices, not bland ones, and it is not a quality score.

**Dependency note.** Production voice screening imports `grimoire.evals.text` and `grimoire.evals.checks.SAMPLE_COPY_CHARS` (only those modules; enforced by the purity allowlist). Changing the gate's normaliser or copy length therefore changes runtime screening, by design: generation and evaluation use one matcher.

## Proof

`backend/tests/test_voice_scenarios.py` uses a synthetic cast: Oda, Bellamy, Quill and Hask discussing a guild betrayal over three turns. Bellamy learns the secret on turn 2, Quill on turn 3, Oda and Hask never. A deterministic fake renderer voices each NPC through `speaker_fragment` and `render_speakers`. It emits every second voice word and then a **speaker-neutral** cue.

The parsed `story-quality/1` run has:
- no hard findings: no leaks, no copies, and one `npc_generation` call per line;
- distinguishability exactly 1, and still 1 with the cues removed.

Two controls show the attribution comes from the fragments. Relabelling the lines (rotated speakers) scores 0, and a cue-only renderer scores at most 1/4. Several variants fail:
- a copied example: `voice_sample_copy`;
- one call voicing all four NPCs: `separate_generation`;
- flattened voices: `voice_regression` against the distinct cast;
- a leaky cue: `knowledge_leak`.

This proves the contract's plumbing: each fragment reaches only its own speaker's line, screened for knowledge. **It is not a claim about model quality**, and no LLM is involved.

## Not yet covered

| Not covered | Owner |
|---|---|
| Persistence and file format | 010 |
| Which kinds besides `character` carry a voice | 009 |
| Deriving `known`/secret sets from evidence | 012 |
| Provider calls, call planning, usage accounting and cue construction | 027 |
| CC V3 parsing and import | 026 |
| `story-quality/2` | 015 |
| Author-editable anchors and correction→preference | milestone 2 |
| Long-term voice development, emotional state and code-switching | milestone 3 |
| Narrator style | — |
| Physical voice/TTS | — |
| Delegated PC speech | — |

None of this makes V2 playable.
