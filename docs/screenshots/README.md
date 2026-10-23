# Screenshot fixtures

[Documentation](../README.md) · [Development](../reference/development.md)

The public images show the running application with synthetic text from
[library.json](../../backend/tests/fixtures/documentation/library.json).
They contain no records, images, model responses or statistics from a personal
Grimoire library. Initials are the application's normal fallback for records
without art in the original captures below; no generated artwork or image editing
was used for those captures. The [PR #554 VN captures](pr-554/README.md) add
procedurally generated fixture artwork.

## Published captures

Captured on 2026-10-20 from the `docs/cleanup` worktree, based on `9a33cafd2`.
The UI code is unchanged from that base. The later cache/build changes only
remove documentation from fingerprints and packaged assets.
The browser used the light theme at device scale 1, with local fonts loaded.

| File | View | Browser viewport |
|---|---|---|
| [scene.png](scene.png) | Saltmarch → The ledger reopened | 1500 × 1150 |
| [world-overview.png](world-overview.png) | Library → Worlds → Realm → Overview | 1500 × 1150 |
| [character-editor.png](character-editor.png) | Realm → Characters → Seraphine, card read view | 1500 × 1150 |
| [campaigns.png](campaigns.png) | Campaign list, at `/` | 1500 × 750 |

The character image retains its existing filename so README image links stay
stable. Fields enter edit mode through their **Edit** controls.

## Reproduce the fixture session

Use the repository's [verify procedure](../../.agents/skills/verify/SKILL.md).
Its launch pattern is the authority for isolating the app. Use a new empty
scratch directory, dedicated loopback ports, and a fresh browser profile.
Set `GRIMOIRE_HOME` **before importing the backend**. Never reuse a personal
instance, the default app ports, an existing library or a browser profile with
personal state.

For these captures the backend used port **8209** and Vite used **5209**:

```powershell
$env:GRIMOIRE_API = 'http://127.0.0.1:8209'
cd frontend
npx vite --port 5209 --strictPort
```

On a Unix shell, use
`GRIMOIRE_API=http://127.0.0.1:8209 npx vite --port 5209 --strictPort`.

The session reused the isolated `client`, bootstrap-pointer and process-lock
fixtures from [conftest.py](../../backend/tests/conftest.py). Its live uvicorn
server served that same app; its existing TestClient owned the app lifespan.
The model dependency was overridden with
`tests.llm_fakes.from_cassette("campaign_flow")`. Provider/catalog/key URLs were
also redirected to the loopback fixture server, so an unexpected request could
not reach a hosted service. No generation was needed for the published captures:
the transcript is the hand-authored text in `library.json`.

Before seeding, verify that `GET /api/worlds` returns `[]`. Then use the app API
to create the records, in this order:

1. Create an OpenRouter provider at `/api/llm-connections`, named
   `Fixture provider`, with the synthetic key `sk-or-fixture`. Assign its ID and
   `test/model` to Primary through `/api/inference/settings` →
   `roles.primary.selection`. The fake owns all model responses.
2. Set `theme` to `light` with `PUT /api/config`.
3. Post the fixture's `world` to `/api/worlds`.
4. Post each `characters` entry to that world's `/characters`. Build its card
   with `store.characters.blank_card(name)` and apply the fixture's fields to
   `card["data"]`. Post the fixture's `pc` to `/pcs`.
5. Post `location` to `/locations` and each `lore` entry to `/lore`.
6. Post `campaign` plus the returned `world` ID to `/api/campaigns`.
7. Post `scene` to `/api/campaigns/{cid}/scenes/import`, adding the returned
   location ID and a cast containing both characters and the PC, with their
   returned version IDs. Use the fixture's messages unchanged.

All record writes in the capture session went through these API routes. The
frozen compatibility store was not changed or regenerated. Temporary launch
scripts, browser profiles, logs and generated stores belong in ignored scratch;
only the synthetic fixture and reviewed PNGs belong in the public tree.

## Capture and review

Open each view in the table, wait for its data and fonts to load, and capture
the viewport. These images were taken with headless Chrome through its local
debugging connection after the integrated browser could not start. They are
direct browser captures, with no DOM content replacement or redaction.

Inspect every resulting image at readable size: verify fixture names and text,
loaded assets, useful framing, theme and absence of private content. Check the
browser's console and failed requests. Update this provenance when refreshing
images, then follow each README image link. Shut down the temporary browser and
servers. Stop Vite before running `make check`: its dependency reinstall cannot
replace an `esbuild.exe` still open on Windows.
