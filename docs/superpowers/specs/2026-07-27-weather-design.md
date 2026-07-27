# Weather: procedural generation, overrides, and time-advance

Status: draft (brainstorm output; not yet through the spec → planning gate)
Issues: #44 (procedural generation), #45 (campaign-local manual override),
#46 (extractor-driven override), #104 (weather changes across locations on
advance), #195 (scene HUD widget), #40 (atmosphere config editor)

## Problem

There is no weather anywhere in the codebase. Five open issues assume one, and
each assumes a slightly different model, so the first job is to settle a single
model all five can share.

What play actually needs:

- A sky for the current location and moment, stable across re-reads — asking
  twice must not reroll it.
- Weather that *persists* the way real weather does. Some places run three days
  of unbroken grey; others squall and clear twice before lunch. Both must be
  expressible, per place.
- Narration and GM fiat outrank the dice. If the story says it snowed, it
  snowed, and the system records that rather than arguing.
- No unbounded state growth. A campaign that plays fifty in-game years must not
  accumulate fifty years of weather rows.

## Design decisions

Settled during brainstorming, in the order they were decided.

1. **Weighted tables, normalized per season.** Table entries carry relative
   weights, normalized over whatever the season's table holds — `{overcast: 4,
   light rain: 3, clear: 3}` gives overcast 4/10. Nothing sums to 100, adding an
   entry re-normalizes the rest, and a zero-weight entry is never drawn (a handy
   way to disable one without deleting it).
2. **Three axes: condition, temperature, wind.** Rich enough for the HUD to have
   real fields and for the extractor to have somewhere to write; not an open
   schema. Rejected: condition-only (nothing structured for #104 to compare
   against), and open-ended custom axes (the #46 extractor cannot map narration
   onto axes it can't know in advance).
3. **Time blocks, not days.** Weather resolves per block — dawn / morning /
   afternoon / evening / night — and the persistence chain runs across
   consecutive *blocks*, spanning day boundaries. One knob then produces both
   observed behaviors: low persistence gives a coast that squalls and clears
   before noon, high gives a week of unbroken grey.
4. **Persistence on both climate and location.** The climate sets the baseline
   (a temperate interior is steadier than a coast); a location may override it,
   so one notoriously changeable mountain pass can sit inside an otherwise
   ordinary climate.
5. **A climate registry, two-tier like `store/calendars/`.** Named climates are
   documents; a location references one by id. Presets ship in the repo, private
   ones load from `<GRIMOIRE_HOME>/climates/`.
6. **Climates declare their own seasons, as fractions of the year.** Not the
   calendar. Weather-seasons are not calendar-seasons: monsoon country has two,
   a temperate coast four, somewhere equatorial one. Fractions (`wet: 0.45–0.75`)
   keep presets portable across calendars of any year length, and make
   hemispheres free — a southern location just uses a climate whose ranges are
   shifted.
7. **Pure function plus a sparse override layer.** Weather is computed, never
   stored, except for authored exceptions. A campaign with no overrides stores
   nothing; storage is proportional to fiat, not to campaign length.
8. **Spatial correlation deferred, with a hook.** Making a storm sweep across
   neighboring locations needs a location containment hierarchy that doesn't
   exist. Deferred — but the seed derivation leaves a seam for it (§ Weather
   zones).

Decisions **not** put to the user, flagged for review: axis ordering and the
`requires_temp` constraint (§ Drawing a block), and block boundary times
(§ Blocks).

Still open: how persistence survives a boundary without an unbounded walk
(§ Anchoring). The first draft's answer was wrong; two candidate replacements
are recorded there.

## Data model

### Climate documents

JSON, not Python. This is a deliberate deviation from `store/calendars/`, which
mirrors that package's *two-tier lookup* but not its provider-class shape:
calendars need code (arithmetic, holiday rules), climates are pure data, and
data is what #40's atmosphere editor can edit from the UI.

- Shipped presets: `backend/src/grimoire/store/climates/*.json`
- Private climates: `<GRIMOIRE_HOME>/climates/*.json`, loaded best-effort by
  `store/climates.py` exactly as `calendars/plugins.py` loads custom providers —
  a malformed file is skipped, not fatal, and is retried on the next call.

Per the privacy rule in `CLAUDE.md`, only generic real-world-ish climates ship
in the repo (`temperate-coastal`, `temperate-interior`, `high-desert`,
`monsoon`, `boreal`, `equatorial`). Anything invented for a private world is a
plugin and is never committed.

```json
{
  "id": "temperate-coastal",
  "name": "Temperate Coastal",
  "persistence": 0.35,
  "seasons": [
    {
      "name": "winter",
      "from": 0.92,
      "to": 0.21,
      "temperature": { "freezing": 2, "cold": 6, "mild": 2 },
      "conditions": {
        "clear": 2,
        "overcast": 5,
        "light rain": 4,
        "storm": 1,
        "snow": { "weight": 2, "requires_temp": ["freezing"] }
      },
      "wind": { "calm": 1, "breeze": 4, "strong": 3, "gale": 1 }
    }
  ]
}
```

- `from`/`to` are fractions of the year in `[0, 1)`. `from > to` wraps the year
  end (the winter above runs from 92% through 21%). Seasons must cover the year
  without gaps; overlaps resolve to the first match in document order.
- **`from == to` means a full-year season.** Without this, a one-season
  equatorial climate — a case this spec explicitly claims to support — has no
  legal encoding: the range forbids `to: 1`, and `from: 0, to: 0` would
  otherwise read as an empty interval and fail the coverage check.
- A table entry is either a bare weight (`"clear": 2`) or an object with
  `weight` plus modifiers. Both forms parse to the same internal shape.
- **Every season must declare at least one condition with no `requires_temp`**,
  validated at load. This is what makes the degenerate-table fallback safe
  (§ Drawing a block) without letting it emit a constrained condition outside
  its band.
- `persistence` is `[0, 1]`: 0 rerolls every block independently, 1 holds the
  underlying stream constant. Absent means 0.5.

  Note what `1` does **not** mean. It cannot mean "the weather never changes,"
  because the season table underneath it changes: a held value that no longer
  exists in the new season's table, or a constant noise sample mapped through a
  different inverse CDF, both yield different weather at a season boundary. The
  guarantee is therefore scoped: **at `persistence: 1` weather is constant
  within a season and may change only where the table changes.** That is also
  the desirable reading — a climate whose sky is genuinely immutable across a
  season change would be a bug, not a feature.

### Locations

Locations are generic entities with string-scalar frontmatter (`store/entities.py`,
`store/frontmatter.py`), so both new keys are strings:

```yaml
climate: temperate-coastal
persistence: "0.3"        # optional; overrides the climate's
weather_zone: saltmarch   # optional; see below
```

These three keys are **not reachable through the product today**, and adding
them to the frontmatter is not enough to change that. `entity_schema.FIELDS`
has no `locations` entry at all, so `field_keys("locations")` is empty and
`routes._check_fields` 400s on every one of them; `ENTITY_FIELDS.locations` in
`frontend/src/api/client.ts` is likewise `[]`, so `EntityEditor` never sends
them. Both tables must gain location entries — the module docstring in
`entity_schema.py` already requires keeping them in sync — or per-location
climate is a hand-edit-the-Markdown feature, which for a decision this design
puts at the location level is not acceptable.

Lenient parsing throughout: an unknown `climate` falls back to the campaign
default, an unparseable `persistence` falls back to the climate's. Weather never
raises into a turn.

"Unparseable" means **not a finite number in `[0, 1]`**, not merely "float()
raised." `"2"`, `"-1"`, and `"NaN"` all parse successfully and are all invalid:
they would make the carry test always true, always false, or undefined, which is
worse than the fallback because it looks like a working setting. Range-check
after parsing, and fall back on failure.

A campaign-wide default climate lives in **`campaigns/<cid>/climate.json`**,
beside `calendar.json` and copied at create time the same way, so a world that
hasn't tagged its locations still gets weather. It needs its own file and its
own name: `weather.json` is already the override store, and conflating
configuration with campaign-local fiat would put authored spans and library
defaults in one blob.

```json
{ "default_climate": "temperate-coastal", "epoch": "1492-01-01" }
```

Both fields are optional. When the file is absent, `default_climate` is unset,
**or the climate it names cannot be loaded**, the fallback is the shipped
`temperate-interior` preset — a named, committed default rather than an implicit
one, so an untagged world and a missing file resolve identically. The
unloadable case matters as much as the missing one: a configured default can
name a malformed climate, or a custom-only climate the user has since deleted,
and without this rule an untagged location has nowhere left to fall — and a
location naming that same dead id would bounce to the campaign default and back.
The shipped preset terminates the chain unconditionally. `epoch` is only
meaningful under Option 1 (§ Anchoring), and is stored as a fixed day.

### Weather zones (the deferred-correlation hook)

The seed is derived from a **zone**, not a location: `weather_zone` on a
location, defaulting to the location's own id. Locations sharing a zone share a
random *stream*.

A shared stream is only a shared *sky* when the zone's members also agree on
climate and persistence — the same random values mapped through different
weighted tables, or advanced at different carry-forward rates, produce different
weather. Since locations may independently set both, the guarantee is stated
precisely: **members of a zone that agree on climate and persistence get
identical weather; members that disagree get correlated weather.** Disagreement
is reported as a validation warning, not an error, because correlated-but-not-
identical is sometimes exactly right — a sheltered valley and the exposed peak
above it should share a front and not a temperature.

The guarantee is also **procedural-scope only**. Overrides are keyed by location
id and applied after the shared stream, so a manual or extractor override on one
member diverges it from the rest of its zone regardless of configuration. That
is the right default — GM fiat about the docks should not silently repaint the
whole town — but it means "identical weather" describes the generated layer, not
the resolved one. A zone-wide override is expressible by writing the same span
to each member. Not by keying it to `_default`: that key is campaign-wide, so in
any campaign with more than one zone it would repaint every location on the map.
A zone-keyed override representation would be the clean fix if this proves
tedious in practice; it is not in v1.

That is crude — it is co-location, not a storm front moving across a map — but
it costs nothing now, covers the common case (six locations inside one town),
and is the seam real spatial correlation slots into later: when a location
hierarchy exists, zones get derived from it and the interpolation happens inside
zone resolution without touching callers.

### Override store

`campaigns/<cid>/weather.json` — campaign-local, never a world file, per #45 and
#46. A JSON sidecar rather than campaign location-copies, so `sync.py`'s
hash-diffing of entity blobs is undisturbed.

```json
{
  "saltmarch-docks": [
    {
      "id": "ovr-3f2a91",
      "from": "1492-06-14T06:00",
      "to": "1492-06-17T18:00",
      "condition": "blizzard",
      "temperature": "freezing",
      "wind": "gale",
      "note": "the Wintertide storm",
      "source": "manual",
      "set_at": "2026-07-27T18:22:04Z"
    }
  ]
}
```

- Keyed by location id; `_default` applies campaign-wide.
- Each entry is a span, not a point — GM fiat is usually "it snows for three
  days," and per-block rows for that would be absurd.
- **Spans are half-open: `from <= t < to`.** Adjacent overrides sharing an
  endpoint must match exactly one record, or the precedence rules below decide
  between two spans that were never meant to compete. This convention also
  governs how writers merge, truncate, and round durations.
- **Comparison happens on the fixed-day axis, never on the stored strings.**
  `from`, `to`, and the queried moment are each parsed through the campaign's
  primary provider to `(fixed_day, minute)` before any ordering test. Native
  date strings are not lexicographically ordered under every provider — the
  shipped Hebrew calendar formats as `{year}-{token}-{day}` with a month *name*
  token (`hebrew.py:70`), so `5784-nisan-01` and `5784-tishrei-01` sort
  alphabetically into the wrong order. String comparison would silently match
  the wrong spans rather than fail loudly. An endpoint with no clock takes
  minute 0 for `from`, and for `to` **the following fixed day at minute 0** —
  not end-of-day. Native times validate only through 23:59
  (`calendars.minutes_of`), so an end-of-day `to` under `t < to` would exclude
  the final minute and `from: 1492-06-14, to: 1492-06-14` would cover nothing
  at all rather than the whole day it names.
- **Records store resolved coordinates, not just native strings.** Each span
  carries `from_fixed` / `to_fixed` as `(fixed_day, minute)` pairs alongside the
  human-readable native strings, and those are what resolution compares.
  `PUT /campaigns/{cid}/calendar` (`routes.py:2079`) can swap the primary
  provider after overrides exist, and it validates only the new config — a
  Gregorian-looking `1492-06-14` then either fails to parse under `hebrew` or
  silently maps to a different day. Native strings are for display and
  re-editing; the fixed coordinates are authoritative and survive a provider
  change. Writers compute both. The same applies to Option 1's epoch, which is
  stored as a fixed day rather than a native string.
- **`id` is required**, and it is what makes the precedence backstop
  implementable — the rules below need a stable per-span identity that array
  position cannot supply. Writers generate one; a hand-authored entry missing an
  `id` gets a canonical one derived by hashing `(from, to, source, axes)`, so
  the backstop still holds for files edited outside the app.
- Axes are individually optional: an override may pin `condition` alone and let
  temperature and wind still be drawn, which is what narration usually gives us
  ("it was raining" says nothing about wind).
- `source` distinguishes `manual` (#45) from `extractor` (#46), which sets the
  resolution order and lets the UI show provenance.

## Resolution

Per #44's instruction to design the chain up front. One entry point:

```
current_weather(croot, cid, location_id, native) -> dict | None
```

**It returns `None` when either input is missing**, and callers treat that as
"no weather section." Both `scenes.get_location_history` and `get_time_history`
document `Missing ⇒ []`, so a scene that has just been created legitimately has
no location, no moment, or neither — and `context._assemble` runs while exactly
such scenes are being opened and generated. Making only the Jinja section
tolerant does not help: the exception would be raised computing the section's
input, before any template is touched. The nullable return is the guard.

1. Manual override covering this location and moment (`source: manual`)
2. Extractor override covering it (`source: extractor`)
3. Procedural draw

Applied **per axis**, not per record, so a manual `condition` and a procedural
wind coexist. The returned dict carries `source` per axis so the HUD can mark
what was authored.

Source rank alone does not settle it — the span-list schema permits several
rank-equal matches, and array order must not be the tiebreak. Within a rank,
for each axis independently:

1. **Specificity**: a span keyed by this location beats one keyed by `_default`.
2. **Recency**: among equally specific spans, the newest `set_at` wins.
   `set_at` is a **wall-clock write timestamp** (`now_iso()`), deliberately not
   an in-game moment. Since `set_datetime` permits moving a scene backward in
   time, an in-world stamp would let a freshly authored override lose to one
   written weeks ago, and the GM's most recent instruction is what should win.
   It is the only field in the record on the real-world clock; `from` and `to`
   remain native campaign moments.
3. **Determinism backstop**: if `set_at` ties too, the lexicographically
   greatest span `id` wins, so the result never depends on file ordering.

Writers should merge or truncate an existing overlapping span rather than
stacking a second one, but the resolver must not assume they did.

## The procedural draw

### Blocks

Five blocks, by wall-clock minute (time-of-day is already handled by the
agnostic helpers in `calendars/base.py`, not by providers):

| block | from | to |
| --- | --- | --- |
| night | 21:00 | 04:00 |
| dawn | 04:00 | 08:00 |
| morning | 08:00 | 12:00 |
| afternoon | 12:00 | 17:00 |
| evening | 17:00 | 21:00 |

A scene with a date but no clock — common, since `set_datetime` makes time
optional — resolves to `afternoon`, the block containing midday. That keeps
dateless-time scenes stable and sensible rather than defaulting to a sky nobody
expects.

**Block identity is an integer on a continuous minute axis**, never a
`(date, block name)` pair. Index from `fixed_day * 1440 + minutes`, so `night`
is one contiguous interval and 22:00 does not land in a different block from
02:00 the following morning. Concretely: the 00:00–03:59 stretch belongs to the
*preceding* date's `night`. Deriving identity from the date would let the sky
reroll halfway through a single night — the one span most likely to hold a
continuous scene.

### Anchoring

Persistence means block *N* depends on block *N−1*, which naively means walking
back to campaign day zero — unbounded work as a campaign ages.

**The year-anchor-plus-burn-in scheme in this spec's first draft was wrong**, and
is recorded here because the reasoning generalizes. It seeded a fresh chain per
`(campaign, zone, year)` and discarded 20 blocks of burn-in before the anchor.
Burn-in makes the first retained state approach the correct *marginal*
distribution, but it cannot correlate that state with the final block of the
preceding year, because the preceding year's chain was never consulted. At
`persistence: 1` each year is internally constant and still free to jump every
New Year — directly contradicting "1 never changes," and failing the year-seam
test this spec asks for. Fixing correlation across a boundary requires either
crossing it or removing it.

Two ways to do that. **This is the one open architectural decision in the spec.**

**Option 1 — anchor at the campaign epoch, memoize each year's end state.**
Keeps the Markov chain exactly as described; only the anchor moves. Year *N*
starts from year *N−1*'s final block, computed lazily and cached, recursing back
to the campaign's first year. Correlation is then genuinely continuous. Cost is
O(years since epoch) on first touch and O(1) after — ~91k cheap RNG steps for a
50-year span, which is milliseconds, and a jump to year +50 pays it once.
Carries three liabilities. **Dates before the epoch need a construction the
forward chain cannot supply** — a seeded Markov walk generates successors, not
predecessors, and `set_datetime` permits arbitrary backward jumps, so a prequel
scene set before the epoch has no defined sky. Two acceptable resolutions, and
the spec must pick one rather than leave it to the implementer: run a second
chain *backward* from the epoch state, using the same transition rule and a
distinct seed, which stays deterministic and joins continuously at the epoch
because it starts from that state; or reject pre-epoch dates outright and place
the epoch a generous margin before the campaign's first recorded moment so the
case effectively cannot arise. The backward chain is preferable — rejection
turns a legal in-app action into an error the user cannot fix. Note that
Option 2 has no such case: a noise field is defined at negative indices exactly
as it is at positive ones.

The per-year memo must be invalidated when climates or
location settings change, or a long-running process will keep serving a stale
walk while a restarted one computes something different — breaking the
cross-process determinism this spec promises; the climate content hash and the
resolved location settings therefore belong in the cache key, not just the year.

And the epoch itself has to be **persisted and immutable**, which is why
`climate.json` carries an `epoch` field. There is no in-game campaign start date
to derive it from: `create_campaign` records only real-world `created`/`updated`
stamps (`campaigns.py:81-87`), and scene native dates arrive later and can be
edited or added in any order. Deriving the epoch from the earliest known scene
would mean that adding a prequel scene silently rewrites every sky the campaign
has ever shown — a determinism violation that would surface as "the weather
changed in scenes I already played." Written once, on first weather resolution,
and never recomputed.

**Option 2 — replace the chain with random-access correlated noise
(recommended).** Instead of a Markov walk, each axis is a correlated noise
function over the block index, with correlation length derived from
`persistence`, sampled through the season table's inverse CDF. Runs of weather
emerge from the noise's correlation length rather than from carry-forward.

**The marginal must be uniform before it reaches the table**, and this is a real
trap rather than a detail. Inverse-CDF sampling reproduces the declared weights
only when fed uniform quantiles, but conventional smooth value noise is not
uniform — interpolation concentrates samples toward the middle of the range, so
mapping it directly through the table would systematically under-draw the rare
entries at the tails and over-draw the common middle ones, quietly violating the
weights the whole data model is built on. Specify the process as **correlated
Gaussian noise pushed through the standard normal CDF Φ** to obtain uniform
marginals, and only then through the table's inverse CDF — a Gaussian copula.
Any construction with provably uniform marginals is acceptable; an unstated one
is not.

This is a real pivot, but it dissolves three findings at once rather than
patching them:

- *No anchor, so no seam* — at any boundary, year or season. `persistence: 1`
  becomes infinite correlation length, holding the sample constant, which under
  the scoped guarantee (§ Climate documents) means constant weather within a
  season.
- *No memoized walk, so nothing to invalidate* — the cache-coherence liability
  above simply does not exist, and determinism is structural rather than
  maintained.
- *Condition revalidation becomes automatic* — condition is always
  `inverse_cdf(table filtered by temperature(t), noise(t))`, so when temperature
  moves, the filtered table moves with it and an ineligible value cannot be
  carried. The explicit revalidation rule above becomes a property instead of a
  step.

It also fixes something nobody flagged: at a season boundary the table changes
while the noise value stays continuous, so weather *morphs* into the new season
instead of stepping. The cost is that runs are no longer geometrically
distributed the way a Markov chain's are, and `persistence` becomes a
correlation length rather than a carry-forward probability — a knob that needs
recalibrating, not a knob that disappears.

**It also costs the cheap accumulators**, which is the strongest argument
against it. § Accumulators claims snowpack folds over "the same walk the
resolver already performs" — true under Option 1, false here, because Option 2
performs no walk. Answering "how deep is the snow on day 900" would mean
replaying every prior block or storing checkpoints, reintroducing exactly the
unbounded work or state this design exists to avoid. The bounded answer is a
**windowed fold**: accumulate over the last *K* blocks only, K fixed per
accumulator, which is O(K) random access and needs no state. That is defensible
on the merits — snowpack and ground saturation have finite physical memory, and
nothing that melted six months ago is still on the ground — but it is a genuine
semantic narrowing from "true history," and multi-year drought would not be
expressible this way. Option 1 gets accumulators for free; Option 2 buys them.

### Drawing a block

Given the season (from the year fraction) and the previous block's draw:

1. With probability `persistence`, carry the previous block's value for an axis
   unchanged. Otherwise draw fresh from that axis's weighted table.
2. Axes are drawn in order **temperature → condition → wind**, because
   conditions can constrain on temperature. A condition entry's `requires_temp`
   drops its weight to zero when the drawn band isn't listed — which is what
   stops the tables from producing snow at high summer temperatures.
3. Wind is independent of both.

Two consequences of applying persistence per axis, both of which the naive
reading gets wrong:

- **A carried condition is revalidated against the newly resolved
  temperature.** `requires_temp` filters a freshly *drawn* condition, but a
  block that carries `snow` forward while its temperature rerolls from
  `freezing` to `mild` would emit exactly the combination the constraint
  exists to prevent. Carrying forward is therefore conditional: if the held
  value is ineligible under the new temperature, it is discarded and redrawn
  from the filtered table. Persistence orders the axes, it does not exempt
  them.
- **A carried value is also revalidated when the season table changes.** The
  same defect one boundary out: at `persistence: 1` under Option 1, carrying
  every axis unchanged forever means a winter `freezing` temperature persists
  into a summer table that offers only `hot`, and the sky holds a value the
  active season does not contain. Whenever the active table changes, any held
  value absent from the new table is remapped — by rank if the tables share an
  ordering, otherwise redrawn. This is what makes the scoped `persistence: 1`
  guarantee (§ Climate documents) true rather than aspirational under Option 1;
  under Option 2 it is automatic, since the sample is remapped through the new
  table by construction.
- **The filtered table can be empty**, and normalizing an empty table is
  undefined. A climate whose season pairs a `mild` temperature band with
  conditions that all require `freezing` is structurally valid but unresolvable.
  Guard on both sides: **validate at load** that every temperature band with
  positive weight has at least one eligible positive-weight condition, and
  **at runtime** fall back rather than raising. The fallback is the
  highest-weight **unconstrained** condition — the one every season is required
  to declare (§ Climate documents) — ties broken by entry key. It must not be
  the highest-weight *unfiltered* entry: that reintroduces `requires_temp`
  violations by the back door, emitting snow at `mild` in exactly the case the
  guard exists to handle, and contradicting the invariant the constraint test
  asserts. A malformed private climate degrades to a boring sky; it never takes
  a turn down, and never lies about the temperature.

Seeding depends on which option § Anchoring resolves to, and the two are not
interchangeable:

- **Option 1**: a stable hash of `(cid, zone, epoch)` seeds the chain once, then
  the walk advances it. Per-year seeding is what the discarded first draft did
  and is exactly what reintroduces the New Year seam.
- **Option 2**: a stable hash of `(cid, zone)` parameterizes the noise field,
  which is then sampled at the continuous block index. There is no per-year
  seed at all — introducing one would decorrelate the samples either side of
  New Year and recreate the seam the option exists to remove.

Either way the campaign id is in the seed per #44 — two campaigns in one world
must not share skies.

### Accumulators (later, not v1)

Snowpack, ground saturation, and drought are folds over the block sequence: snow
depth adds on snowfall blocks and subtracts on above-freezing ones. No new
storage, still deterministic. This is what #104's "the snow at the pass has
melted" actually needs — but weather ships first.

**How cheap this is depends on the open decision in § Anchoring.** Under
Option 1 the resolver already walks the chain, so an accumulator is a fold over
work that is happening anyway, over true history, at no extra cost. Under
Option 2 there is no walk, so accumulation must be a bounded window over the
last *K* blocks — still deterministic and still O(1)-ish, but a narrower
semantics that cannot express multi-year drought. Whichever is chosen, the
resolver should expose the block sequence it uses so accumulators consume it
rather than reimplement it.

## Integration

- **Prompt** (#44): a new tolerant section `templates/scene/sections/weather.j2`,
  fed by a `weather` key alongside `_today_data`, omit-never-crash like every
  other section. It must be added in **two** places, and adding it to only one
  fails silently: `templates/scene/system.j2`, whose hard-coded include chain is
  what actually reaches the model, **and** `context._SECTIONS`, which is only
  the token-breakdown view (`context.py:644` says so outright — it *mirrors*
  system.j2 rather than driving it). Registering in `_SECTIONS` alone yields a
  weather line visible in the token breakdown and absent from every prompt.
- **HUD** (#195): `GET /api/campaigns/{cid}/scenes/{sid}/weather`. The scene id
  is load-bearing, not decorative — location and moment live per scene in
  `location_history` / `time_history`, and `CampaignView.tsx` tracks its own
  `activeId`, so a campaign has as many "current moments" as it has scenes.
  Resolving from `cid` alone would return an arbitrary scene's sky. Accepts
  optional explicit `location` / `native` overrides for previewing a moment the
  scene isn't at.
- **Manual override** (#45): `PUT /api/campaigns/{cid}/weather`, with the target
  in the **request body** — `{location, from, to, condition?, temperature?,
  wind?, note?}`, where `location` accepts `_default`. The campaign id alone
  cannot say what is being overridden: `weather.json` holds the target only as
  an outer object key and the span record itself has no location field, so a
  handler given just `cid` has nothing to key the write by. The body carries it
  rather than the route, so one endpoint covers both a location and the
  campaign-wide default. **Both** weather
  routes must be declared before the generic entity routes — not just the PUT:
  `@router.get("/campaigns/{cid}/{kind}")` sits at `routes.py:3783`, so a
  later-registered weather GET is captured as an entity-list request for kind
  `weather`.
- **Extractor** (#46): a `weather_edits` key in `templates/absorb/system.j2`,
  parsed and validated in **`absorb.parse_output`**, then a `weather` branch in
  `absorb.materialize` (before = current resolved value, after = narrated value)
  and in `apply_edits`, writing `source: extractor`. The `parse_output` step is
  not optional plumbing: it rebuilds an explicit dict of known keys
  (`absorb.py:127-142`) rather than passing the model's object through, so an
  unlisted `weather_edits` is dropped on the floor and the materialize and
  apply branches are unreachable no matter how well the prompt performs.
  Rides the existing review checklist in `CampaignView.tsx` — no new UI.
  Since every override record needs `from`/`to` but narration gives a value and
  not a span, the extractor needs a span rule: **default to the block containing
  the narrated moment**, and let the schema carry an optional explicit duration
  for narration that states one ("the rain set in for three days"), mapped to
  whole blocks by rounding outward. Narration that implies onset rather than
  extent ("rain begins") takes the default — one block, re-narratable next turn.
- **Climate editor** (#40): `GET /api/climates` (the merged list, each entry
  tagged `builtin` or `custom`), `GET /api/climates/{id}`,
  `PUT /api/climates/{id}`, and `DELETE /api/climates/{id}` — the delete route
  is what makes the revert behavior below reachable, and without it a custom
  copy can never be undone from inside the app. The write path needs a rule the spec cannot leave
  implicit: shipped presets live inside the installed backend package and must
  never be written, so **editing a preset copies it to
  `<GRIMOIRE_HOME>/climates/{id}.json` and edits the copy** — same
  copy-on-write shape the codebase already uses for campaigns diverging from
  worlds. Lookup precedence follows: a custom climate shadows a shipped one of
  the same id. Deleting a custom climate reverts to the preset rather than
  removing the id, and an id with no preset behind it simply disappears.
- **On advance** (#104): `sweep(cid, sid, prev_native, now_native)`. The previous
  moment and the scene are both required: `scenes.set_datetime` permits
  arbitrary jumps, including backward ones, and keeps a separate history per
  scene, so a sweep that knows only the new "now" cannot say what changed.
  Since generation is pure, "changes across locations on advance" mostly falls
  out for free; the sweep exists to *name* the transitions for the digest, not
  to cause them.

## Testing

Backend, `backend/tests/test_weather.py`, store isolated with
`monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`:

- Determinism: same `(cid, zone, block)` yields the same weather across
  processes and across resolution order.
- Persistence: at `persistence: 0.9` runs are demonstrably longer than at
  `0.1`, over a sampled year.
- Year seam: the distribution of change-events across the year boundary matches
  the distribution mid-year, **and** at `persistence: 1` the value is identical
  either side of a New Year — the assertion the first draft's burn-in would
  have failed. Scope the equality to a climate whose season does *not* boundary
  at fraction 0, or assert on the underlying correlated sample instead: a season
  change at New Year is licensed to change the weather by the scoped guarantee,
  so an unconditional assertion would reject a conforming implementation.
- Season seam: likewise across a season boundary, where the table changes but
  the underlying stream should not jump.
- Block identity: 23:00 and 01:00 the following morning resolve to the same
  block; 03:59 and 04:01 do not.
- Override precedence: location beats `_default`, newer `set_at` beats older,
  and two spans differing only in array order resolve identically.
- Span boundaries are half-open: at the shared endpoint of two adjacent
  overrides, exactly one matches.
- Span comparison under a non-lexicographic provider: under `hebrew`, spans
  whose native strings sort the wrong way round still match by real
  chronology — the test that fails if comparison is done on raw strings.
- Precedence under backward time travel: an override written later in wall-clock
  time wins over one written earlier, even when the scene has since moved
  backward in-world.
- Climate editing: writing a shipped preset creates a custom copy under
  `<GRIMOIRE_HOME>/climates/` and leaves the installed file untouched;
  deleting the copy restores the preset.
- Date-only spans: `from: <day>, to: <day>` covers every minute of that day,
  including 23:59, and does not bleed into the next.
- Provider swap: overrides written under `gregorian` resolve to the same real
  moments after the campaign's primary calendar is changed.
- Config leniency: `persistence` of `"2"`, `"-1"` and `"NaN"` each fall back to
  the climate's value; a campaign default naming a deleted climate falls back
  to the shipped preset rather than looping.
- Season-boundary carry: at `persistence: 1`, a held value absent from the new
  season's table is remapped rather than retained.
- Weight fidelity: over a long sample the observed frequency of each condition
  matches its normalized weight, at several persistence settings — the test
  that catches a non-uniform marginal reaching the inverse CDF.
- Degenerate tables: a season whose conditions are all ineligible for a drawn
  temperature yields the unconstrained fallback, and never a `requires_temp`
  violation.
- One-season climate: `from == to` covers every day of the year.
- Empty scene state: a scene with no location, no moment, or neither resolves
  to `None` and renders no section, without raising during `_assemble`.
- Season fractions: a climate resolves to the same season names under a 365-day
  and a 400-day calendar.
- Constraints: no `snow` draw ever co-occurs with a temperature band outside its
  `requires_temp`.
- Wrapping seasons: a `from > to` season contains both year-end and year-start
  days.
- Leniency: unknown climate id, unparseable `persistence`, malformed climate
  plugin — all degrade, none raise.
- Overrides: per-axis merge, span boundaries inclusive/exclusive, manual beats
  extractor beats procedural.

Templates render via `scripts/verify_templates.py`.

## Deferred

- **Spatial correlation** — storms sweeping across adjacent locations. Needs a
  location hierarchy. Hook left at § Weather zones.
- **Accumulators** — snowpack, saturation, drought. Design the walk to permit
  them; build later.
- **LLM-authored flavor** — #44's Option B, as a "reroll with AI" garnish on top
  of the procedural draw rather than a replacement for it.

## Open questions

- **Chain-from-epoch or correlated noise?** (§ Anchoring.) The one open
  architectural decision; everything else in the spec holds either way. The
  trade has sharpened since it was first written: noise is cleaner at every
  seam and needs no cache invalidation, but it gives up true-history
  accumulators for a bounded window, which is a real loss for multi-year
  drought and a real cost against #104.
- Do weather edits join `changes.json`? `absorb._BROWSABLE_KINDS` currently
  gates this and weather is arguably too noisy to browse (#46's own note).
- Should the campaign-wide default climate be copied into the campaign at
  create-time (like `calendar.json`) or resolved by reference to the world?
  Copy-on-create matches the established pattern but diverges silently.
