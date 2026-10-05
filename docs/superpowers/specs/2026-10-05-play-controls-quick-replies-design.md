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
{"id": "qr-1", "label": "Look around", "kind": "send",
 "text": "I take in the room.", "mode": "send"}
```

| `kind` | Fields | Does |
|---|---|---|
| `send` | `text`, `mode` | posts `text` as the player (Speak) |
| `direct` | `text`, `mode` | sends `text` as a director note (OOC instruction) |
| `roll` | `notation`, `label?` | rolls the saved dice string (`POST .../roll`) |
| `task` | `task` | runs an auxiliary task: `rolling_summary`, `scene_break` or `suggestions` |
| `opener` | — | opens the opener generator (only on a scene with no posts) |

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
