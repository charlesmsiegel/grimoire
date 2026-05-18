# Fork

Branch a whole campaign at a chosen turn. Both the original and the fork
are real campaigns with full state, developed independently. The
orchestrator has branch-based `fork`; this spec describes the richer
campaign-level model.

## Modes

### Fork from current

"I want to try a different direction from here."

1. Pick a new campaign id and create `data/campaigns/<new-id>/`.
2. Copy all narrative files (scenes, sheets, overrides, emergent content)
   to the new directory.
3. Library refs inherited unchanged (same pinned versions or
   `track_latest`).
4. SQLite rows tagged with the original `campaign_id` duplicated with the
   new `campaign_id` — facts, commitments, transient state, alternates,
   knowledge state, relationships, etc.
5. Images: hardlink by default (storage saving), deep-copy fallback when
   hardlink fails (cross-device).
6. Provenance: `forked_from: <original-id> at turn <post-id>`.
7. Both campaigns independent from this moment on.

### Fork from earlier turn

Same as fork from current, but only scenes / state up to the chosen turn
are copied. The fork's "latest post" is the chosen turn; future scenes
don't exist. The user resumes playing from the fork point.

Implementation: walk the audit log for the original campaign and replay
deltas up to the cutoff into a fresh state store. Faster than naive
duplicate-and-truncate because the audit log is the SSOT.

## SQLite implementation

```sql
-- For each campaign-scoped table
INSERT INTO facts (...)
SELECT ..., :new_campaign_id, ...
FROM facts
WHERE campaign_id = :original_campaign_id
  AND turn_no <= :fork_at_turn;
-- Same pattern for commitments, transient_*, knowledge_state,
-- relationships, audit log, etc.
```

For "fork from current", `turn_no <= MAX(turn_no)`.

## Governance

- Forks appear in the campaign list under their parent campaign,
  indented.
- A fork can itself be forked.
- Forks can be deleted independently of the parent.
- Forks can be **merged back** — re-apply selected turns from the fork
  into the parent, or accept the fork as the new parent and archive the
  original. Data model preserves enough provenance to support this; the
  concrete UI / API is v2.

## Backend surface

```python
async def fork_campaign(
    campaign_id: str,
    new_campaign_id: str,
    new_name: str,
    fork_at_post_id: Optional[str] = None,    # None = fork from current
    image_handling: ImageHandling = ImageHandling.HARDLINK,
) -> Campaign: ...
```

```
POST   /campaigns/{id}/forks
GET    /campaigns/{id}/lineage              # parent + descendants
```

WebSocket event:

```json
{ "type": "campaign_forked", "source_campaign_id": "...", "new_campaign_id": "..." }
```

## Frontend

Fork dialog:

```
Fork campaign

  Source: by-night-london (currently at scene 47, post p_4710)
  Fork from:
    ◉ Current state
    ◯ Earlier post: [____________]

  New campaign name: [_________________]
  New campaign id:   [auto-suggested]

  Image handling:
    ◉ Hardlink (saves disk space)
    ◯ Deep copy

  ☐ Make this the active campaign after forking
  Describe the divergence (optional): [__________]

  [Fork]   [Cancel]
```

Campaign list:

```
Campaigns
├── by-night-london
│   └── by-night-london-what-if-negotiation   (forked at scene 47)
└── a-saga-in-iberia
```

Auto-suggested names are bad (`by-night-london-fork-1`). The dialog
should require a descriptive name and offer a "describe the divergence"
field for audit color.

## Concurrency

- Fork mid-flight (during canonical turn streaming): fork is queued and
  runs after the turn completes.
- Two forks open concurrently: allowed; each campaign has independent
  state and event streams.

## Audit and observability

```
[fork] source=by-night-london new=by-night-london-what-if-negotiation \
       at_post=p_4710 image_handling=hardlink
```

Time-travel queries in `16-observability.md` are aware of fork events as
first-class.

## Interactions

- `swipes-alternates.md` / `retcon.md`: fork-then-retcon is a common
  pattern; the retcon spec nudges toward forking on heavy retcons.
- `03-state-store.md`: every campaign-scoped table needs the
  fork-aware bulk-copy path.
- `12-imagegen.md`: image hardlinking vs deep-copy; cross-device
  fallback.

## Failure handling

| Failure | Behavior |
|---|---|
| Hardlinks fail (e.g. cross-device) | Fall back to deep copy automatically; log warning |
| Audit-log corruption affecting fork-from-earlier | Fall back to copy-and-truncate strategy; logged as degraded |
| Fork during streaming turn | Queue; run after turn completes |

## Performance

- Fork from current: SQLite COPY + file copy / hardlinks → < 1s for a
  typical campaign.
- Fork from earlier turn: audit-log replay → typically 1–5s for a long
  campaign.
