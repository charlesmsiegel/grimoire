# Seed, drive and stop

## Seed through the current API

Create a provider, then assign its model through roles. Legacy `openrouter_key`
and `model` fields on `/api/config` are not the current settings interface.

1. `POST /api/llm-connections` with `kind: "openrouter"`, `name: "Fixture provider"`,
   `preset: "openrouter"` and `api_key: "sk-or-fixture"`. Retain its returned `id`.
2. `PUT /api/inference/settings` with:

   ```json
   {"roles":{"primary":{"selection":{"provider":"<returned-id>","model":"test/model"}}}}
   ```

3. Create a world, characters with V3 cards, a PC and any locations/lore needed.
   Use existing placeholder names such as Realm, Saltmarch, Seraphine, Mara and
   Winifred. Record returned IDs and version IDs rather than guessing slugs.
4. Create a campaign with the world's returned ID, then scenes and cast through
   their API routes. `/api/campaigns/{cid}/scenes/import` accepts a reviewed
   synthetic transcript, location and typed cast references when generation is
   unnecessary. World records can be inherited; campaign-local records remain
   separate from their shared world counterparts.

For public screenshots use
[library.json](../../../../backend/tests/fixtures/documentation/library.json).
Its text is synthetic and may be published; a private library must never be
used as seed data. Do not regenerate the frozen compatibility `home/` fixture.
The running app's `/openapi.json` describes request shapes from code.

## Drive, inspect and stop

Use the available browser-control procedure on `http://127.0.0.1:5209`.
The campaign list is `/`, the hub is `/campaigns/{cid}`, and play is
`/campaigns/{cid}/scenes/{sid}`. Inspect visible behavior, console exceptions,
failed requests and loaded assets. Initial-letter art is expected for text-only
fixtures; investigate image failures rather than dismissing them automatically.

Keep launchers, logs, profiles and generated stores in ignored scratch. Review
every public screenshot for fixture-only content before copying it into the
manual. Stop only the temporary browser and servers started for this session.
Stop Vite before `make check`: Windows cannot reinstall a running `esbuild.exe`.

Source owners: [world routes/](../../../../backend/src/grimoire/routes/worlds/),
[inference routes](../../../../backend/src/grimoire/routes/inference.py),
and [overlay/](../../../../backend/src/grimoire/store/overlay/).
