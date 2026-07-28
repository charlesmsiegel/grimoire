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
- **Entry names must be non-empty strings, and unique within each axis of each
  season.** Non-empty is not pedantry: the name is the row's only identity and
  its only display value, so an empty condition is indistinguishable from the
  popover's blank state and an empty temperature name makes every
  `requires_temp` choice referencing it unreadable. Uniqueness
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
- `persistence` is the **lag-1 autocorrelation between adjacent blocks**. The
  **accepted** range is `[0, 1]` — a document declaring `1` is valid and loads;
  the **effective** range is `[0, 0.998]`, since values are clamped (§ The
  construction, concretely) and `1` resolves to the clamp with a warning. The
  two ranges are stated separately because an implementation that built its
  load validation from a single `[0, 1)` would reject a document the rest of
  this spec requires it to accept. `0` makes every block independent; `0.35`
  means a block is 35% correlated with the one before it; higher values give
  longer runs. Absent means 0.5.

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
weighted tables produce different weather. Since locations may independently set
both, the guarantee is stated precisely, and at the level it actually holds:

- Members agreeing on climate and persistence get **identical weather**.
- Members that disagree share a **correlated latent field** — the same `z`,
  differently filtered. Their *rendered* weather is correlated only to the
  extent their tables are comparably ordered.

The second clause has to be about the latent rather than the sky, because
nothing forces two climates' tables to be commensurable: one member may use a
single-entry table, whose output is constant and therefore uncorrelated with
anything, and two tables with reversed entry order map the same quantile to
opposite conditions. Promising correlated *weather* would be promising something
the data model permits authors to break.

Disagreement is a validation warning, not an error, because
correlated-but-not-identical is sometimes exactly right — a sheltered valley and
the exposed peak above it should share a front and not a temperature.

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
      "seq": 7,
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
  sentinel far-future date. Clearing an open-ended span **truncates it at the
  clear range**, exactly as clearing a bounded span does — there is no
  open-ended special case.

  An earlier draft had clearing delete such a record outright so that earlier
  scenes reverted to procedural. That was wrong twice over: it contradicted the
  range semantics of the clear operation, and it is the wrong fiction. "Storm
  until I say otherwise," set on day 10 and cleared on day 15, means it stormed
  for five days — re-reading day 12 should still show the storm. Retracting
  weather that scenes were played under is what `DELETE` is for, deliberately a
  separate and more emphatic action.
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

  **Explicit ids are checked for uniqueness per storage key on load**, and a
  collision is resolved by re-deriving both records' ids canonically. The
  canonical rule only covers records that *omit* an id, so two hand-authored
  records under one key can otherwise carry the same explicit `id` while
  differing in bounds or axes — occupying one `DELETE` address, and
  indistinguishable to the id tiebreak when their other precedence fields tie.

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
- An optional **`suppress`** list names axes this span forces back to
  procedural, used to shadow an inherited `_default` override at one location
  (§ Interface). It is a field of its own rather than a reserved value inside
  an axis, so it cannot be confused with an authored condition that happens to
  share its name.
