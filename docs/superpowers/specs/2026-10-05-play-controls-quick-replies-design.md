# Play controls VI — quick replies

Step 6 of the SillyTavern-parity play controls (programme table in
`2026-10-05-play-controls-swipes-design.md`). Decisions a brainstorm would have
put to the owner are recorded with their reason; the owner asked for the
programme to run without stopping.

## The request

> Composer buttons that send canned text, an OOC instruction, or an action:
> roll a saved dice string, run an auxiliary task, open the opener generator.
> Sets per world or campaign, stored as JSON. Once a plugin API exists, a quick
> reply can call a plugin command.

## A quick reply

```json
{"id": "3f9c…", "label": "Look around", "kind": "send",
 "text": "I take in the room.", "mode": "send"}
```

Ids are minted by the server (uuid4 hex) for any entry saved without one, so a
campaign reply can never collide with an unrelated world reply by accident.

| `kind` | Fields | Does |
|---|---|---|
| `send` | `text`, `mode` | posts `text` as the player (Speak) |
| `direct` | `text`, `mode` | sends `text` as a director note (OOC instruction) |
| `roll` | `notation`, `roll_label?` | rolls the saved dice string (`POST .../roll`); `roll_label` is the transcript label, distinct from the button `label` |
| `task` | `task` | `rolling_summary` or `scene_break` (run now, forced), or `next_scene` (open the next-scene chooser, which fetches its own suggestions) |
| `opener` | — | expands and focuses the opener generator (only on a landed scene with no posts) |

- `mode` for the text kinds: `send` (send immediately) or `insert` (put the
  text in the composer for the player to edit and send) — SillyTavern offers
  both, and a canned line often wants a tweak.
- `label` up to 40 characters; `text` up to 2000; `notation` validated by the
  dice parser when saved (400 on a bad string).
- **Plugin commands** are a reserved kind, `plugin`, rejected (400
  `plugin_api_unavailable`) until a plugin API exists. Reserving the name now
  means a set written later stays readable.

## Sets and layering

- World set: `<world>/quick_replies.json`; campaign set:
  `<campaign>/quick_replies.json`. Each is `{"replies": [<quick reply>, …]}`.
- **Effective set** for a campaign: the world's replies in order, then the
  campaign's. A campaign reply with the same `id` as a world reply **replaces**
  it in place; a campaign entry `{"id": …, "hidden": true}` hides a world reply.
  This is the tracker-field layering pattern, which players already know.
- New module `store/quick_replies.py`: lenient reads (missing or garbled file
  is an empty set), strict writes through `atomic`; the campaign writer takes
  `campaign_lock(cid)` and the module is classified in `locks.DOMAIN_MODULES`.
  World bundles and campaign forks carry the files with their directories.

## Routes

- `GET/PUT /worlds/{wid}/quick-replies` — the world set.
- `GET/PUT /campaigns/{cid}/quick-replies` — the campaign's own set.
- `GET /campaigns/{cid}/quick-replies/effective` — the layered set the composer
  shows.
- Plain pydantic models; the whole set is replaced on PUT (sets are small).

## Composer

- A strip of buttons above the input bar, in effective order; absent when the
  set is empty. Horizontal scroll on a phone.
- Each button is disabled exactly when the control it stands for is:
  - `send`/`direct` (send mode): the Send button's guards;
  - `insert` mode: never disabled while the composer is shown;
  - `roll`: the dice button's guards (`busy`, scene locked, rolling) — but
    **not** gated on a bound mechanics module, since the roll route only parses
    dice;
  - `task`: its route's own conditions (`suggestions` on a scene with posts;
    `rolling_summary` and `scene_break` on an active scene);
  - `opener`: only on a scene with no posts.
- `send()` is parameterised (text, director flag) so a quick reply does not
  have to write composer state and then call it; the existing call sites pass
  the composer's own state.
- The opener generator gets a mount point outside the empty-scene cast panel
  so a quick reply can open it (it is still only offered on an empty scene).
- **No hotkeys.** A bare key that posts or spends money would break the
  registry's rule; the buttons are one tap.

## Editors

The list/detail pattern (CLAUDE.md): `components/QuickReplyEditor.tsx`, a rail
of replies plus a read-only view and an explicit Edit step, with a form whose
fields follow `kind`. Mounted:

- in the world view's **Writing** group as "Quick replies" (world scope);
- in the campaign hub's **Settings** column as "Quick replies" (campaign
  scope), which also lists the inherited world replies read-only with
  "Override" and "Hide" actions that create the campaign entry.

## Testing

Backend:
- world and campaign sets round-trip; effective layering appends, replaces by
  id and hides; a garbled file reads as empty;
- validation: label/text length, bad dice notation, unknown kind, `plugin`
  refused with `plugin_api_unavailable`;
- lock domain and atomic guards pass for the new module.

Frontend:
- the strip renders the effective set and is absent when empty;
- `send` posts the text, `direct` sends a director note, `insert` fills the
  composer without sending;
- `roll` calls the roll route with the saved notation (and is offered without a
  bound module);
