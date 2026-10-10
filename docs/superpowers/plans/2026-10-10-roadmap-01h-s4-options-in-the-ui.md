# 01h-S4: Options in the UI — Plan

**Goal:** the embedding options 01h-S2/S3 deliver server-side are stated and
read in the app. The model facts panel edits them (for a model that may
embed), a Suggest fills the form from the conventions model authors publish,
Save goes through the existing `confirm_embedding` question, the Models
page's Embedding row says what its requests carry, and a test whose endpoint
ignored a requested width says so with both widths. Delivers spec §4.4 in
full; the UI half of 01h-C1/C2.

**Spec:** `docs/superpowers/specs/2026-10-09-roadmap-01h-embedding-options-design.md`
§3.5 (the suggestion table), §4.4, §11 (Frontend, Probe), Slices 01h-S4.

**Review gates:** not run (speed mode); the Codex gates are owed.

**Open questions (§13):** Q5 (suggest by model id): the recommendation,
suggest only, never applied -- Suggest fills the form and a save (confirmed)
is the only write. Q6: the generic field, so the suggestions table carries
the request-field conventions too (Voyage `input_type`, Jina v3 `task`).

**01s:** the Models summary's Embedding row (01s §3.1) is today's
`EmbeddingRow` in `components/models/RoleRow.tsx`, so the line goes there.

## Design

- **Server.** `EmbeddingCard.options`: the stated options the role's requests
  are built from -- the endpoint dict's own `EmbedOptions`, put back in its
  stored shape (`facts.options_block`, which omits the default
  `dimensions_field`) -- or null while the role is off or none are stated.
  One deliberate widening of the spec's "the canonical dict": the stored
  block includes the query side, because the line says what is sent
  ("query/document prefixes"), and the canonical form drops the query side.
- **Client helpers** (`components/models/embeddingOptions.ts`): the form
  (`embeddingForm`, which also reads an invalid block so it can be fixed),
  `blockOf` (only the mode's fields, a width only when typed), `sameBlock`,
  `suggestFor` (§3.5's families by model id), `optionsSummary` (with "two
  requests per recall" for `param`) and `mismatchLine`.
- **The panel** (`ModelFactsPanel`, `EmbeddingOptionsFields`): the section
  only when the model's `embed` is not a known `no`. Input type (None /
  Prefix / Request field), the mode's fields (prefixes in textareas, since
  a published query instruction has a newline), Dimensions and its field.
  Save sends `embedding` only when the block changed; the server's 400
  `confirm_embedding` raises the existing inline confirmation, whose button
  resends with the yes. The view's sidebar has an "Embedding options"
  section (the summary, "None", or the validator's sentence for an invalid
  block). A failed embed verdict with both widths reads "requested N, test
  returned M: this endpoint ignores `dimensions`", in the Tested list and the
  test dialog's results.
- **Models row:** "Options: …" under the rate line, only when stated.

## Files

- `backend/src/grimoire/store/inference/facts.py` (`options_block`),
  `backend/src/grimoire/store/inference/settings.py` (`_embedding_card`)
- `frontend/src/api/types.ts`, `frontend/src/components/models/embeddingOptions.ts`,
  `frontend/src/components/models/EmbeddingOptionsFields.tsx`,
  `frontend/src/components/models/RoleRow.tsx`,
  `frontend/src/components/inference/TestCallDialog.tsx`,
  `frontend/src/routes/ProvidersView.tsx`
- Tests: `test_inference_settings.py`, `embeddingOptions.test.ts`,
  `ModelsView.test.tsx`, `ProvidersView.test.tsx`

## Tests

- The card reads back its options (null with none).
- Helpers: round trip, only the mode's fields, sameBlock, width check, each
  suggestion family, the summary, the mismatch sentence.
- Panel: no section for a known `no`; Suggest fills and saves nothing; Save,
  the 400, the confirm and the resend with `confirm_embedding: true`; an
  unchanged block is not sent; an invalid block and a width mismatch are
  said.
- Models row: the options line, and none without options.
