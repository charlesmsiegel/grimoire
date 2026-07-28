# Weather: procedural generation, overrides, and time-advance

Status: settled. Through four rounds of adversarial review (#229); the
anchoring question that was open at merge is now resolved in favour of a
correlated noise field. Ready for the planning stage.
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
   afternoon / evening / night — and persistence correlates *consecutive
   blocks*, spanning day boundaries. One knob then produces both
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

9. **Persistence is a correlated noise field, not a Markov chain**
   (§ The noise field). How persistence survives a year or season boundary
   without an unbounded walk was the last open question in this design; it is
   settled in favour of random-access noise. The rejected chain-based
   alternatives are recorded there, because the obvious reading of "today
   depends on yesterday" is a chain and two drafts of this spec tried it.

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
      "temperature": [
        { "name": "freezing", "weight": 2 },
        { "name": "cold", "weight": 6 },
        { "name": "mild", "weight": 2 }
      ],
      "conditions": [
        { "name": "clear", "weight": 2 },
        { "name": "overcast", "weight": 5 },
        { "name": "light rain", "weight": 4 },
        { "name": "snow", "weight": 2, "requires_temp": ["freezing"] },
        { "name": "storm", "weight": 1 }
      ],
      "wind": [
        { "name": "calm", "weight": 1 },
        { "name": "breeze", "weight": 4 },
        { "name": "strong", "weight": 3 },
        { "name": "gale", "weight": 1 }
      ]
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
- **Tables are JSON arrays of `{name, weight, …}`, never objects keyed by
  name.** Order is semantically significant here (see below), and JSON objects
  are unordered by specification — a formatter, an editor round-trip, or a
  parser that does not preserve member insertion order would silently change
  which entry a quantile maps to, and therefore change deterministic weather
  that nobody edited. JavaScript makes it worse by reordering integer-like keys
  during enumeration. An array is ordered by construction, so the guarantee
  survives any conforming JSON implementation.
- **Entry names must be unique within each axis of each season.** Uniqueness
  came free when tables were name-keyed objects and does not survive the move
  to arrays. Two entries sharing a name are individually unidentifiable
  everywhere downstream — `requires_temp` cannot say which temperature row it
  means, the override popover and the HUD show one label for two rows, and the
  override store records a name rather than an index — so a duplicate is not a
  quirk but a row the user can neither select nor remove.
- **Weights must be finite and non-negative, and every axis of every season
  must carry at least one strictly positive weight, and each axis's total must
  itself be finite.** Validated at load and at save. The total matters
  separately from the individual values: two entries of `1e308` are each finite
  and positive while their sum overflows to infinity, after which every
  normalized probability is zero and the close-at-1.0 rule hands back the last
  entry — a silent, wrong, and perfectly deterministic answer. Normalization
  additionally scales by the largest weight before summing, so a table that
  validates cannot overflow on the way to a distribution. Zeroing an entry is the documented way to disable it, which makes
  zeroing *all* of them an easy accident — and a table with zero total weight
  has no inverse CDF at all, so the resolver has nothing to fall back to. The
  unconstrained-condition fallback (§ Drawing a block) does not help here: it
  covers a condition table emptied by `requires_temp`, not a temperature or
  wind table with no drawable entries.
- **Entry order is significant**, and tables are read in **array order** when
  building the cumulative distribution. Order carries no meaning to
  the draw itself — the weights alone decide frequencies — but it decides what
  a quantile maps to, and therefore how weather transitions across a season
  boundary (§ Drawing a block). Author each table **monotonically along its
  natural axis**: calm → stormy for conditions, cold → hot for temperature,
  still → gale for wind. The shipped presets do. A shuffled table is legal and
  draws correctly; it just makes season transitions arbitrary instead of
  continuous.
- **Every season must declare at least one condition with no `requires_temp`**,
  validated at load. This is what makes the degenerate-table fallback safe
  (§ Drawing a block) without letting it emit a constrained condition outside
  its band.
- `persistence` is the **lag-1 autocorrelation between adjacent blocks**, in
  `[0, 1)`. `0` makes every block independent; `0.35` means a block is 35%
  correlated with the one before it; higher values give longer runs. Absent
  means 0.5. Values are clamped to `0.998` (§ The construction, concretely),
  so `1` is accepted but resolves to the clamp with a warning.

  This is a real unit, not a dial — the same number produces the same run
  lengths in any conforming implementation, and it is measurable directly from
  a generated series.

  **It never means "the weather never changes."** Even at the clamp the season
  table underneath it changes, so a sample held across a season boundary maps
  through a different inverse CDF and yields different weather. High
  persistence buys long runs *within* a season, nothing more. Genuinely
  unchanging weather is a **single-entry table** — exact, visible in the
  climate document, and unrelated to this setting.

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
{ "default_climate": "temperate-coastal" }
```

Both fields are optional. When the file is absent, `default_climate` is unset,
**or the climate it names cannot be loaded**, the fallback is the shipped
`temperate-interior` preset — a named, committed default rather than an implicit
one, so an untagged world and a missing file resolve identically. The
unloadable case matters as much as the missing one: a configured default can
name a malformed climate, or a custom-only climate the user has since deleted,
and without this rule an untagged location has nowhere left to fall — and a
location naming that same dead id would bounce to the campaign default and back.
The shipped preset terminates the chain unconditionally.

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
- **`to` may be `null`, meaning open-ended** — the span matches every `t >= from`
  with no upper bound, and `to_fixed` is `null` alongside it. This is the
  storage for the HUD's *"until I clear it"* duration (§ Interface), which
  otherwise has no representation and would force implementers to invent a
  sentinel far-future date. Clearing such an override **deletes the record**
  rather than setting `to` to the current moment: a deleted span leaves no
  trace in the resolution chain, while a closed one would keep shadowing lower-
  precedence overrides for its historical range, so re-reading an old scene
  would show weather the GM had already retracted.
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
  change. Writers compute both.
- **`id` is required**, and it is what makes the precedence backstop
  implementable — the rules below need a stable per-span identity that array
  position cannot supply. Writers generate one; a hand-authored entry missing an
  `id` gets a canonical one derived by hashing the **location key together with
  every field of the record except `id` itself** — bounds, source, axes, note
  and `set_at` alike — so the backstop still holds for files edited outside the
  app.

  Hashing a chosen subset does not work, and two subsets were tried before this
  one. Omitting the location key makes the same storm pinned at the docks and
  at the lighthouse derive one id. Omitting `set_at` and `note` makes two
  hand-authored records that differ only in when they were written — which
  precedence explicitly contemplates, since recency is a tiebreak — derive one
  id, so the delete contract cannot address either of them. Hashing everything
  is the only rule with no such gap. Two records identical in *every* field are
  genuinely indistinguishable, and are deduplicated on load rather than kept as
  a pair nothing can tell apart.
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

A moment is resolved to a block by its **wall-clock minute on a continuous
axis** (`fixed_day * 1440 + minutes`), never by a `(date, block name)` pair, so
`night` is one contiguous interval and 22:00 does not land in a different block
from 02:00 the following morning. Concretely: the 00:00–03:59 stretch belongs to
the *preceding* date's `night`. Deriving the block from the date alone would let
the sky reroll halfway through a single night — the one span most likely to hold
a continuous scene.

**That minute coordinate is not the noise index.** The two are separate and
conflating them breaks the whole construction: the noise field defines
`persistence` as the correlation between indices differing by **1**, while
adjacent blocks differ by 240–480 minutes. Feeding minute coordinates to the
filter would put neighbouring blocks outside each other's windows entirely — at
`persistence: 0.9` the windows are 38 wide and 240 apart — making every block
independent, the exact opposite of the setting's meaning, and silently: the
distributions would all still look right.

The noise index is therefore a **consecutive block ordinal**:

```
ordinal = 5 * fixed_day + position       position: dawn 0, morning 1,
                                                   afternoon 2, evening 3,
                                                   night 4
```

with `night` belonging to the date it *starts* on, so 02:00 on day *D+1*
resolves to `5*D + 4` — the same ordinal as 22:00 on day *D*, one less than
dawn of *D+1*. Consecutive blocks differ by exactly 1 across day and year
boundaries alike, which is what makes `persistence` mean what it says.

**The season is looked up from the block's owning date too**, not from the
queried moment. A night block spans midnight and can therefore span a season
or year boundary: 22:00 on *D* and 02:00 on *D+1* share an ordinal and so share
a sample, but looking the season up per-moment would map that one sample
through *D*'s table for the first query and *D+1*'s for the second — changing
the weather halfway through a single night, which is exactly what indexing the
block contiguously was meant to prevent. Everything about a block derives from
the date it starts on.

### The noise field

Weather is **not** a Markov chain over blocks. Each axis is a **correlated noise
field** over the continuous block index, sampled directly at whatever index is
asked for, with correlation length derived from `persistence`. Runs of weather
emerge from the field's smoothness rather than from carrying a previous value
forward, and any block resolves in O(1) regardless of how old the campaign is.

**Why not a chain**, since a chain is the obvious reading of "today depends on
yesterday" and two drafts of this spec tried it. A chain has to start somewhere,
and every choice of anchor is either unbounded or discontinuous. Anchoring per
year and discarding a burn-in — the first draft — fixes the *marginal*
distribution at the anchor but cannot correlate it with the final block of the
previous year, so at `persistence: 1` the sky is constant all year and still
free to jump every New Year. Anchoring at a campaign epoch fixes that, but then
needs a persisted immutable epoch (there is no in-game campaign start date to
derive one from — `create_campaign` records only real-world stamps), a cache key
carrying the climate content hash so a long-running process doesn't serve a
stale walk, a second backward chain for prequel scenes set before the epoch, and
explicit remapping of held values at season boundaries. A field needs none of
those: it is defined at every index, forward and backward, with nothing cached
and nothing to invalidate.

#### The construction, concretely

"Correlated noise" is not a specification — many processes satisfy it while
producing wildly different run lengths for the same `persistence`, which would
make presets non-portable and `0.35` meaningless. The process is therefore
pinned down exactly:

1. **Latent field.** For each `(cid, zone, axis)` there is an i.i.d. standard
   normal `z(i)` at every integer block ordinal. This is the only source of
   randomness, it is defined at negative ordinals, and it does **not** depend
   on `persistence`.

   "A stable hash" is not good enough here, and saying it was is inconsistent
   with rejecting two extra filter taps two paragraphs down — if the digest,
   the serialization, or the normal transform is left open, two conforming
   implementations produce different weather and the whole determinism
   argument collapses. So, exactly:

   - **Key**: the UTF-8 string `cid \x1f zone \x1f axis \x1f i`, with `i` in
     decimal and `-` for negatives. Unit separators, so no id containing a
     dash or colon can collide with another key.
   - **Digest**: BLAKE2b-256 of that key.
   - **Uniform**: the leading 53 bits of the digest, big-endian, divided by
     2⁵³ — giving `u ∈ [0, 1)` with exactly float64's mantissa precision.
   - **Normal**: `z = Φ⁻¹(u)` via **`statistics.NormalDist().inv_cdf`** from
     the Python standard library, with `u = 0` mapped to `2⁻⁵³` first so the
     tail is finite. That function *is* Wichura's AS241 (`statistics.py:1092`
     cites it), so naming the stdlib and naming the algorithm agree — and it
     is pure Python, which keeps it inside the Android base-dependency
     constraint in `CLAUDE.md`. Not `scipy.special.ndtri`, which is the Cephes
     implementation and disagrees in the last bits.

   - **Forward**: `Φ(g) = 0.5 · erfc(−g / √2)`, with **`erfc` vendored** into
     the codebase as a pure-Python rational approximation, not taken from
     `math.erfc`.

   The forward transform has to be vendored, and the earlier argument for
   leaving it to the stdlib was wrong. `math.erf`/`math.erfc` defer to platform
   libm on most builds, and two environments during review returned
   `0.04808979266518426` and `0.04808979266518429` for the same input — a real
   divergence, not a hypothetical one. The reasoning that followed ("an ulp
   cannot cross a bucket boundary") holds only for the worked example's table,
   whose boundaries sit at 0.111, 0.556 and 0.889. A private climate may
   legally place a cumulative boundary *between* those two values, and then the
   same campaign and block draws different weather on different builds.
   Quantizing the quantile would shrink that window rather than close it. Only
   a deterministic `erfc` closes it.

   `Φ⁻¹` needs no vendoring — `statistics` is pure Python — so only one
   function is added, and it stays inside the Android base-dependency
   constraint.

   What enforces that scoped guarantee is **reference vectors**, which the spec
   now carries rather than merely promising:

   | cid | zone | axis | i | u | z |
   | --- | --- | --- | --- | --- | --- |
   | `saltmarch-chronicle` | `saltmarch` | `temperature` | 0 | `0.45105387316006484` | `-0.12299918123991178` |
   | `saltmarch-chronicle` | `saltmarch` | `condition` | 0 | `0.7615608961018518` | `0.7113325140777527` |
   | `saltmarch-chronicle` | `saltmarch` | `wind` | 0 | `0.17774645354109264` | `-0.9239873403260146` |
   | `saltmarch-chronicle` | `saltmarch` | `condition` | 1 | `0.9654995835326088` | `1.8184143093163614` |
   | `saltmarch-chronicle` | `saltmarch` | `condition` | −1 | `0.9510130394641974` | `1.6547564014712368` |
   | `saltmarch-chronicle` | `highreach` | `condition` | 0 | `0.21315957935313046` | `-0.7955061091380776` |

   And end-to-end, through the filter and the wind table of the worked example
   above (`calm 1, breeze 4, strong 3, gale 1`) at ordinal 0:

   | persistence | W | taps | g(0) | Φ(g) ≈ | drawn |
   | --- | --- | --- | --- | --- | --- |
   | 0.0 | 0 | 1 | `-0.9239873403260146` | 0.17774645354109 | breeze |
   | 0.5 | 6 | 7 | `-1.6636640450077727` | 0.04808979266518 | calm |
   | 0.9 | 38 | 39 | `-0.6397550785578683` | 0.26116592065069 | breeze |

   `u`, `z` and `g` above are exact and final — BLAKE2b and AS241 are pure
   Python, and `g` follows once the accumulation order is pinned.

   **The `Φ(g)` column is provisional.** Those values were computed with
   `statistics.NormalDist().cdf`, which is the implementation this spec has
   just rejected. They are shown to three fewer digits than they were computed
   to, precisely because their last digits are the ones under dispute. Once the
   vendored `erfc` lands, `Φ(g)` must be **recomputed against it and re-pinned
   here to full precision**, at which point all four columns are asserted
   exactly. Carrying provisional numbers as though they were normative is how
   a reference-vector test ends up enshrining the bug it exists to catch, so
   the provisional status is part of the spec rather than a footnote.

   The drawn entries are stable regardless — this table's boundaries sit at
   0.111, 0.556 and 0.889, nowhere near the disputed digits — so they can be
   asserted now.
2. **Smoothing.** The correlated field is a normalized **one-sided** (causal)
   exponential filter over the latent, with `a = persistence`, indexed by the
   **block ordinal** of § Blocks — never by the minute coordinate:

   `g(t) = Σ_{k=0}^{W} a^k · z(t − k) / sqrt( Σ_{k=0}^{W} a^{2k} )`

   **Accumulate ascending in `k`, from 0 to `W`, term by term.** Floating-point
   addition is not associative, and summing the same terms in descending order
   gives a result one ulp away — `-1.6636640450077729` against
   `-1.6636640450077727` for the worked example below. One ulp cannot change a
   drawn entry unless the quantile sits on a bucket boundary, but the reference
   vectors are asserted exactly, so the order is part of the specification
   rather than an implementation detail.

   Normalization is by the **finite** sum, not by `sqrt(1 − a²)`. The infinite
   form gives `Var(g) = 1 − a^{2(W+1)}` once truncated, so `Φ(g)` would not be
   exactly uniform and the weight-fidelity guarantee — the whole reason for the
   copula — would hold only approximately. Dividing by the actual sum of the
   weights used makes the variance exactly 1 for any `W`.

   The filter is one-sided on purpose. For two-sided weights `a^{|k|}` the
   lag-1 autocorrelation works out to `2a / (1 + a²)`, not `a` — so `a = 0.35`
   would actually give 0.624 and `a = 0.9` would give 0.994, and every preset
   would be far more persistent than its number claimed. For the one-sided
   filter the lag-1 autocorrelation is `a` for the untruncated sum. Weather
   remembering its past but not its future is also the more defensible model.
3. **Parameter mapping.** `a = persistence` directly, making **`persistence`
   the lag-1 autocorrelation** between adjacent blocks — measurable, portable,
   and meaningful to an author: `0.35` means "a block is 35% correlated with the
   one before it." No log/exp round trip, so `p = 0` needs no special case: the
   filter collapses to `g(t) = z(t)`, independent blocks, exactly as documented.

   Truncation perturbs this slightly — the finite filter's lag-1 correlation is
   `a · (1 − a^{2W}) / (1 − a^{2(W+1)})`. With `W` as specified below the
   relative error is under `10⁻³`, so the calibration test asserts within a
   tolerance covering both that bias and sampling error rather than exact
   equality. Stating the exact finite form matters because an implementer who
   measures `0.8997` against a documented `0.9` needs to know that is correct
   and expected, not a bug to chase.
4. **Truncation.** `W` is the **maximum lag**, `W = ceil(4 / ln(1/a))` for
   `a > 0`, else 0. Since the sum runs `k = 0..W` inclusive, the number of taps
   evaluated is **`W + 1`**: 1 tap at `p = 0` (just `z(t)`), 39 at `p = 0.9`
   (`W = 38`), 399 at `p = 0.99` (`W = 398`). Cost is O(W) per sample, still
   O(1) in campaign age, which is the property this construction exists for.
   The exact counts matter — an implementation that evaluated a different
   number of taps would normalize differently and could land in a different
   inverse-CDF bucket, which is a determinism break rather than a rounding
   preference.
5. **Upper bound.** `persistence` is clamped to `0.998` (`W = 1998`, 1999 taps,
   a correlation length around a hundred days). A document may write `1`; it
   resolves to the clamp with a validation warning rather than an error.
   **There is no `persistence: 1` special case**, because an infinite-
   correlation process is not implementable and faking it with a constant
   sample would silently decouple that location from the rest of its zone —
   the one guarantee § Weather zones makes. Genuinely unchanging weather is
   expressed by a **single-entry table**, which is exact, obvious in the
   climate document, and needs no support from the noise field at all.

**Persistence is a filter over a shared latent, not a different field.** That is
load-bearing for § Weather zones: two locations sharing a zone but disagreeing
on `persistence` are two *smoothings of the same `z`*, so their weather stays
genuinely correlated rather than merely equally-distributed. A construction that
rescaled coordinates or redrew innovations as `L` changed would silently break
the correlated-weather guarantee while passing every distribution test — which
is why the latent is defined before, and independently of, `persistence`.

**The marginal must be uniform before it reaches the table**, and this is a real
trap rather than a detail. Inverse-CDF sampling reproduces the declared weights
only when fed uniform quantiles, but conventional smooth value noise is not
uniform — interpolation concentrates samples toward the middle of the range, so
mapping it directly through the table would systematically under-draw the rare
entries at the tails and over-draw the common middle ones, quietly violating the
weights the whole data model is built on. The process is therefore **correlated
Gaussian noise pushed through the standard normal CDF Φ** to obtain uniform
marginals, and only then through the table's inverse CDF — a Gaussian copula.
Uniform marginals are the *reason* for this shape, not a licence to substitute
another construction that also has them: the exact process above is normative,
because two implementations that both produce uniform marginals still produce
different weather, and the reference vectors exist precisely to forbid that.

Three properties follow structurally rather than by maintenance:

- **No seam at any boundary**, year or season. The latent is indexed globally
  and the filter looks back across whatever boundary happens to fall inside its
  window, so nothing distinguishes 31 Twelfthmonth from any other block.
- **Nothing to invalidate.** Determinism is a property of the construction, not
  something a cache key has to preserve.
- **Condition revalidation is automatic.** Condition is always
  `inverse_cdf(table filtered by temperature(t), noise(t))`, so when temperature
  moves the filtered table moves with it and an ineligible value cannot be
  carried. The revalidation rules in § Drawing a block describe a property here,
  not a step to implement.

It also buys something nobody asked for: at a season boundary the table changes
while the noise value stays continuous, so weather *morphs* into the new season
instead of stepping.

**The costs, stated plainly.** Runs are no longer geometrically distributed the
way a Markov chain's are, and `persistence` is a correlation length rather than
a carry-forward probability — a knob that needs calibrating against play, not a
knob that disappears. And accumulators get more expensive: with no walk to fold
over, snow depth cannot be accumulated across true history without replaying
every prior block. § Accumulators specifies a bounded window instead, which
covers snowpack and ground saturation but cannot express multi-year drought.
That was the one real argument for the chain, and it was judged worth paying:
long-horizon climate facts are better authored as lore than stumbled into by a
simulation.

### Drawing a block

Given the block index *t* and the season it falls in (from the year fraction),
each axis resolves independently of the blocks around it — the correlation
lives in the noise field, not in a carry-forward step:

`inverse_cdf` walks the table in array order accumulating normalized weights,
and selects the **first entry whose running cumulative exceeds `u`** — buckets
are half-open, `cum_{i−1} <= u < cum_i`, with the first starting at 0 and the
last treated as closing at 1.0 so floating-point drift in the final sum cannot
fall through. A zero-weight entry therefore spans an empty interval and can
never be selected, which is what makes zeroing an entry a clean disable rather
than a near-zero chance. The convention has to be stated because `u` is
discrete and an author can declare weights whose boundary lands exactly on an
emitted quantile; `<=` versus `<` then picks different weather from the same
seed.

1. **Temperature** = `inverse_cdf(season.temperature, Φ(noise_temp(t)))`.
2. **Condition** = `inverse_cdf(season.conditions filtered by the resolved
   temperature, Φ(noise_cond(t)))`. A condition entry's `requires_temp` drops
   its weight to zero when the resolved band isn't listed — which is what stops
   the tables from producing snow at high summer temperatures. Temperature
   resolves first precisely so this filter has something to apply.
3. **Wind** = `inverse_cdf(season.wind, Φ(noise_wind(t)))`, independent of both.

Two properties this construction gives for free, both of which a carry-forward
implementation has to work for and which earlier drafts of this spec got wrong:

- **A held condition can never contradict its temperature.** In a chain, a
  block that carries `snow` forward while its temperature rerolls from
  `freezing` to `mild` emits exactly the combination `requires_temp` exists to
  prevent, so carrying has to be made conditional and the held value redrawn.
  Here the condition is always read through the table *as filtered by the
  temperature at that same index*, so an ineligible value is not representable.
- **A held value can never outlive its season.** Same defect one boundary out:
  a chain at `persistence: 1` carries a winter `freezing` into a summer table
  offering only `hot`, holding a value the active season does not contain. Here
  the sample is mapped through whichever table is active at *t*, so crossing a
  season boundary remaps automatically. What is preserved across the boundary is
  the **quantile**, not a "nearest" entry — categorical tables have no distance
  metric, so nearness is undefined unless the ordering is. Since inverse-CDF
  sampling walks entries in **array order** (§ Climate documents),
  a sample sitting at quantile 0.8 of winter's conditions lands at quantile 0.8
  of summer's, and an author who orders both tables monotonically gets
  continuity for free — a stormy winter reading becomes the stormy end of
  summer, not a coin flip. This is what makes the high-persistence guarantee
  (§ Climate documents) true by construction rather than by enforcement.
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

**Seeding**: a stable hash of `(cid, zone, axis)` parameterizes each noise
field, which is then sampled at the continuous block index. There is **no
per-year or per-season component in the seed**, and adding one would be a
regression, not a refinement — it would decorrelate the samples either side of
the boundary and reintroduce exactly the seam this construction exists to
remove. The three axes take distinct seeds so temperature, condition and wind
do not move in lockstep.

The campaign id is in the seed per #44 — two campaigns in one world
must not share skies.

### Accumulators (later, not v1)

Snowpack, ground saturation, and drought are folds over the block sequence: snow
depth adds on snowfall blocks and subtracts on above-freezing ones. No new
storage, still deterministic. This is what #104's "the snow at the pass has
melted" actually needs — but weather ships first.

**Accumulation is a bounded fold**, not true history. Since resolution is random
access, there is no walk to piggyback on: accumulating from the campaign's
beginning would mean replaying every prior block, reintroducing the unbounded
work this design exists to avoid.

A fixed window alone does **not** solve this, and saying it did was wrong. Snow
that fell before the window is still on the ground during a freeze longer than
*K*; starting each fold at zero loses it, so old snowfall would vanish abruptly
as the window slid, with no melt event to explain it. Truncation has to be
*justified* by the dynamics, not merely declared.

So every accumulator must satisfy two properties, and one that cannot is out of
scope by construction rather than by preference:

- **A bounded, non-negative decay factor.** Each block multiplies the standing
  value by a factor in `[0, d]` with `d < 1` before adding its own
  contribution. Both ends of that range are load-bearing: "at most `d`" alone
  is satisfied by a large negative multiplier, which amplifies old state
  instead of forgetting it and destroys the error bound below. Physically the
  constraint is real — snowpack loses mass to sublimation and compaction even
  below freezing, ground saturation drains even without sun — so it is a
  modelling constraint rather than a fudge.
- **A saturating cap.** The value is clamped at some maximum, so a long
  accumulation cannot make arbitrarily old blocks matter.

Together these bound the truncation error: a block *K* back can contribute at
most `dᴷ · cap`, so *K* is chosen per accumulator to put that under the
resolution the value is reported at. The fold is then O(K) random access with no
stored state, fully deterministic, and provably independent of anything older —
which is what the fixed window only asserted.

**Multi-year drought fails the first property** — it is precisely an absence
that compounds without decay — and so is not expressible here. That is the
honest cost of random access. Long-horizon climate facts are authored as lore
instead, which is where a GM would want them anyway: a drought the simulation
wandered into is not a drought anyone can plan a story around.

The exact dynamics per accumulator (melt rates, caps, what counts as a snowfall
block) belong to the accumulator work itself, which is deferred. What this spec
fixes is the *shape* any accumulator must have to be implementable at all.

The resolver should expose the block sequence it samples so accumulators consume
it rather than reimplement the axis logic.

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

## Interface

Three surfaces: authoring a climate, assigning one to a location, and reading
and overriding the weather during play. The absorb review panel is a fourth but
needs no new UI — extractor proposals ride the existing checklist in
`CampaignView.tsx`.

### Climate editor (#40)

A record-list page, so it follows the mandatory two-pane pattern in `CLAUDE.md`
— `.editor` wrapping `.editor-list` (a `+ New climate` button plus one `.row`
per climate) and `.editor-body` with `mode: "view" | "edit"`, read-only by
default, an explicit **Edit** step. The rail tags each entry `builtin` or
`custom`; editing a builtin silently creates the custom copy described in
§ Integration, and the rail re-tags it.

**One deviation from the pattern, deliberately.** The canonical `.detail-view`
renders `.detail-rendered` as the record's markdown body. A climate has no
prose body — it is structured data — so `.detail-main` instead renders a
generated read-only summary: each season as a heading with its span in *dates*,
followed by its three tables as normalized percentages. `.detail-sidebar` keeps
the pattern exactly: **Edit** in `.form-actions`, then `.side-section` blocks
for persistence, season count, and a `chip` per location using this climate,
clickable through to that location.

The sidebar also carries a **Delete** action for custom climates, labelled
**Revert to preset** when the climate shadows a builtin — without it the
`DELETE` route and the revert behaviour it enables are unreachable from the
product, and a preset edited by accident could never be undone. Builtins with
no custom copy show no such action, since there is nothing to remove. Deleting
a climate that locations still reference warns and names them, using the same
chips the sidebar already lists.

**Ids.** `+ New climate` generates the registry id by slugifying the name and
uniquifying it against both tiers — the `slugify`/`uniquify` pair
`campaigns.create_campaign` already uses — and shows it, since the id is what
locations and `climate.json` will reference. It is **immutable after
creation**: renaming a climate changes its display name only. `PUT` rejects a
body whose `id` disagrees with the path, so a document can never be stored
under one registry key while advertising another.

The form is where the real work is:

- **Weights are shown as percentages, always.** A weight of 4 renders as
  `4 → 40%`, recomputed live as siblings change. Relative weights are the right
  storage model and a terrible authoring model; nobody can read a column of
  integers as a distribution. The percentage is display-only — the stored value
  stays the integer.
- **Season boundaries are edited as dates, stored as fractions**, since `0.45`
  is not authorable by a human (§ Climate documents). The conversion needs a
  fixed reference or it is not stable: 1 March is a different fraction in a
  Gregorian leap year than in a common one, and custom calendars vary more.
  Climates are also campaign-agnostic while calendars are campaign-scoped, and
  a campaign has no single "current" date — its scenes each carry their own.

  So: the display provider is the calendar of the campaign the editor was
  opened from, falling back to `gregorian` when there is none, and the
  reference year is that provider's `RULE_REFERENCE_YEAR` — already the
  convention for calendar-relative rules in `calendars/base.py`. Dates round
  to the day.

  **Crucially, the stored fraction is only recomputed for a boundary the user
  actually edited.** A boundary the user did not touch is written back as the
  number that was read, never re-derived from the date the form displayed —
  otherwise opening a climate under a leap-year calendar and saving an
  unrelated field would silently walk its seasons.

  The invariant is **exact numeric equality**, not byte identity: the `PUT`
  carries the whole document, and a stored `0.4500` or `4.5e-1` parses to a
  float and serializes back as `0.45` regardless of intent. Byte identity would
  be untestable through a JSON round trip and would fail on formatting that
  changes nothing. What must hold is that the value is the same number.
- **Entry order is drag-reorderable and its meaning is stated inline.** Order
  decides what a quantile maps to across a season boundary (§ Drawing a block),
  so the form says so — otherwise a well-meaning alphabetical sort silently
  makes every season transition arbitrary.
- **`requires_temp`** is a multi-select on each condition row, offering exactly
  the temperature bands declared in that season.
- **Validation is inline and blocking on save**, covering the invariants the
  resolver depends on: seasons cover the year without gaps; **every individual
  weight is finite and non-negative**; every axis of every season has at least
  one positive weight; every season declares at least one unconstrained
  condition; every positive-weight temperature band has at least one eligible
  condition; **entry names are unique within each axis**; **each axis's weight
  total is finite**; **every `requires_temp` names a temperature entry that
  exists in the same season *and* every positive-weight constrained condition
  names at least one temperature entry whose own weight is positive**; and
  **the climate's own `persistence` is finite
  and within `[0, 1]`** — the same range rule the location field gets, which
  the editor would otherwise leave to a backend failure. An empty
  `requires_temp` array is rejected rather than read as "unconstrained", since
  the two are visually identical and mean opposite things. Each failure names
  the season and the fix.

  The positive-weight half of the `requires_temp` rule is a second silent-zero
  case, one step past the dangling-name one: `freezing: 0, mild: 1` with `snow`
  requiring `freezing` passes every other check — the name resolves, the axis
  has positive weight, and an unconstrained `clear` keeps the band eligible —
  while `snow` is filtered out of every draw it was ever weighted for. Both
  rules exist to catch the same shape of mistake, a declared weight that can
  never be drawn.

  The dangling-`requires_temp` check earns its place: `requires_temp:
  ["freezng"]` on a snow entry passes every other rule — the band `freezing`
  exists and is made eligible by some other unconstrained condition — while
  snow itself is filtered out of every draw it was ever weighted for. A
  declared weight that can never be drawn is exactly the kind of silent
  nothing this validation exists to catch. Renaming a temperature entry in the
  editor updates the conditions that reference it, rather than orphaning them.

  The per-weight check is separate from the per-axis one on purpose:
  `{clear: 1, storm: −1}` satisfies "this axis has a positive weight" while
  still violating the data model, and would otherwise reach the author as a
  backend 400 instead of the promised inline error — or be accepted outright
  if the server check ever drifts. These are the conditions that would
  otherwise surface at runtime as a fallback sky nobody asked for, or for a
  zero-total table as no sky at all.

### Campaign default climate

`campaigns/<cid>/climate.json` needs a surface too, or the only way to set the
default every untagged location falls back to is hand-editing the store or
tagging every location one at a time.

`GET`/`PUT /api/campaigns/{cid}/climate`, rendered as a single select in the
campaign's settings alongside the calendar config it sits beside on disk
(`CalendarConfig.tsx` is the model, and #73's per-campaign settings tabs are
where it belongs long-term). This route needs declaring before the generic
`/{kind}` entity routes.

**A world-level default exists too**, in `worlds/<wid>/climate.json` with the
same one-field shape, copied into the campaign at create time exactly as
`calendars.copy_calendar` copies the calendar. Without it the campaign wizard
has nothing to prefill from — a world author would have to set the default
again for every campaign, or tag every location individually.

**`create_campaign` takes a `climate` argument**, alongside the `calendar` one
it already has, and the wizard passes the user's selection through it. Copying
the world file unconditionally would make the wizard's control a lie: it would
render an editable prefilled choice and then discard whatever the user chose.
The world file is the default *for* that argument, not a substitute for it —
same shape as `calendar`, which the wizard also prefills and the caller can
also override.

This inherits a gap rather than inventing one: #40 records that the *calendar*
is world-scoped on disk with no world-side read/write route and no UI, settable
only by hand-editing before the first campaign exists. Climate lands in exactly
the same position, so `GET`/`PUT /worlds/{wid}/climate` and its control belong
in the same world-settings tab that issue proposes for the calendar — and if
that tab is not built, the world default is dead weight and the wizard prefill
should be dropped with it rather than reading a file nothing can write.

The world `GET` needs declaring **before the generic world entity routes**, for
the same reason its campaign twin does: `GET /worlds/{wid}/{kind}` is already
registered at `routes.py:1965`, so a later-registered `/worlds/{wid}/climate`
resolves as an entity-list request for kind `climate` and the settings control
silently fails to load.

### Assigning a climate to a location

`climate`, `persistence` and `weather_zone` become location fields, which means
entries in **both** `entity_schema.FIELDS` and `ENTITY_FIELDS` in
`frontend/src/api/client.ts` — the former has no `locations` key at all today,
so `routes._check_fields` currently 400s on all three.

`climate` wants to be a picker over the climate registry, but entity fields are
text-only (`entity_schema.py`: ref-valued widgets are deferred to #221/#222).
Rather than block on that, it ships as **a text field validated against the
climate list on save**, rejecting an unknown id with the available ids in the
message.

The validation is the point, not a consolation. Weather resolution is
deliberately lenient — an unknown climate falls back to the campaign default
rather than raising, because raising would kill a turn. But that leniency makes
a typo *invisible*: `temperate-costal` produces plausible weather from the wrong
climate and reports nothing. Leniency is right in the turn loop and wrong at the
authoring surface, where the user is present and can simply be told. When the
ref-valued widget lands, the field becomes a picker and the validation stays.

**The check belongs in the backend save path, not only in the form.**
`entity_schema.invalid_keys` and `routes._check_fields` validate field *names*
and nothing else, so a location saved through the API — a script, a sync, a
future importer — would persist an unknown climate however careful the UI is,
which is exactly the invisible typo this section exists to prevent. Entity
fields therefore need a per-field **value** validator alongside the descriptor,
with `climate` supplying one that checks the registry, `persistence` one that
checks finite-and-in-range, and the frontend calling the same rule for its
inline error. This is a small extension to `entity_schema`, and the first field
in the codebase to need it.

### Scene HUD widget (#195)

**Reading.** One line in the existing always-visible inspector, beside the
"When" and "Location" sections it depends on: *"Overcast and cold, wind
rising."* Each axis carries its provenance from `current_weather` — an authored
axis is marked and shows its `note` on hover, so a GM can tell at a glance which
part of the sky they decided and which part the world did. When resolution
returns `None` (no location or no moment yet, § Resolution) the widget renders
nothing rather than a placeholder or an error, matching how the neighbouring
widgets degrade.

**Overriding.** Clicking the line opens a small popover — this is where weather
is changed, per the decision that it belongs where you are already looking when
you decide it should be raining:

- Three selects, one per axis, each offering the entries of the **active
  season's table** plus a *"leave to chance"* option. Per-axis, because
  narration usually constrains one axis and not the others (§ Override store).

  *"Leave to chance"* **clears that axis**, rather than merely omitting it from
  the record being written. On save, every covering span that sets the axis has
  it removed, and a span left setting nothing is deleted. Omitting would be the
  simpler implementation and the wrong behaviour: a user who opens the popover
  on an overridden axis and selects *leave to chance* means "stop overriding
  this," and would otherwise watch the setting appear to do nothing.
- A duration control: *this block* (the default), *this and the next N blocks*,
  or *until I clear it*. These map onto the span the store requires; the
  default matches the extractor's one-block default so the two paths behave
  alike.
- A free-text `note`, which is what the prompt actually gets to work with —
  "the Wintertide storm" tells the model more than `condition: storm` does.
- **Clear override**, shown only when one is active on this location and
  moment, which deletes rather than writing a counter-override, so the store
  doesn't accumulate cancelling spans.

Deleting needs a contract the `PUT` cannot provide — its body carries axes and
a duration, not an identity. So `current_weather` returns, alongside each
axis's `source`, **the full stack of spans covering this moment** for that
axis, winner first, each with its `id` **and its storage key**. And there is a
`DELETE /api/campaigns/{cid}/weather/{storage_key}/{span_id}` (before the
catch-all, like the others), keyed by both, matching how canonical ids are
derived.

The storage key is not the scene's location and cannot be inferred from it: a
covering span may live under `_default`, and its id is a hash rather than
something the key can be recovered from. Returning the key with each span is
what makes the delete callable at all.

**Clear override deletes the whole covering stack, not just the winners.**
Returning only the winning ids would be a trap: precedence explicitly permits
an older manual span or an extractor span to sit shadowed beneath the winner,
so deleting the winner alone would *promote* the shadowed record — the sky
would change rather than return to procedural, and the button would look
broken in the one case it most needs to work. That is also why the resolver
returns the stack rather than a single provenance: the UI cannot clear what it
cannot see.

**Clear override** is therefore the three-axis case of *"leave to chance"* — it
clears all three at once rather than being a separate mechanism, and both route
through the same operation:

```
POST /api/campaigns/{cid}/weather/clear   { location, at, axes: [...] }
```

**Clearing is one atomic server-side call, not client-orchestrated edits.**
Removing a single axis from a span that sets several means *mutating* that
record while preserving its `source`, `note`, `set_at` and range — and the
client has only whole-record `DELETE` and a create-shaped `PUT` to work with,
so it would have to delete and recreate, losing exactly the fields precedence
depends on. Worse, it would have to do that across a stack of spans
non-atomically, leaving a half-cleared state if anything failed midway. The
server walks the covering stack, strips the named axes from each span, deletes
any span left setting nothing, and returns the new resolved weather.

`DELETE .../{storage_key}/{span_id}` stays, for removing a specific record
outright rather than clearing an axis of it.

Saving `PUT`s to the override endpoint with the scene's location, and the widget
re-reads. The next turn's prompt picks it up through the ordinary weather
section; nothing special-cases an override downstream.

## Testing

Backend, `backend/tests/test_weather.py`, store isolated with
`monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`:

- Determinism: same `(cid, zone, block)` yields the same weather across
  processes and across resolution order.
- Persistence: at `persistence: 0.9` runs are demonstrably longer than at
  `0.1`, over a sampled year.
- Year seam: the distribution of change-events across the year boundary matches
  the distribution mid-year, **and** the underlying sample `g(t)` is continuous
  across 31 Twelfthmonth → 1 Firstmonth, with the same lag-1 correlation there
  as anywhere else. Assert on the sample rather than the rendered weather: a
  climate whose season boundary sits at fraction 0 is licensed to change the
  weather at New Year, so an assertion on the visible value would reject a
  conforming implementation. This is the assertion every chain-with-an-anchor
  design in this spec's history failed, and it is the reason for the noise
  field.
- Season seam: likewise across a season boundary, where the table changes but
  the underlying stream should not jump.
- Block identity: 23:00 and 01:00 the following morning resolve to the same
  block; 03:59 and 04:01 do not. Include a night that **spans a season
  boundary and a year boundary** — both moments must resolve to the same
  weather, which fails if the season is looked up from the queried moment
  rather than from the block's owning date.
- Reference vectors: the tuples tabulated in § The construction, concretely
  produce their documented `u`, `z`, `g` and drawn entry **exactly**. `Φ(g)` is
  asserted exactly too, **once its column has been recomputed against the
  vendored `erfc` and re-pinned** — it is provisional until then and must not
  be asserted against the values currently written, which came from the
  rejected stdlib implementation. Assert against the **values in the spec**,
  never against a fixture regenerated from the implementation: a self-generated
  fixture passes while preserving exactly the drift it exists to detect.
- Vendored `erfc`: agrees with a high-precision reference to within the
  documented tolerance across the range, and — the point of vendoring — returns
  identical bits on two machines with different libm.
- Accumulation order: summing the filter descending in `k` produces a different
  `g` from the documented one, so the test pins ascending order rather than
  merely the formula.
- Inverse-CDF boundaries: a quantile landing exactly on a cumulative boundary
  selects the following entry (`cum_{i−1} <= u < cum_i`), and a zero-weight
  entry is never selected at any `u`.
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
- Season-boundary remap, tested **through the production resolver under two
  climates**: resolve the same ordinal twice, once with a climate whose season
  table is A and once with an otherwise identical climate whose table is B,
  and assert first that the **latent `g(t)` is identical in both runs**, then
  that the selected entries sit at the same quantile of their respective
  tables.

  The latent assertion is the whole test. Hand-feeding one sample to two
  inverse CDFs proves nothing — the quantile is preserved by definition of the
  inverse CDF, so it would pass against an implementation that reseeds per
  season, which is the defect being hunted. Only sampling through the real
  resolver can show the table swap left the field alone. Comparing the actual
  blocks either side of a real boundary is also wrong: `g(t−1)` and `g(t)` are
  different samples with different quantiles even at the clamp.
- Season-boundary continuity, separately, asserted on **the latent field and
  not the rendered weather**: the distribution of `g(t) − g(t−1)` at boundary
  indices matches its distribution at non-boundary indices. Rendered weather
  cannot carry this test — two legal single-entry seasons (`freezing` then
  `hot`) change at every boundary and never mid-season, so a rendered-change
  assertion would reject a perfectly continuous field. The field is what the
  design guarantees; the tables are free to make that continuity visible or
  not.
- Persistence clamp: a climate declaring `persistence: 1` loads, warns, and
  resolves at the clamp — it neither raises nor produces a constant sky. A
  single-entry table, by contrast, does produce a constant value at any
  persistence.
- Zero persistence: `persistence: 0` yields `g(t) = z(t)` and independent
  blocks, with no division by zero anywhere in the filter.
- Persistence calibration: the measured lag-1 autocorrelation of the field is
  within tolerance of the configured `persistence`, across several values —
  the test that makes a preset's `0.35` mean the same thing in any
  implementation, which a "0.9 runs longer than 0.1" test cannot. Tolerance
  must cover the finite-filter bias as well as sampling error.
- Block ordinal: two chronologically adjacent blocks receive noise indices
  differing by exactly 1 — across a day boundary, a year boundary, and the
  cross-midnight night span. This is the test that catches the minute
  coordinate being passed to the filter, which would leave every block
  independent while every distribution still looked correct.
- Unit variance: the sampled field has variance 1 at several persistence
  values including the clamp, so `Φ(g)` is genuinely uniform and the
  weight-fidelity test is measuring the table rather than the filter.
- Cross-persistence zone coupling: two locations sharing a zone but configured
  with *different* `persistence` still show correlated weather, since both are
  smoothings of the same latent. This fails immediately if an implementation
  reseeds or rescales the latent per correlation length.
- Accumulator truncation: extending an accumulator's window well past its
  chosen *K* changes the reported value by less than its reporting resolution,
  including across a freeze longer than *K*.
- Random access: resolving block *t* directly and resolving it after walking
  every block from 0 to *t* yield the same answer, at any *t*, including
  negative — the property that makes prequel scenes and multi-year jumps free.
- Weight fidelity, against a climate **with no `requires_temp` anywhere**:
  over a long sample the observed frequency of each condition
  matches its normalized weight, at several persistence settings — the test
  that catches a non-uniform marginal reaching the inverse CDF. The
  unconstrained climate is required, not incidental: under `requires_temp` an
  entry is filtered out whenever its band is absent and the survivors are
  renormalized, so a conforming resolver's unconditional frequencies
  legitimately differ from the raw weights. Constrained tables are covered by
  comparing against the temperature-conditioned expectation instead.
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

Frontend, from `frontend/` with `npx vitest run` and `npx tsc -b`:

- Climate editor follows the list/detail contract, per the pattern's own test
  requirements: clicking a row shows the read-only view with no `textarea`,
  **Edit** reveals the form, `+ New climate` opens the form directly.
- Editing a shipped preset creates a custom copy and re-tags the rail entry,
  without a separate "duplicate" step by the user.
- Weight percentages recompute live as sibling weights change, and reordering
  entries does not alter them.
- Save is blocked, with a message naming the season, when a season leaves a
  year gap, declares no unconstrained condition, has a temperature band with
  no eligible condition, or has an axis whose weights are all zero.
- Round-tripping a climate through the editor — open, save, no edits — leaves
  every season fraction numerically equal, including under a leap-year
  calendar, and including when an unrelated field on the climate was changed.
- A `requires_temp` naming a temperature entry that does not exist in that
  season is rejected inline; renaming a temperature entry updates the
  conditions referencing it.
- *"Leave to chance"* on an axis that currently has an override returns that
  axis to procedural weather, rather than leaving the existing override in
  place — and when that override also sets other axes, those survive with
  their `source`, `note` and `set_at` intact rather than being recreated.
- The campaign wizard's selected climate is what the created campaign has, not
  the world default it was prefilled from.
- A covering span stored under `_default` is clearable from the HUD, which
  requires its storage key to be present in the resolver's response.
- The campaign settings control reads and writes the default climate, and the
  campaign wizard prefills it from the world's default.
- An override saved as *"until I clear it"* stores `to: null` and matches
  arbitrarily far-future moments; clearing it removes the record rather than
  closing it, so a re-read of an earlier scene shows procedural weather.
- A location saved with an unknown climate id is rejected with the available
  ids, rather than accepted and silently falling back — asserted **through the
  API as well as the form**, since the API path is the one a script or importer
  takes.
- Individual weights: an axis containing a negative or non-finite weight is
  rejected inline even when a positive sibling is present.
- Duplicate entry names within one axis are rejected inline.
- A constrained condition whose `requires_temp` names only zero-weight
  temperature entries is rejected, as is an empty `requires_temp`.
- Deleting a custom climate that shadows a builtin restores the preset and the
  rail re-tags it; deleting one that locations reference warns and names them.
- A climate's id is generated from its name, stays fixed across a rename, and a
  `PUT` whose body id disagrees with the path is rejected.
- An axis of two `1e308` weights is rejected rather than silently resolving to
  its last entry, and a table of large-but-summable weights still produces its
  declared distribution.
- Climate `persistence` of `2`, `-1` or `NaN` is rejected inline by the editor
  rather than deferred to a backend save error.
- **Clear override** with a shadowed second span beneath the winner returns the
  axis to procedural weather, rather than promoting the shadowed override.
- Two identical spans authored under different locations receive different
  canonical ids, and deleting one leaves the other intact.
- Table order survives a save/load round-trip through the JSON store unchanged,
  including a table whose entry names are numeric-looking strings.
- The HUD widget renders nothing when weather resolution returns `None`, and
  marks authored axes distinctly from generated ones.
- The override popover writes only the axes the user set, leaves *"leave to
  chance"* axes procedural, and **Clear override** removes the span rather than
  writing an opposing one.

## Deferred

- **Spatial correlation** — storms sweeping across adjacent locations. Needs a
  location hierarchy. Hook left at § Weather zones.
- **Accumulators** — snowpack and ground saturation, as bounded windows over the
  sampled block sequence. Drought is out of scope by construction (§ Accumulators).
- **LLM-authored flavor** — #44's Option B, as a "reroll with AI" garnish on top
  of the procedural draw rather than a replacement for it.

## Open questions

- **What `persistence` values actually feel like at the table.** It is now a
  correlation length rather than a carry-forward probability, so the presets
  ship with values calibrated by eye and will need adjusting against play. The
  shipped climates are the place to tune this, not the algorithm.
- Do weather edits join `changes.json`? `absorb._BROWSABLE_KINDS` currently
  gates this and weather is arguably too noisy to browse (#46's own note).
*(The campaign-default inheritance rule was open here and is now settled as
copy-on-create in § Campaign default climate — matching `calendar.json`, which
is copied by `calendars.copy_calendar` and likewise does not propagate later
world edits. The two must not disagree: a planner cannot follow a settled
design and an open question that contradicts it, and the alternatives differ
observably once a world default changes after campaigns exist.)*
