# Screenshots

The images the [root README](../../README.md) showcase points at. **This file
is the one place the capture procedure is written down** — `CONTRIBUTING.md`
links here rather than keeping a second copy, because a safety procedure in
two places is a safety procedure with one stale copy.

## Every one of these is fake, and has to be

`CLAUDE.md` forbids a real world, campaign or character name anywhere in this
repo — and a screenshot of a working install shows a real library by default.
So these are captured against an **isolated store** seeded with invented
content, using only placeholder names the codebase already uses (Saltmarch,
Mara, Seraphine, Winifred).

**Never replace one of these with a capture of a live library**, however
harmless the visible records look. If a screenshot needs updating, rebuild the
fixture and re-capture.

## How to re-capture

The harness is the `verify` skill
([`.claude/skills/verify/SKILL.md`](../../.claude/skills/verify/SKILL.md)), which
exists for exactly this: a grimoire instance with `GRIMOIRE_HOME` pointed at a
scratch directory and `grimoire.openrouter.API_URL` pointed at a local mock
replaying `backend/tests/fixtures/llm/campaign_flow.json`.

1. Launch the isolated backend on **8199** and vite on **5199**. Never 8173 /
   5173 — that is where someone's real instance lives, and the whole point of
   the exercise is to stay away from it.
2. **Confirm the isolation before anything else**: `curl
   http://127.0.0.1:8199/api/worlds` must return `[]` on first run. If it
   returns worlds, you are pointed at a real library. Stop.
3. Seed a world, a few characters, locations, lore, greetings, a campaign, a
   PC and one scene through the API. All names invented.
4. Play a turn or two against the mock so the transcript has body.
5. Drive Chromium with Playwright at **1440×900** and screenshot each page.

## Keep them small

These are committed binaries: PNGs do not delta-compress, so every byte lands
in every clone, forever, including the bytes of the version you replaced.

Capture (or downscale to) **1440×900 at 1×** and save palette-quantized —
these are flat UI screenshots with few distinct colours, so 256 of them is
visually lossless. That is the difference between ~790 KB and ~255 KB for the
four images here. The README links each thumbnail to the file itself, so a
reader who wants detail clicks through; nobody needs a 2× asset inline.

## The files

| File | Page |
|---|---|
| `scene.png` | a scene mid-play — transcript, cast rail, response controls |
| `world-overview.png` | a world's overview: record counts and the setup checklist |
| `character-editor.png` | a character's world record — the list/detail pattern |
| `campaigns.png` | the campaigns home |

Anything added here must be referenced by one of the documents
`backend/tests/test_docs_guard.py` maintains, or that guard fails it as an
orphan — an image nobody links to is one nobody notices going stale, and in
this repo that is a privacy question rather than a tidiness one.
