# Observability — "Why this character?" debug view (issue #352)

> Spec §7 of the original observability remaining-design pass: surface a per-character debug view explaining inclusion reason for each character that ended up in a past turn's prompt.

**Upstream:** `2026-05-18-observability-COMPLETED.md` §7 (deferred).
**Issue:** [#352](https://github.com/charlesmsiegel/grimoire/issues/352).
**Module touched:** `frontend/` only.

## Why this is small

The audit pipeline already carries the data:

- `grimoire.types.context.ContextSource` already has `inclusion_reasons: list[InclusionReason]`.
- `grimoire.context.builder` already populates that field at every emission site (`PC_CARD`, `PRESENT_IN_SCENE`, `MENTIONED_IN_RECENT_POSTS`, `COMMITMENT_OPEN_TO_PC`, `KEYWORD_TRIGGERED`, `RELATIONSHIP_TO_PRESENT`, `PINNED_BY_USER`, `SCENE_ANCHOR`, etc.).
- `AuditStore.record` JSON-serializes the full `ContextSource[]` (including `inclusion_reasons`) into `turn_audits.prompt_messages.sources`.
- `GET /api/observability/turns/{turn_id}/prompt` already returns those sources verbatim.
- `GET /api/observability/turns?campaign_id=...` already returns recent audits.

The only missing piece is a **frontend lens for past turns**. The live-preview Context Inspector renders inclusion reasons for the *next* prompt; we need the same idea pointed at an audit.

## Scope (settled in brainstorming)

- **Lens scope:** characters only (`kind === "character"`).
- **Mount point:** new `/observability/turns` route, sibling of the existing Health panel.
- **Backend shape:** the frontend filters the existing `/prompt` response — zero backend changes.
- **Turn picker:** campaign + turn list, leveraging the existing `list_turn_audits` endpoint.
- **Display:** resolve character display names via the existing characters API; fall back to `owner_id` literal.

## Pieces

### 1. `frontend/src/api/observability.ts`

Small REST client wrapping the two existing endpoints used by this view.

```ts
export interface TurnAuditSummary {
  turn_id: string;
  campaign_id: string;
  branch_id: string;
  scene_id: string;
  started_at: string;
  player_input: string;
  llm_model: string;
}

export interface ContextSourceFromAudit {
  source_id: string;
  owner_id: string | null;
  kind: string;
  scope: string;
  tier: ContextTier;
  inclusion_reasons: InclusionReason[];
  tokens: number;
  summary: string;
}

export interface TurnPromptResponse {
  messages: unknown[];
  sources: ContextSourceFromAudit[];
  budget_used: Record<string, number>;
  messages_hash: string;
  composition_snapshot: unknown;
  summary: unknown;
}

export const observabilityApi = {
  listTurns(campaignId: string, limit = 50): Promise<TurnAuditSummary[]>;
  getTurnPrompt(turnId: string): Promise<TurnPromptResponse>;
};
```

`ContextTier` and `InclusionReason` are imported from `./inspector.ts` (already defined there). No duplication.

> Note: if PR #357 (Health panel) has already shipped a `frontend/src/api/observability.ts`, this work extends that file rather than replacing it.

### 2. `frontend/src/routes/observability/inclusionReasonLabels.ts`

Extract `REASON_LABELS` from `frontend/src/routes/campaign/Inspector/SourceList.tsx` into a shared module so the new panel and the existing live inspector render the same human labels. Update `SourceList.tsx` to import from the new module. No behavior change.

### 3. `frontend/src/routes/observability/WhyCharacterPanel.tsx`

Two-pane layout:

- **Left pane — turn picker.**
  - Campaign id input (defaults from URL `?campaign_id=` if present).
  - Calls `observabilityApi.listTurns(campaignId)` on campaign change.
  - Renders a scrollable list of audits: `turn_id` (short hash), `started_at` (localized), and the first 60 chars of `player_input`.
  - Selected row highlighted.

- **Right pane — character cards.**
  - On selection, calls `observabilityApi.getTurnPrompt(turn_id)`.
  - Filters `sources` to `kind === "character"`.
  - **Groups by `owner_id`** — one character can produce multiple sources (full card, voice anchor, recent dialogue). The card shows the union of reasons across all of that character's sources, and the sum of tokens.
  - For each unique `owner_id`, fires `charactersApi.resolve(owner_id, campaign_id)` to get a display name. Falls back to `owner_id` literal on error.
  - Renders one `CharacterReasonCard` per character: name, tier badge (highest-priority tier across that character's sources), tokens, ordered reason chips using the shared `REASON_LABELS` map.

### 4. Route wiring

- Add `/observability/turns` to `frontend/src/App.tsx`.
- Add a nav link "Why this character?" in `frontend/src/shell/NavSidebar.tsx` under the observability section (next to "Health" if #357 has merged).

## Data flow

```
user → /observability/turns
  ↓ on mount with campaign_id: observabilityApi.listTurns(campaignId)
  ↓ shows scrollable list of audits
user clicks a turn
  ↓ observabilityApi.getTurnPrompt(turn_id)
  ↓ filter response.sources to kind === "character"
  ↓ group sources by owner_id, union inclusion_reasons, sum tokens
  ↓ for each unique owner_id, charactersApi.resolve(...)
  ↓ render one CharacterReasonCard per character
```

## Edge cases

| Case | Behavior |
|---|---|
| Audit 404 | Empty-state: "No audit available for that turn." |
| No character sources in the audit | Empty-state: "This turn's context had no character sources." |
| Audit predates §7 wiring (empty `inclusion_reasons`) | Render "(no declared reason)" — matches `SourceList` convention. |
| Character resolution fails (deleted card) | Fall back to `owner_id` literal. Card still renders. |
| Two characters with same display name | Show `owner_id` as a small subscript under the name so they're distinguishable. |
| `/listTurns` returns an empty list | Empty-state: "No audits yet for this campaign." |

## Testing

### Backend

`backend/tests/observability/test_audit.py` — additive round-trip test ensuring `inclusion_reasons` on a `ContextSource` survive `AuditStore.record` → `AuditStore.get`. Likely covered transitively already; if so, skip this case.

### Frontend

`frontend/src/routes/observability/__tests__/WhyCharacterPanel.test.tsx` (Vitest + React Testing Library):

1. **Renders one card per character with union of reasons.** Mock `getTurnPrompt` to return two character sources for the same `owner_id` with disjoint reasons; assert one card appears with both reasons shown.
2. **Filters out non-character sources.** Mock returns a `lore` source alongside characters; assert the lore source does not produce a card.
3. **Renders the empty-state when no character sources.** Mock returns sources with only non-character kinds; assert the "no character sources" message.
4. **Falls back to `owner_id` when character resolution fails.** Mock `charactersApi.resolve` to throw; assert the literal `owner_id` is rendered.
5. **Audit 404 is handled.** Mock the prompt endpoint to 404; assert the panel shows the not-available message.

`frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts` — snapshot the exported label map so future additions to the `InclusionReason` union surface as a test diff.

## Out of scope

- Browsing across multiple campaigns simultaneously.
- Character avatars (just names).
- Showing reasons for non-character sources — spec §5/§6 territory.
- Backend joins/aggregations or a dedicated `/turns/{turn_id}/characters` endpoint.
- Live-tailing audits (spec §13).

## Risks / coordination

- **PR #357** (Frontend Health panel) is open and adds `frontend/src/api/observability.ts`, `frontend/src/routes/observability/`, and a nav entry. If #357 lands first, this work *extends* those files. If this lands first, #357 will need a small merge. The conflict surface is the API module and the nav entry — both additive.

## Spec self-review notes

- No placeholders, TBDs, or contradictions.
- Scope is one PR, frontend-only.
- Edge-case table is the only place behavior could be ambiguous and it's explicit.
