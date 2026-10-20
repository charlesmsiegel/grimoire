# Rules activation and optional content

### 6. Author `rules/*.md` — LLM rules text

One markdown file per rules doc; the slug is the filename stem (`combat.md` → `combat`).
Frontmatter carries **activation flags**, combinable:

| flag | when it loads | typical use |
|---|---|---|
| `always: true` | whenever the module is bound to the campaign | the core digest — how the dice work, at a glance |
| `on_roll: true` | injected into the continuation whenever a roll resolves | dice interpretation — botches, crits, counting successes |
| `keys: [...]` | keyword-activated against recent scene text (lorebook mechanism) | situational rules triggered by in-fiction words ("fight", "shift") |
| `sheet_types: [...]` | loaded when an actor with that sheet type is in the scene | splat powers / class features specific to that character type |

A doc with **no** flags loads only when a check's `checks.json` `rules:` list names it — use that
for check-specific procedure text that doesn't need standing activation. Body is the markdown fed
to the LLM; keep it short and procedural, not prose-y lore.

### 7. Optional: statted content

If the module ships ready-made entities (a monster, a magic item), add
`content/<kind>/<id>.md` — same markdown + frontmatter shape as a world entity — and, if it
carries stats, a sidecar `content/<kind>/<id>.sheet.json`:

```json
{"sheet_type": "talisman", "fields": {"power": 3, "charges": {"current": 10, "max": 10}}}
```

Note the `resource` field's current/max pair shape (`{"current": ..., "max": ...}`) versus a
plain scalar for `dots`/`number`/`text`/`list` fields. The sidecar validates against the resolved
sheet type exactly like a campaign sheet file.
