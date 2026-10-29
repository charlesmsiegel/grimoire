# Selective migration and porting from V1

**Do not merge V1 `main` wholesale into V2.** The first V2 commit intentionally deletes the old application code from this *branch* only. No source, committed history, license, or user library is destroyed: V1 remains in `main`, and the exact baseline is `e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce`.

## Candidates to port, not assumptions to inherit

| V1 subsystem | V2 treatment |
|---|---|
| V3/V2/PNG/CHARX card parser and writer | Port later as a loss-aware import/export adapter, not the character store |
| LLM providers, routing, retries and cost accounting | Port after independent provider interfaces; no card-specific prompt coupling |
| Async scene streaming, run fencing and cancellation | Reuse proven lifecycle and failure tests when V2 owns turns/events |
| Atomic file writes, locking, conflict and recovery tests | Port guarded file protocols behind native domain writers |
| Dice parser and existing mechanic packs | Port independent dice utility; preserve different ruleset semantics |
| Images, content-addressed blobs, expression outfits and art | Port independently of the V1 actor directory/asset assumptions |
| React design system, web UI, VN view and Android shell | Reuse selectively only after stable V2 API contracts; no dead V1 routes |
| History/retrieval/epistemic filters | Mine for invariants and tests; feed the native subjective actor view |
| Stored V1 worlds and campaigns | Add a previewable, non-destructive converter; never rewrite on first boot |

This initial branch cut **does not claim these subsystems are unimportant**. They are valuable reference code and regression specifications. Their old dependency graph, however, is not the new domain contract.

## Suggested V2 PR sequence

1. **Records and source resolution:** human-editable source definitions, world-owned characters, optional catalogs and strict ID resolution.
2. **Campaign identities:** separate actor instances, controller assignments, source baselines and local overrides.
3. **Character perspectives:** event evidence, observation channels, beliefs and a bounded view for one actor.
4. **A complete freeform turn:** user intent, fair adjudication, one accepted event and stable narration of the result.
5. **A minimal playable interface:** one scene, text input, actor presentation, deterministic persistence and visible correction.
6. **Interoperability and rich infrastructure:** V1 importer/compatibility, card I/O, image assets, model routing, mechanic packs, voices, VN and Android, each through narrow adapters.
7. **Long-running worlds:** selective root rebases, history-preserving forks, durable actions, memory, campaign simulation and multiplayer.

These are *guiding capability stages*, not a mandatory serialized issue queue. Submit mutually independent PRs in parallel when contracts are stable.

## Migration safety

Do not import real user data into this public repository, examples or tests. Synthetic worlds and actors only. Build a read-only migration report first, with explicit compatibility/failure fixtures, then a distinct user-approved apply operation with rollback. Export/import must avoid dropping unknown source fields, unrelated art, historical responses and privately held character knowledge.