- `source` distinguishes `manual` (#45) from `extractor` (#46), which sets the
  resolution order and lets the UI show provenance.

## Resolution

Per #44's instruction to design the chain up front. One entry point:

```
current_weather(croot, cid, location_id, native) -> dict | None
```

**It returns `None` when either input is missing**, and callers treat that as
"no weather section."

**A location id that no longer resolves is not a missing input**, and must not
raise. Deleting a location does not clean up the scene histories referencing
it, so `get_location_history` keeps returning a perfectly non-empty id for an
entity that is gone — the `None` guard never fires, and reading its `climate`
field would raise `EntityNotFound` in the middle of `context._assemble`.
`context.py:535-539` already wraps exactly this read in a `try/except` that
omits the setting block, so weather raising where the setting degrades would
make a deleted location break turn assembly for a scene that currently just
loses one section. An unresolvable location therefore resolves its climate and
persistence from the campaign default, and keeps the id as its weather zone —
the id is still a stable seed whether or not an entity stands behind it, so
the sky stays continuous rather than blanking. Both `scenes.get_location_history` and `get_time_history`
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
2. **Recency**: among equally specific spans, the newest wins — ordered by a
   **monotonic per-storage-key `seq`**, not by `set_at`. `set_at` is a
   wall-clock write timestamp, deliberately not an in-game moment, but
   `now_iso()` formats only to whole seconds (`paths.py:104`), so two spans
   written in the same second tie and fall through to the id backstop — which
   can hand the argument to the *earlier* instruction. A GM adjusting an
   override twice in quick succession is exactly when recency matters most.
   `seq` is a stored integer, incremented on every write to that storage key —
   the file's current maximum plus one — and it is what precedence compares.
   `set_at` is retained for display and provenance.

   **A record without `seq` reads as `seq: 0`**, which is what every existing
   file and every hand-authored record will have. That needs no migration pass
   and is not a special case in the resolver: ties at `seq` fall through to
   `set_at`, then to the id backstop, which is precisely the ordering this
   spec had before `seq` existed. Legacy records therefore keep their old
   behaviour among themselves and lose to anything written afterwards, which is
   the correct reading — a span written today *is* newer than one that predates
   the field.

   Splitting a span (§ Interface) gives both fragments the original's `seq`:
   they are one instruction cut in two, not two instructions. Since `set_datetime` permits moving a scene backward in
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
   - **Uniform**: take `n`, the leading **52** bits of the digest big-endian,
     and set `u = (2n + 1) / 2⁵³`. The numerator is an odd integer below `2⁵³`,
     so it is exactly representable and the division by a power of two is
     exact: the map is injective and lands strictly inside `(0, 1)` with no
     special case at either end.

     Two simpler forms fail, both silently. `u = n / 2⁵³` emits `u = 0`, which
     `Φ⁻¹` cannot transform, and remapping `0 → 2⁻⁵³` collides with what
     `n = 1` already produces — no mass at the bottom, double mass one step up.
     The obvious repair, midpoints over the full 53 bits (`(n + 0.5) / 2⁵³`),
     is worse than it looks: `n + 0.5` is not representable at that magnitude,
     so `n = 2⁵³ − 1` rounds to exactly `1.0` — outside AS241's domain — while
     `2⁵³ − 2` and `2⁵³ − 3` both round to `0.9999999999999998`. Dropping one
     bit is what makes the midpoint exact rather than approximately exact.
   - **Normal**: `z = Φ⁻¹(u)` via **`statistics.NormalDist().inv_cdf`**, which
     is Wichura's AS241 (`statistics.py:1092` cites it).

   - **Forward**: `Φ(g)` via **`statistics.NormalDist().cdf`**.

   Both come from `statistics`, which is where the installation-scoped
   determinism decision lands (§ Determinism scope). Worth knowing what is
   given up: `NormalDist.cdf` goes through `math.erf`, which defers to platform
   libm, and two environments during review returned `0.04808979266518426` and
   `0.04808979266518429` for the same input. Within one installation that value
   does not move; across a libm change it can, and if a climate's cumulative
   boundary sits between two such values, one block's draw changes with it.

   **Determinism is scoped to an installation** — see § Determinism scope for
   the decision and how to reverse it. Within one, weather is stable across
   processes, restarts, and upgrades of grimoire itself, which is the property
   play actually depends on.

   What enforces that scoped guarantee is **reference vectors**, which the spec
   now carries rather than merely promising:

   | cid | zone | axis | i | u |
   | --- | --- | --- | --- | --- |
   | `saltmarch-chronicle` | `saltmarch` | `temperature` | 0 | `0.45105387316006496` |
   | `saltmarch-chronicle` | `saltmarch` | `condition` | 0 | `0.761560896101852` |
   | `saltmarch-chronicle` | `saltmarch` | `wind` | 0 | `0.17774645354109275` |
   | `saltmarch-chronicle` | `saltmarch` | `condition` | 1 | `0.9654995835326089` |
   | `saltmarch-chronicle` | `saltmarch` | `condition` | −1 | `0.9510130394641975` |
   | `saltmarch-chronicle` | `highreach` | `condition` | 0 | `0.21315957935313057` |

   And end-to-end, through the filter and the wind table of the worked example
   above (`calm 1, breeze 4, strong 3, gale 1`) at ordinal 0:

   | persistence | W | taps | g(0) | Φ(g) | drawn |
   | --- | --- | --- | --- | --- | --- |
   | 0.0 | 0 | 1 | *(fixture)* | *(fixture)* | breeze |
   | 0.5 | 6 | 7 | *(fixture)* | *(fixture)* | calm |
   | 0.9 | 38 | 39 | *(fixture)* | *(fixture)* | breeze |

   `u` is final: BLAKE2b is bit-stable by specification and the mapping is
   exact arithmetic. The `z`, `g` and `Φ(g)` columns are filled in during
   implementation and committed as a **regression fixture** — generated once
   from the implementation, reviewed for plausibility, and asserted thereafter.

   That is the right shape of test for installation-scoped determinism: it
   catches an accidental change to the algorithm, the seeding, or the
   evaluation order, which is what would silently move a user's weather. It
   does not attempt cross-implementation conformance, which this spec no longer
   promises (§ Determinism scope). The fixture should carry a comment saying
   so, since a bare table of magic numbers invites someone to "fix" it later.

#### Determinism scope

**Decided: determinism is scoped to an installation.** The same campaign, block
and climate yield the same weather across processes, restarts, and grimoire
upgrades on a given machine. They are *not* guaranteed to match on a different
machine or a different Python.

This is recorded as a decision rather than a preference because six review
rounds pushed the other way, one reasonable finding at a time, and the
accumulated cost of the stronger promise was: two numerical functions vendored
with their coefficients normative, `W` computed by a hand-rolled multiplication
loop rather than a closed form, the filter's power generation and `sqrt` and
division individually pinned, and a reference table that could not be completed
until implementation and then had to be validated against `mpmath` rather than
against the code. None of that is wrong; it is simply a large amount of
machinery for a property this tool does not appear to need.

What the weaker scope gives up, precisely: if a user's Python or libm changes
*and* a climate's cumulative table boundary sits within an ulp of a drawn
quantile, one block in one campaign may render differently than it did before.
Nothing else in this spec depends on the difference.

**To reverse this**, restore in the construction above: vendored `erfc` (Cody,
Math. Comp. 22 1969 / CALERF) and `Φ⁻¹` (Wichura AS241 `PPND16`), both with
published coefficients and branch structure; `W` as the smallest `k` with
`a^k <= e⁻⁴` by repeated multiplication; explicit `sqrt` and division
semantics; and reference vectors validated against `mpmath` at 50 digits
instead of captured as a regression fixture. Those four items are the whole of
the difference and were written to be restored as a unit.
2. **Smoothing.** The correlated field is a normalized **one-sided** (causal)
   exponential filter over the latent, with `a = persistence`, indexed by the
   **block ordinal** of § Blocks — never by the minute coordinate:

   `g(t) = Σ_{k=0}^{W} a^k · z(t − k) / sqrt( Σ_{k=0}^{W} a^{2k} )`

   Floating-point addition is not associative — summing the same terms
   descending gives a result one ulp away — so the evaluation is pinned in full
   below rather than left to the formula.

   Normalization is by the **finite** sum, not by `sqrt(1 − a²)`: the infinite
   form leaves `Var(g) = 1 − a^{2(W+1)}` once truncated, a systematic error that
   grows as persistence falls.

   **This makes the marginal uniform to a tolerance, not exactly.** Three
   departures survive by construction and no formula removes them: `z` is drawn
   from `2⁵²` discrete atoms rather than a continuous normal; AS241 is a
   finite-precision approximation, so those atoms are not the exact quantiles
   they stand for; and a weighted sum of non-normal values is not normal, so
   `Φ` of it is not exactly uniform. The guarantee is therefore that observed
   frequencies match declared weights **within a stated tolerance**, and the
   weight-fidelity test asserts that rather than equality. A legal table with a
   bucket narrow enough to sit inside the residual error can still deviate —
   that is a property of representing continuous distributions in float64, not
   something this design can promise away.

   Evaluate as one ascending pass with carried powers — `a**k` and repeated
   multiplication differ in their last bits, and consistency here keeps a
   store's weather stable when the module is refactored:

   ```
   w = 1.0; num = 0.0; den = 0.0
   for k in 0..W:  num += w * z(t - k);  den += w * w;  w *= a
   g = num / sqrt(den)
   ```

   Powers come from carrying `w`; numerator and denominator accumulate in the
   same ascending pass.

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
   `a > 0`, else 0. Since the sum runs `k = 0..W` inclusive, the tap count is
   `W + 1`: 1 tap at `p = 0`, 39 at `p = 0.9` (`W = 38`), 399 at `p = 0.99`
   (`W = 398`). Cost is O(W) per sample, still O(1) in campaign age, which is
   the property this construction exists for.
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
**skipping zero-weight entries entirely**, and selects the first remaining entry
whose running cumulative exceeds `u` — buckets are half-open,
`cum_{i−1} <= u < cum_i`, with the first starting at 0 and **the last
positive-weight entry** treated as closing at 1.0 so floating-point drift in the
final sum cannot fall through.

Skipping zero-weight rows is not merely tidy. Closing on the *physical* last
entry lets rounding hand the draw to a disabled row: with weights `[1, 9, 0]`
the second entry's cumulative comes out as `0.9999999999999999`, which is
exactly the largest quantile this latent can produce, so the strict comparison
falls past it and the forced-last rule returns the zero-weight third entry. If
that zero came from `requires_temp` filtering, the result is an ineligible
condition — the precise outcome the constraint exists to prevent, reached
through arithmetic rather than through the tables. The convention has to be stated because `u` is
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
  to declare (§ Climate documents) — **ties broken by array position, earliest
  first**. Not "by entry key": tables are arrays of objects and have no key, a
  leftover from when they were name-keyed maps, and an implementation following
  it literally would look for a field that does not exist. It must not be
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
- **A two-sided clamp.** State is confined to `[0, cap]` after every block, not
  merely capped above. An upper cap alone bounds nothing when contributions can
  be negative: melt subtracted from an already-empty snowpack drives the value
  arbitrarily negative, `cap` stops bounding the omitted pre-window state, and
  the error bound below stops following. Clamping at zero is also the
  physically correct reading — there is no negative snow.

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

  The response carries the three resolved axes with per-axis provenance and
  covering span stack, **plus the resolved climate id, the active season's
  name, and that season's three tables**. The tables must come from the server:
  the popover's selects offer the active season's entries, and the client
  cannot determine those on its own — the climate may be inherited from the
  campaign default or fallen back from a dangling id, and the season depends on
  year-fraction arithmetic over the campaign's calendar. Deriving them
  client-side means reimplementing the fallback chain and the calendar maths,
  and lets the popover disagree with the weather displayed beside it.
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
  carrying **both** tier flags — `builtin: true/false` and `custom: true/false`
  — rather than one tag; a single `custom` label cannot distinguish a custom
  climate that shadows a preset from one that stands alone, and the editor
  needs that to choose between **Revert to preset** and **Delete**, and to know
  whether deleting frees the id), `GET /api/climates/{id}`,
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
a climate that is still referenced warns and names the referrers — **locations
that name it, campaigns whose `climate.json` defaults to it, and worlds whose
default is it**. Locations alone would miss the worst case: a custom-only
climate used purely as a campaign default has no location naming it, so the
warning would report nothing, deletion would proceed, and every untagged
location in that campaign would quietly switch to `temperate-interior`.

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

  **Where any condition is constrained, every condition row shows per-band
  odds** — not just the constrained ones. Filtering `snow` out on a non-freezing
  block renormalizes the *whole* surviving table, so an unconstrained row's
  probability moves too: in the worked table `clear` is `2/14 = 14.3%` on
  freezing blocks and `2/12 = 16.7%` everywhere else. A single number per row
  would be wrong for every row, not only the constrained ones.

  So: a season with no `requires_temp` anywhere shows one simple share per row;
  a season with any constraint shows a small per-band breakdown for all rows.
  Anything in between — one number for unconstrained rows and a breakdown for
  constrained ones — presents two incompatible quantities in one column.