- `task` calls the matching route; `opener` opens the generator on an empty
  scene and is absent otherwise;
- disabled states mirror the controls they stand for;
- the editor follows the list/detail tests (row → read-only view, Edit → form,
  + New → form), and campaign override/hide create the right entries.

## Gate resolutions (binding where they refine the text above)

Spec → planning gate: independent adversarial review (stand-in for
`/codex:adversarial-review`, Codex CLI unavailable; owner-approved).

**Tasks.** `rolling_summary` and `scene_break` call their routes with
`force=true` and an absolute `upto=<firstIndex + messages.length>` (the
transcript is fetched in windows, so the bound counts from the window's
offset), so a fold cannot swallow an unanswered post. The inspector panel
sends no `upto` at all — its buttons are held while a turn streams — so the
strip bounds absolutely because it fires outside that turn-held context. They
are disabled while
`sceneLocked`, while no connection is `ready`, and while the same task is
already running from either the strip or the inspector. Their outcome
(`refreshed`/`asked` false, or an error) is shown as a composer notice; a
success bumps the inspector key as the panel does. `next_scene` opens
`NewSceneChooser`, whose own hook fetches suggestions — the campaign-level
suggestions draft is not called directly, because its only consumer is that
chooser.

**Sending.** The parameterised send path (`sendText(text, director)`) never
reads or clears the composer's `input`, so a draft survives a quick reply;
every use of `directing` in that path takes the parameter. On failure the
canned text is not handed back to the composer (it is re-tappable) and the
composer mode is not flipped. A quick reply consumes the pending one-shot
response targets exactly as Send does (the player set them for the next turn).
In a scene with no player character (Speak disabled there), a `send` reply is
disabled with the title "This scene has no player character". `text` must
contain a non-space character (an empty send is the "next NPC round" path and
is not a quick reply).

**Insert.** Into an empty composer: the text, and a `direct` insert switches the
composer to Direct (a `send` insert to Speak). Into a same-kind draft: appended
after a blank line. Into a different-kind draft: the box is kept (one
composer carries one mode) and the text is parked in `parkedPrompts`, behind
the existing held-draft notice, exactly as a recovered prompt waits; it comes
back when the box is cleared (sending the draft clears it, so the parked text
then arrives and flips the composer to its kind, as a recovered prompt does).
If something recovered is already parked for the scene, the insert is refused
with a composer notice instead, so the player's own words are never
overwritten.

*Recorded resolution (final review):* this replaces the earlier "refused with
the existing held-draft notice" — that notice reads "held · clear the box to
get it back", which is false unless the text is actually held, so the two
rulings can only both be honoured by parking it.

**Roll.** Disabled by the dice button's real guards — `!activeId || busy ||
sceneLocked || messages.length === 0 || rolling` — and while `moduleBound` is
still unknown. `doRoll` becomes `doRoll(notation, label)` with its own notice
for errors (the popover's form error is unreachable from the strip), still
taking the roll latch and asking for follow-ups. **This deliberately overrides
the recorded rule that hides the dice button in a campaign with no mechanics
module** (freeform play): a saved roll is something the player configured, and
the roll route has no module gate.

**Opener.** The opener generator is already rendered inside the empty-scene
cast panel; the quick reply expands that panel (controlled `open`) and focuses
the opener prompt rather than mounting a second, stateful copy. Enabled when
the cast panel is mounted (a landed scene with no posts) and a connection is
ready.

**Sets.** `{"version": 1, "replies": [...]}`; at most 50 entries; ids unique
within a set; a hide entry is `{"id", "hidden": true}` and nothing else.
Request models are loose (`kind: str`, optional fields, the `TrackerLayer`
`list[dict]` style) and every rule is checked in the store, so violations are
400s, not pydantic 422s; the store normalises each entry to its kind's fields
(no `null`s written). Reads salvage per entry: an entry of an unknown kind (a
future `plugin`, a newer build's kind) is kept verbatim on disk and preserved
across a PUT, but not shown. **Concurrency**: every PUT carries `expect`, the
digest of the set the client read, and is refused 409 `set_changed` when the
file moved — override and hide are client read-modify-writes and would
otherwise lose an update. A campaign with no world has an empty world set. The
world writer is atomic-only (no world lock, as the tracker's world layer).
A campaign PUT bumps the campaign's write token and activity stamp like any
campaign write; that is accepted.

**Editor.** Reorder with ↑/↓ in the rail (order is the contract). The campaign
view's inherited world replies offer Override, Hide, and — on a hidden one —
Show (removes the hide entry). Mounting touches `worldPaths.ts`
(`FLAT_SECTIONS`, `SectionTarget`), `WorldView.tsx` (`IndexKey`, `INDEX`
Writing group, the campaign-shape filter — world-only like tracker — `countOf`,
render), `CampaignHub.tsx` (panel union, Settings list), and the store
facade's `__all__`.

**One-tap sends** are the feature; the strip's buttons carry their full text in
a `title` so a mis-tap is at least visible before it happens.
