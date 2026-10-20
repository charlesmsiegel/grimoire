## Minimal complete example

A tiny but complete worked example — one group, one sheet type composing it, one check. Modeled
directly on `pool-basic`; scale this pattern up per the interview's answers.

`sheets.json`:

```json
{
  "groups": {
    "attributes": {
      "label": "Attributes",
      "fields": [
        {"key": "grit", "label": "Grit", "type": "dots", "max": 5, "default": 1},
        {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1}
      ]
    }
  },
  "sheet_types": {
    "wanderer": {
      "label": "Wanderer",
      "kind": "characters",
      "groups": ["attributes"],
      "fields": [
        {"key": "resolve", "label": "Resolve", "type": "resource", "max": 8}
      ],
      "derived": {"instinct_pool": "grit + wits"}
    }
  }
}
```

`checks.json`:

```json
{
  "instinct": {
    "label": "Instinct",
    "roll": "{grit + wits}d10 t6",
    "requires": ["attributes"]
  }
}
```

Note this check's `roll` spells out `grit + wits` directly rather than referencing the sheet
type's `instinct_pool` derived field — a check's placeholders are validated against its
`requires` groups at pack-load time, before any sheet type is in the picture, so only
group-level names (and group-level `derived`) are load-time-reachable there. A sheet-type-level
`derived` name like `instinct_pool` is real and usable elsewhere (e.g. displayed on the sheet),
but only becomes reachable in a `roll` once an actual actor/sheet type is bound at fire time —
don't rely on it validating at load time the way group names do.

That's a load-valid pack on its own (plus `module.md`); `rules/*.md` and `content/` are additive
from there. Verified against the real validator while writing this skill.