- **Season boundaries are edited as dates, stored as fractions**, since `0.45`
  is not authorable by a human (§ Climate documents). The conversion needs a
  fixed reference or it is not stable: 1 March is a different fraction in a
  Gregorian leap year than in a common one, and custom calendars vary more.
  Climates are also campaign-agnostic while calendars are campaign-scoped, and
  a campaign has no single "current" date — its scenes each carry their own.

  So: the display provider is the calendar of the campaign the editor was
  opened from, falling back to `gregorian` when there is none, and the
  reference year is that provider's `RULE_REFERENCE_YEAR` — already the
  convention for calendar-relative rules in `calendars/base.py`.

  **`GET /api/campaigns/{cid}/calendar` gains `reference_year` and
  `year_length`** in its response. `RULE_REFERENCE_YEAR` is a backend class
  attribute a custom provider may override, and the existing calendar routes
  expose neither it nor the year's length — `/calendar/months` requires the
  client to supply a year, which is the very thing it does not know. Without
  this the editor would guess, and guess wrong for exactly the homebrew
  calendars this design otherwise supports.

  Both directions are pinned, because "round to the day" leaves three choices
  open and the editor and the resolver disagreeing by one day moves weather:

  - **Day index is zero-based**: the first day of the reference year is index
    `0`, so `fraction = day_index / year_length` and the year's first day is
    fraction `0` exactly.
  - **Fraction → day** uses `ceil(fraction * year_length)` — the smallest day
    whose own start fraction reaches the boundary. Not `floor`: with half-open
    seasons the incoming season begins on the first day at or after the
    boundary, so for a 365-day year and `from: 0.45`, day 164 has fraction
    `164/365 < 0.45` and still belongs to the outgoing season. `floor` would
    display 164 and the resolver would use 165, showing every existing
    fractional boundary a day early.
  - **A fraction past the last day's start has no day to name.** Anything in
    `((L−1)/L, 1)` — `0.999` in a 365-day year — makes `ceil` return `L`, which
    is not a valid zero-based index and would either build an invalid date or
    roll into a year whose fractions start again at 0. The editor **writes only
    day-start fractions** `k/L`, so it can never create one; a pre-existing one
    displays as the last day of the year with its raw fraction shown beside it,
    and is left byte-untouched unless the user edits that boundary, at which
    point it snaps to a day start.
  - **Day → fraction** emits the day's own start, `day_index / year_length`,
    never a midpoint or an end.

  Season intervals are half-open in the year exactly as override spans are in
  time: `[from, to)`. A boundary edited to a given date makes that date the
  first day of the incoming season.

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

