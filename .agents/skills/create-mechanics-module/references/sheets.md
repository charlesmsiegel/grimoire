# Sheets and expressions

### 3. Author `sheets.json` — groups first

Groups are shared field sets referenced by multiple sheet types — DRY without inheritance's
override semantics. Write every group the interview surfaced before touching sheet types:

```json
{
  "groups": {
    "attributes": {
      "label": "Attributes",
      "fields": [
        {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1}
      ],
      "derived": {}
    }
  },
  "sheet_types": {}
}
```

A group's `derived` (optional) computes group-local math (e.g. an ability modifier) visible only
to that group's own fields — see Expression language below.

### 4. Author `sheets.json` — sheet types per kind

Add one sheet type per character-type/object-family the interview named. Each has: `label`,
`kind` (one of `characters`, `items`, `locations`, `creatures`, `groups`, `lore` —
`characters` covers PCs too), `groups` (ordered list of group names to compose in), `fields` (its
own fields on top of the groups), and optional `derived` (sees the full assembled field set plus
every composed group's derived names).

Field keys must be unique across a sheet type's composed groups + its own fields; derived names
must not collide with field keys. Reserved-key rules: field and derived names cannot be `difficulty`, `modifier` or `new`. A field key may not be
an expression function name (`min`, `max`, `floor`, `ceil`, `abs`), and may not equal another
field's implicit resource-max name (`<key>_max` of a `resource` field in the same assembled
set). `backend/src/grimoire/store/modules/validate.py`'s validator catches all of these at load time.

**Field-type table** (from the spec's "Module data contract" section):

| type | meaning | extras |
|---|---|---|
| `number` | integer scalar | `default`, optional `min`/`max` |
| `dots` | small rated scalar (rendered as dots later) | `max` (required), `default` |
| `track` | boxes that check off (a damage track) | `max` (required) |
| `resource` | mutable current/max pair (HP, willpower, essence) | `max` (required — the *default* maximum; each sheet stores its own current/max pair, since maxima vary per character) |
| `text` | freeform string | — |
| `list` | list of strings (merits, gear) | — |
| `ref` | reference to an entity | `ref_kind` in `entities.ENTITY_KINDS` |

`resource` and `track` are the fields play can mutate — flag them for anything that goes up or
down during a session; everything else is closer to a fixed rating.

Sheet *instances* exist as of Phase 3: campaigns store per-entity sheets (created/edited from
each entity's detail view via the Sheet section), and worlds can hold per-module starting sheets
that seed into new campaigns bound to that module — so a module you author here is immediately
usable end-to-end.

**Expression-addressability** (matters for `derived` and for `checks.json` below, per the spec's
"Expression language" section): a `resource` field named `essence` contributes **two** names to
expressions — `essence` (current value) and `essence_max` (its maximum); a `track` field
contributes just its checked-box count under its own name. `dots` and `number` fields contribute
their scalar directly. **`text`, `list` and `ref` fields are not addressable** in any expression — don't
reference them in `derived` or `roll`. Unknown names are a load-time validation error, never a
roll-time surprise; the whitelist is numeric literals, names, `+ - * /` (true division), `//`,
unary minus, comparisons, `and`/`or`/`not`, ternary `x if c else y`, and calls to `min`, `max`,
`floor`, `ceil`, `abs` only — no attributes, subscripts, strings, or arbitrary calls.

For creation-point budgets or advancement costs, read [progression](progression.md).
