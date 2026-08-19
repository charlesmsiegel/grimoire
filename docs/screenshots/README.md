# Screenshots

The images the [root README](../../README.md) showcase points at.

## Every one of these is fake, and has to be

`CLAUDE.md` forbids a real world, campaign or character name anywhere in this
repo — and a screenshot of a working install shows a real library by default.
So these are captured against an **isolated store** seeded with invented
content, using only placeholder names the codebase already uses (Saltmarch,
Mara, Seraphine, Winifred).

**Never replace one of these with a capture of a live library**, however
harmless the visible records look. If a screenshot needs updating, rebuild the
fixture and re-capture.

## How they were made

The harness is the `verify` skill (`.claude/skills/verify/SKILL.md`), which
exists for exactly this: a grimoire instance with `GRIMOIRE_HOME` pointed at a
scratch directory and `grimoire.openrouter.API_URL` pointed at a local mock
replaying `backend/tests/fixtures/llm/campaign_flow.json`.

1. Launch the isolated backend on **8199** and vite on **5199** — never 8173 /
   5173, which is where someone's real instance lives.
2. Confirm the isolation before anything else: `curl
   http://127.0.0.1:8199/api/worlds` must return `[]` on first run. If it
   returns worlds, you are pointed at a real library — stop.
3. Seed a world, a few characters, locations, lore, greetings, a campaign, a
   PC and one scene through the API. All names invented.
4. Play a turn or two against the mock so the transcript has body.
5. Drive Chromium with Playwright at 1440×900, `deviceScaleFactor: 2`, and
   screenshot each page.

## The files

| File | Page |
|---|---|
| `scene.png` | a scene mid-play — transcript, cast rail, response controls |
| `world-overview.png` | a world's overview: record counts and the setup checklist |
| `character-editor.png` | a character's world record — the list/detail pattern |
| `campaigns.png` | the campaigns home |