**Both default setters validate the id against the registry**, rejecting an
unknown one with the available ids exactly as the location field does. A
misspelled default is the same invisible typo one level up, and worse in blast
radius: it silently moves *every* untagged location in the campaign to
`temperate-interior`. Resolver leniency stays for files that go dangling later
— a climate deleted after the fact must not break a turn — but nothing should
be able to write a dangling reference through an API in the first place.

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
it already has, and the wizard passes the user's selection through it. It
**validates against the registry before creating any campaign files**, exactly
as the two default setters do and for the same reason — `create_campaign`
already resolves its `calendar` argument up front so an unknown provider fails
before anything is written, and an unknown climate should fail the same way
rather than producing a campaign whose every untagged location silently reads
`temperate-interior`. Copying
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

**Empty means absent, and is normalized before any validator runs.**
`EntityEditor` binds each field to `fields[f.key] ?? ""` and sends `""` for one
the user cleared or never set (`EntityEditor.tsx:427`), while the store only
drops empty values later, in `entities.update_entity:113-117` — after route
validation. A validator applied literally to the incoming dict would therefore
reject an ordinary location save that simply has no climate. Empty strings are
coerced to absence at the route boundary, and validators only ever see values
that are actually present.

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

  *"Leave to chance"* **clears that axis over the selected duration**, rather
  than merely omitting it from the record being written. Omitting would be the
  simpler implementation and the wrong behaviour: a user who opens the popover
  on an overridden axis and selects *leave to chance* means "stop overriding
  this," and would otherwise watch the setting appear to do nothing.

  The duration applies to clearing exactly as it does to setting. Clearing one
  block inside a three-day storm splits that span in two and leaves the rest
  standing; it does not erase the axis across the whole record, which would
  silently rewrite scenes on either side that the user never had in view.

  **The select also offers the currently authored value**, even when the active
  season's table has no such entry. Overrides are deliberately not constrained
  to the table — this spec's own example stores `condition: "blizzard"` for a
  climate with no `blizzard` row, and the #46 extractor writes whatever the
  narration invented. Without this, opening the popover on such an override
  shows a blank, and saving an unrelated axis quietly discards the authored
  one. Values not in the table are marked as authored so the distinction stays
  visible.
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
POST /api/campaigns/{cid}/weather/clear   { location, from, to, axes: [...] }
```

It takes a **range**, not a single moment, because the popover's duration
control applies to clearing as much as to setting. The server truncates or
splits spans that only partly intersect the range, rather than stripping the
axis from the whole record.

**Clearing is one atomic server-side call, not client-orchestrated edits.**
Removing a single axis from a span that sets several means *mutating* that
record while preserving its `source`, `note`, `set_at` and range — and the
client has only whole-record `DELETE` and a create-shaped `PUT` to work with,
so it would have to delete and recreate, losing exactly the fields precedence
depends on. Worse, it would have to do that across a stack of spans
non-atomically, leaving a half-cleared state if anything failed midway. The
server walks the covering stack, removes the named axes over the requested
range — splitting a span whose remainder still applies outside it — deletes any
span left setting nothing, and returns the new resolved weather.

**When a split produces two fragments, the earlier one keeps the original
`id` and each further fragment gets a freshly generated one**, persisted as
part of the same atomic write. Copying the id onto both halves would leave two
records sharing a `DELETE` address until some later load happened to
canonicalize them; regenerating both would invalidate an id the client may
have just been handed. Earlier-keeps-it is arbitrary but it has to be written
down, since the client holds ids from the response it is acting on.

**It only mutates spans stored under the requested location.** A `_default`
span covering the moment is inherited by every location in the campaign, so
truncating it to clear the docks would clear the lighthouse and everywhere else
too; skipping it would leave the docks overridden and the button ineffective.
Neither is acceptable, so an inherited span is **suppressed rather than
edited**: the server writes a location-scoped span naming that axis in a
**`suppress` list**, which resolves as "no override here" and outranks
`_default` by the specificity rule already in § Resolution. Clearing a
campaign-wide override for everyone is then a separate, explicit act — issuing
the clear against `_default` itself — rather than something a user does by
accident while adjusting one harbour.

**Suppression is a tombstone: it terminates resolution for that axis across
every lower-precedence record, not merely the one it shadows.** Specificity is
otherwise compared within a source rank, so a location-scoped suppression that
only outranked the manual `_default` would expose the extractor `_default`
beneath it — reinstating exactly the shadow-promotion that clearing the whole
stack exists to prevent, and doing it one rank down where nobody would look.
A covering suppression means the axis is procedural here, full stop.

`suppress` is a **separate field listing axis names**, not a reserved value in
the axis fields themselves. A sentinel string like `condition: "procedural"`
would be indistinguishable from an authored value: entry names are
unrestricted, overrides are explicitly not confined to the active table, and a
climate may perfectly well contain a condition called `procedural` — at which
point selecting it as an override would suppress the weather instead of setting
it. Structure cannot collide with content; a reserved string always can.

The HUD renders a suppressed axis as generated — but the popover must still
offer **Resume inheriting**, shown when a suppression span covers this location
and moment, which deletes that span. Without it the clear is a one-way door:
the axis looks procedural, so **Clear override** does not appear, and setting a
concrete value only writes another local exception rather than restoring the
campaign-wide one. Suppression is state the user created and must be state the
user can remove.

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
- Reference vectors: `u` is asserted against the values tabulated in § The
  construction, concretely, which are final. `z`, `g`, `Φ(g)` and the drawn
  entry are captured once as a **regression fixture** and asserted thereafter,
  so an accidental change to the algorithm, the seeding or the evaluation order
  is caught — the failure that would silently move a user's existing weather.
  The fixture is scoped to this installation and does not claim
  cross-implementation conformance (§ Determinism scope).
- Zero-weight rows are skipped by `inverse_cdf` and the last *positive-weight*
  entry closes the range — asserted with weights `[1, 9, 0]` at the largest
  representable quantile, where closing on the physical last entry would return
  the disabled row.
- Accumulation order: summing the filter descending in `k` produces a different
  `g` from the documented one, so the test pins ascending order rather than
  merely the formula.
- Inverse-CDF boundaries: a quantile landing exactly on a cumulative boundary
  selects the following entry (`cum_{i−1} <= u < cum_i`), and a zero-weight
  entry is never selected at any `u`.
- Latent quantile mapping: `u` is strictly inside `(0, 1)` and injective across
  the extremes of the digest range — specifically at `n = 0` and
  `n = 2⁵² − 1`, and for adjacent `n` near the top, where the discarded
  53-bit-midpoint form produced `1.0` and duplicate values.
- Duplicate explicit span ids under one storage key are re-derived canonically
  on load, leaving both records separately addressable.
- A climate containing an entry literally named `procedural` still works: an
  override selecting it sets that value, and does not suppress the axis.
- A default-climate `PUT` naming an unknown id is rejected at both the campaign
  and world routes, while a default that goes dangling later still resolves to
  the shipped preset rather than raising.
- Override precedence: location beats `_default`, higher `seq` beats lower,
  a record with no `seq` reads as 0 and loses to anything written since, and
  two spans differing only in array order resolve identically.
- Suppression is a tombstone: a location-scoped suppression covering a
  `_default` stack that holds both a manual and an extractor span returns the
  axis to procedural weather, rather than exposing the extractor span beneath.
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
- Cross-persistence zone coupling, asserted on the **latent** field: two
  locations sharing a zone but configured with different `persistence` sample a
  correlated `g`, since both are smoothings of the same `z`. Assert on `g` and
  not on rendered weather — a zone member using a single-entry table renders
  constant output, which correlates with nothing while the field underneath is
  perfectly correlated. This fails immediately if an implementation reseeds or
  rescales the latent per correlation length.
- Recency under sub-second writes: two spans written within the same second
  resolve in write order, which `set_at` alone cannot express.
- Boundary display: for a 365-day year and `from: 0.45`, the editor shows day
  165 — the first day the resolver assigns to the incoming season — not 164;
  and a boundary of `0.999` displays as the last day of the year rather than
  producing an out-of-range index.
*(Accumulator truncation — that extending the window past *K* moves the
reported value by less than its resolution, including across a freeze longer
than *K* — belongs with the accumulator work, not here. It cannot be written
against this scope: the caps, decay rates and reporting resolutions it needs
are all deliberately left to that later pass, so a v1 implementation would have
to invent the deferred feature in order to test it.)*
- Random access: resolving block *t* directly and resolving it after walking
  every block from 0 to *t* yield the same answer, at any *t*, including
  negative — the property that makes prequel scenes and multi-year jumps free.
- Filter evaluation: `g` matches a reference computed with carried powers and a
  single ascending pass, and differs from one computed with `a**k` or a
  separately accumulated denominator — the test that pins the arithmetic rather
  than only the formula.
- Suppression is removable: after clearing an inherited `_default` override at
  one location, **Resume inheriting** is offered there and restores the
  campaign-wide weather.
- Splitting a span by a mid-range clear leaves the earlier fragment holding the
  original id and the later one holding a fresh id, both separately deletable.
- Weight fidelity, against a climate **with no `requires_temp` anywhere**:
  over a long sample the observed frequency of each condition matches its
  normalized weight **within the stated tolerance**, at several persistence
  settings — the test that catches a non-uniform marginal reaching the inverse
  CDF. Tolerance, not equality: the latent is `2⁵²` discrete atoms passed
  through a finite-precision `Φ⁻¹`, so exact uniformity is unavailable in
  principle and asserting it would fail every conforming implementation.

  **The protocol is fixed, not left to the implementer**: one climate,
  `N = 100_000` samples, each entry's observed frequency asserted within
  `3 · sqrt(p(1−p)/N)` of its declared `p`. A stated tolerance with no number
  attached is not a testable claim — it permits an arbitrarily permissive
  bound.

  **The samples come from `N` distinct zone seeds at one ordinal, not from `N`
  consecutive ordinals of one zone.** Consecutive blocks are autocorrelated by
  construction — that is the entire point of `persistence` — so a run of
  100,000 of them carries far less information than 100,000 independent draws,
  and severely so near the `0.998` clamp. The binomial standard error would
  understate the real spread and a conforming implementation would fail the
  bound. Fixing the seed makes such a failure reproducible, not correct.
  Sampling across independent streams tests the marginal distribution, which is
  what weight fidelity is a claim about.

  So bounded this way the test is not statistically flaky: generation is
  deterministic, the sample set is fixed, and the result is a stable pass or
  fail. The `3σ` margin guards against a genuinely wrong distribution rather
  than against random variation. The
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
- Deleted-location state: a scene whose `location_history` still names a
  deleted location resolves weather from the campaign default, keyed on that
  id as its zone, and does not raise — matching how `context.py:535-539`
  already degrades the setting block.
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
- Clearing one block inside a multi-day override splits it, leaving the blocks
  either side overridden as before.
- Clearing at one location that inherits a `_default` override returns that
  location to procedural weather and leaves every other location in the
  campaign still overridden.
- Deleting a climate used only as a campaign or world default warns and names
  that campaign or world, rather than reporting no referrers.
- In a season containing any `requires_temp`, **every** condition row renders
  per-band odds — including unconstrained ones, whose probability also moves
  when a constrained sibling is filtered out.
- Season boundary conversion round-trips: a boundary edited to a date reads
  back as that date, and the resolver treats that date as the first day of the
  incoming season.
- An override holding a value absent from the active season's table — the
  `blizzard` case — renders in the popover as the selected value rather than a
  blank, and survives a save that touches only another axis.
- The campaign settings control reads and writes the default climate, and the
  campaign wizard prefills it from the world's default.
- An override saved as *"until I clear it"* stores `to: null` and matches
  arbitrarily far-future moments; clearing it truncates it at the clear range,
  so re-reading a scene from before that point still shows the override, while
  `DELETE` on the span removes it from history entirely.
- A location saved with an unknown climate id is rejected with the available
  ids, rather than accepted and silently falling back — asserted **through the
  API as well as the form**, since the API path is the one a script or importer
  takes.
- Individual weights: an axis containing a negative or non-finite weight is
  rejected inline even when a positive sibling is present.
- Duplicate entry names within one axis are rejected inline, as is an entry
  whose name is empty or whitespace.
- A location saved with `climate` cleared to an empty string succeeds and
  removes the field, rather than being rejected by the registry validator.
- The climate list distinguishes a custom climate shadowing a preset from a
  custom-only one, and the sidebar action reads **Revert to preset** in the
  first case and **Delete** in the second.
- `POST /api/campaigns` with an unknown `climate` fails before any campaign
  directory is created.
- A climate declaring `persistence: 1` loads without error and resolves at the
  clamp — the accepted range admits it even though the effective range does
  not.
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

*(Determinism scope was open here and is now decided as installation-scoped in
§ Determinism scope, which also records exactly what to restore if the stronger
cross-machine promise is ever wanted. Decided rather than left open because a
planner cannot implement two incompatible constructions, and the document had
begun to describe both.)*
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
