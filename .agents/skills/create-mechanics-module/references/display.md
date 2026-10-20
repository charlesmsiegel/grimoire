# Optional layout and theme

### 7b. Optional: `layout.json` + `theme.json` — pretty rendering

Both cosmetic and optional; problems land in the pack's `display_errors`
(shown in the module library) and never invalidate the module.

`layout.json` arranges each sheet type's widgets. A node has exactly one of
`row` (array of nodes, horizontal), `column` (array, vertical),
`group: "<gid>"`, `fields: ["<key>", ...]`, `derived: ["<name>", ...]`, or
`use: "<fragment id>"`; optional `title` (panel heading) on any node and
`grid: true` (stat-grid cells) on `group`/`fields` nodes. Shared
`fragments` keep groups rendering identically across sheet types:

    {
      "fragments": {"traits": {"group": "attributes", "grid": true, "title": "Attributes"}},
      "sheet_types": {"warden": {"column": [{"use": "traits"}, {"fields": ["essence"], "title": "Power"}]}}
    }

Every field/derived may be placed at most once per sheet type; anything
unplaced renders in a trailing "Other" section, so partial layouts are fine.

`theme.json` is a token whitelist (never CSS — `theme.css` is rejected):
`colors` (`bg`+`ink` must be set together, plus `muted`/`accent`/`rule`,
hex only), `fonts` (`display`/`body`, each one of `display`, `body`,
`mono`, `serif`, `sans`), `dots` (`circle`/`square`/`diamond`), `corners`
(`sharp`/`rounded`):

    {"colors": {"bg": "#191521", "ink": "#d8d2c4"}, "fonts": {"body": "serif"}, "dots": "diamond"}

Validate as in step 8 and check `display_errors` is empty in the pack
payload (`/api/modules/<mid>`).
