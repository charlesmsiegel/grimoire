# Manifest contract

Source: [populate_world_content.py](../scripts/populate_world_content.py).

## The manifest contract

```json
{
  "world": "<wid>",
  "entities": [{"kind": "locations|items|groups|lore", "name": "...", "body": "...",
                "keys": "", "owners": "characters:<id>[,characters:<id2>]", "source": "..."}],
  "reclassifications": [{"lore_id": "...", "new_kind": "locations|items|groups",
                          "name": "...", "body": "...", "source": "..."}],
  "tags": [{"display_name": "...", "source": "..."}],
  "greeting_imports": [{"character": "<cid>", "version": "<vid>", "titles": ["...", ...]}],
  "greeting_edges": [{"greeting_ref": "new:<cid>:<vid>:<idx>|id:<gid>",
                       "leads_to": [...], "excludes": [...], "source": "..."}],
  "greeting_gating": [{"greeting_ref": "...", "requires_tags": [...], "present": [...],
                        "source": "..."}],
  "open_questions": ["..."],
  "greeting_gaps": ["..."]
}
```
`greeting_ref` uses `"new:<character>:<version>:<idx>"` for a greeting the SAME manifest's
`greeting_imports` creates (`idx` 0 = `first_mes`, then each non-empty `alternate_greetings`
entry in card order), or `"id:<gid>"` for a pre-existing greeting. `source`/`evidence_source` is
mandatory on every entry: a file path plus a short quoted excerpt. `owners` on an entity, when
set, must be a comma-joined `"characters:<id>"` string using **exact** existing character ids —
never a bare name, never a guessed abbreviation.
