# Brutalist Codex Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recreate the "Brutalist Codex" design handoff (light warm paper, hard ink rules, offset block shadows, vermilion accent) across every screen, with a 3-theme system (Codex / Manuscript / Astral) driven by semantic CSS variables.

**Architecture:** Theme-system-first, then screen-by-screen restyle. New theme token files carry the full handoff variable set plus temporary aliases for the old token names (`--fg`, `--font-display`, `--font-body`, `--radius`) so unrestyled screens keep rendering during the migration; the final task removes the aliases. Backend gains four small deltas (default theme, speaker on messages, transcript label config, campaign scene counts).

**Tech Stack:** React 18 + TypeScript + react-router + vitest (frontend), FastAPI + pytest (backend), `@fontsource` self-hosted fonts.

**Spec:** `docs/superpowers/specs/2026-07-01-codex-redesign-design.md`
**Design source of truth:** `design_handoff_codex_redesign/README.md` (values) and `design_handoff_codex_redesign/Grimoire.dc.html` (live prototype; `this.themeDefs` at line ~640). Screenshots in `design_handoff_codex_redesign/screenshots/`.

## Global Constraints

- **Zero border radius anywhere** in the new CSS. No `border-radius` declarations except `0` where overriding a leftover is unavoidable.
- **Components never reference raw hex** — only `var(--…)` tokens.
- **Buttons must set `color` explicitly** (prototype bug class: `<button>` doesn't inherit color).
- **Never animate opacity from 0** in view transitions — use `translateY(6px)→0` only.
- Vermilion accent `#c0392b`, ink `#1a1712`, paper `#e7e1d4` come only via tokens.
- Run frontend tests **from `frontend/`**: `npx vitest run` and `npx tsc -b`. Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` from the repo root — **the venv lives in the main checkout** `C:\Users\charl\github\grimoire\backend\.venv`, not in this worktree; use `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Working directory: the worktree `C:\Users\charl\github\grimoire\.worktrees\codex-redesign`, branch `codex-redesign`.
- All arrows/glyphs are text characters (→ ▸ ‹ ↗ ⌦ ✦ ✕ ✓), no icon font.
- Commit after every task (message prefixes below).

---

### Task 1: Theme files — codex / manuscript / astral

**Files:**
- Create: `frontend/src/theme/themes/codex.ts`, `frontend/src/theme/themes/manuscript.ts`, `frontend/src/theme/themes/astral.ts`
- Create: `frontend/src/theme/themes/tokens.test.ts`
- Modify: `frontend/src/theme/themes/index.ts`
- Delete: `frontend/src/theme/themes/occult.ts`, `frontend/src/theme/themes/terminal.ts`, `frontend/src/theme/themes/ink.ts`
- Modify: `frontend/src/theme/ThemeProvider.test.tsx` (theme names in assertions)

**Interfaces:**
- Consumes: existing `Theme` type (`frontend/src/theme/types.ts` — unchanged).
- Produces: `DEFAULT_THEME === "codex"`; `themeList` = `[codex, manuscript, astral]` with labels `CODEX` / `MANUSCRIPT` / `ASTRAL`; every theme's `tokens` contains the 34 new variables **plus** compat aliases `--fg`, `--font-display`, `--font-body`, `--radius`, `--mono` (removed in Task 15).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/theme/themes/tokens.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_THEME, themeList, resolveTheme } from "./index";

const REQUIRED = [
  "--bg", "--surface", "--panel", "--panel2", "--ink", "--subtle", "--muted",
  "--rule", "--rule-soft", "--track", "--chrome", "--chrome-text",
  "--chrome-muted", "--chrome-rule", "--accent", "--on-accent", "--quote",
  "--page", "--page-ink", "--page-muted", "--banner-bg", "--banner-ink",
  "--disabled", "--rw", "--rw2", "--rw3", "--sh2", "--sh3", "--sh4", "--sh5",
  "--fd", "--fb", "--fm", "--fx", "--glow",
];

describe("theme tokens", () => {
  it("ships exactly codex, manuscript, astral", () => {
    expect(themeList.map((t) => t.name)).toEqual(["codex", "manuscript", "astral"]);
  });

  it("defaults to codex", () => {
    expect(DEFAULT_THEME).toBe("codex");
  });

  it("falls back to codex for old saved theme names", () => {
    expect(resolveTheme("occult").name).toBe("codex");
    expect(resolveTheme("terminal").name).toBe("codex");
  });

  it("every theme defines the full variable set", () => {
    for (const t of themeList) {
      for (const v of REQUIRED) {
        expect(t.tokens[v], `${t.name} missing ${v}`).toBeTruthy();
      }
    }
  });

  it("codex is the hard-edged reference", () => {
    const codex = resolveTheme("codex").tokens;
    expect(codex["--accent"]).toBe("#c0392b");
    expect(codex["--rw"]).toBe("2px");
    expect(codex["--sh4"]).toBe("4px 4px 0 rgba(26,23,18,.9)");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/theme/themes/tokens.test.ts`
Expected: FAIL — `Cannot find module './codex'` errors will not occur yet, but assertions fail (`themeList` names are `["occult","terminal","ink"]`, `DEFAULT_THEME` is `"occult"`).

- [ ] **Step 3: Write the three theme files**

Create `frontend/src/theme/themes/codex.ts` (values verbatim from `Grimoire.dc.html` `themeDefs.codex`):

```ts
import type { Theme } from "../types";

const codex: Theme = {
  name: "codex",
  label: "CODEX",
  tokens: {
    "--bg": "#e7e1d4", "--surface": "#efe9dc", "--panel": "#ded7c7",
    "--panel2": "#e2dccb", "--ink": "#1a1712", "--subtle": "#4b4335",
    "--muted": "#7c7361", "--rule": "#1a1712", "--rule-soft": "rgba(26,23,18,.25)",
    "--track": "rgba(26,23,18,.13)", "--chrome": "#1a1712",
    "--chrome-text": "#e7e1d4", "--chrome-muted": "#8f8674",
    "--chrome-rule": "#4b4335", "--accent": "#c0392b", "--on-accent": "#ffffff",
    "--quote": "#c0392b", "--page": "#efe9dc", "--page-ink": "#1a1712",
    "--page-muted": "#7c7361", "--banner-bg": "#f2e2dd", "--banner-ink": "#8a2a20",
    "--disabled": "#b8b0a0",
    "--rw": "2px", "--rw2": "1.5px", "--rw3": "3px",
    "--sh2": "2px 2px 0 #1a1712", "--sh3": "3px 3px 0 #1a1712",
    "--sh4": "4px 4px 0 rgba(26,23,18,.9)", "--sh5": "5px 5px 0 rgba(26,23,18,.85)",
    "--fd": "'Big Shoulders Display',sans-serif",
    "--fb": "'Newsreader',Georgia,serif",
    "--fm": "'JetBrains Mono',monospace",
    "--fx": "none", "--glow": "none",
    // compat aliases — removed in the cleanup task once no CSS references them
    "--fg": "var(--ink)", "--font-display": "var(--fd)",
    "--font-body": "var(--fb)", "--mono": "var(--fm)", "--radius": "0px",
  },
};
export default codex;
```

Create `frontend/src/theme/themes/manuscript.ts`:

```ts
import type { Theme } from "../types";

const manuscript: Theme = {
  name: "manuscript",
  label: "MANUSCRIPT",
  tokens: {
    "--bg": "#14100b", "--surface": "#1c1610", "--panel": "#0f0b07",
    "--panel2": "#241c12", "--ink": "#e8dcc6", "--subtle": "#c9bb98",
    "--muted": "#8a7a5c", "--rule": "rgba(200,164,77,.5)",
    "--rule-soft": "rgba(200,164,77,.28)", "--track": "rgba(200,164,77,.16)",
    "--chrome": "#0f0b07", "--chrome-text": "#c8a44d", "--chrome-muted": "#8a7a5c",
    "--chrome-rule": "rgba(200,164,77,.35)", "--accent": "#c8a44d",
    "--on-accent": "#1a1206", "--quote": "#a8342a",
    "--page": "linear-gradient(180deg,#efe4c9,#e8dcc0)", "--page-ink": "#2c2318",
    "--page-muted": "#9a7d3f", "--banner-bg": "#2a1712", "--banner-ink": "#e0a494",
    "--disabled": "#57503f",
    "--rw": "1px", "--rw2": "1px", "--rw3": "1px",
    "--sh2": "0 2px 8px rgba(0,0,0,.5)", "--sh3": "0 3px 12px rgba(0,0,0,.5)",
    "--sh4": "0 5px 18px rgba(0,0,0,.55)", "--sh5": "0 8px 24px rgba(0,0,0,.6)",
    "--fd": "'Cormorant Garamond',serif",
    "--fb": "'EB Garamond',Georgia,serif",
    "--fm": "'EB Garamond',serif",
    "--fx": "radial-gradient(120% 90% at 50% -10%,rgba(200,164,77,.10),transparent 60%)",
    "--glow": "none",
    "--fg": "var(--ink)", "--font-display": "var(--fd)",
    "--font-body": "var(--fb)", "--mono": "var(--fm)", "--radius": "0px",
  },
};
export default manuscript;
```

Create `frontend/src/theme/themes/astral.ts`:

```ts
import type { Theme } from "../types";

const astral: Theme = {
  name: "astral",
  label: "ASTRAL",
  tokens: {
    "--bg": "#08070f", "--surface": "#0e0d18", "--panel": "#0a0912",
    "--panel2": "#131120", "--ink": "#d7d9ee", "--subtle": "#9a9cc0",
    "--muted": "#7d80a8", "--rule": "rgba(120,190,220,.42)",
    "--rule-soft": "rgba(120,190,220,.22)", "--track": "rgba(120,190,220,.12)",
    "--chrome": "#0a0912", "--chrome-text": "#6fe0da", "--chrome-muted": "#5b5e82",
    "--chrome-rule": "rgba(120,190,220,.28)", "--accent": "#6fe0da",
    "--on-accent": "#041014", "--quote": "#6fe0da", "--page": "#0b0a15",
    "--page-ink": "#d7d9ee", "--page-muted": "#7d80a8", "--banner-bg": "#241021",
    "--banner-ink": "#e77fce", "--disabled": "#3a3d5c",
    "--rw": "1px", "--rw2": "1px", "--rw3": "1px",
    "--sh2": "0 0 10px rgba(111,224,218,.18)", "--sh3": "0 0 14px rgba(111,224,218,.2)",
    "--sh4": "0 0 20px rgba(111,224,218,.22)", "--sh5": "0 0 26px rgba(111,224,218,.25)",
    "--fd": "'Cinzel',serif",
    "--fb": "'Spectral',Georgia,serif",
    "--fm": "'Space Mono',monospace",
    "--fx": "radial-gradient(1px 1px at 20% 30%,rgba(120,200,220,.5),transparent),radial-gradient(1px 1px at 70% 20%,rgba(200,120,220,.4),transparent),radial-gradient(1px 1px at 45% 70%,rgba(120,200,220,.35),transparent),radial-gradient(1px 1px at 85% 60%,rgba(160,160,240,.4),transparent),radial-gradient(90% 60% at 50% -5%,rgba(90,120,200,.14),transparent 70%)",
    "--glow": "0 0 12px rgba(79,214,208,.45)",
    "--fg": "var(--ink)", "--font-display": "var(--fd)",
    "--font-body": "var(--fb)", "--mono": "var(--fm)", "--radius": "0px",
  },
};
export default astral;
```

Replace the body of `frontend/src/theme/themes/index.ts`:

```ts
import type { Theme } from "../types";
import codex from "./codex";
import manuscript from "./manuscript";
import astral from "./astral";

export const DEFAULT_THEME = "codex";

// Register a new theme by adding its import to this array.
const all: Theme[] = [codex, manuscript, astral];

export const themes: Record<string, Theme> = Object.fromEntries(
  all.map((t) => [t.name, t]),
);

export const themeList = all;

export function resolveTheme(name: string): Theme {
  return themes[name] ?? themes[DEFAULT_THEME];
}
```

Delete `occult.ts`, `terminal.ts`, `ink.ts`.

- [ ] **Step 4: Fix tests that referenced old theme names**

Search: `grep -rn "occult\|terminal\|Occult\|ink" frontend/src --include="*.test.*"`. Update each hit — in `ThemeProvider.test.tsx` and `ConfigView.test.tsx`, replace old names/labels with `codex` / `manuscript` / `astral` and labels `CODEX` / `MANUSCRIPT` / `ASTRAL` (keep the tests' structure; only the fixture names change). If a test asserts a token value (e.g. `--bg`), use the codex value `#e7e1d4`.

- [ ] **Step 5: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/theme && npx tsc -b`
Expected: PASS.

Then the full suite: `npx vitest run`
Expected: PASS (theme names are the only cross-cutting change; fix any missed fixture).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/theme
git commit -m "feat(theme): replace occult/terminal/ink with codex/manuscript/astral token sets"
```

---

### Task 2: Self-hosted fonts + base stylesheet foundation

**Files:**
- Modify: `frontend/package.json` (via npm install)
- Modify: `frontend/src/main.tsx` (font imports)
- Modify: `frontend/src/index.css` (replace `:root` block and base styles; add shared primitives)

**Interfaces:**
- Produces CSS classes later tasks use: `.page`, `.page-narrow`, `.page-head`, `.page-h1`, `.count-label`, `.back-link`, `.btn-accent`, `.btn-chrome`, `.btn-outline`, `.joined`, `.view-anim`, `.chip` (+ `.chip.solid`, `.chip.on`), `.section-label`.

- [ ] **Step 1: Install fonts**

Run from `frontend/`:

```bash
npm install @fontsource/big-shoulders-display @fontsource-variable/newsreader @fontsource/jetbrains-mono @fontsource/cormorant-garamond @fontsource/eb-garamond @fontsource/cinzel @fontsource/spectral @fontsource/space-mono
```

(If `@fontsource-variable/newsreader` does not resolve, use `@fontsource/newsreader` and import weights 400/500/600 + italics.)

- [ ] **Step 2: Import fonts in `frontend/src/main.tsx`**

Add at the top, before `./index.css`:

```ts
import "@fontsource/big-shoulders-display/500.css";
import "@fontsource/big-shoulders-display/700.css";
import "@fontsource/big-shoulders-display/800.css";
import "@fontsource/big-shoulders-display/900.css";
import "@fontsource-variable/newsreader";
import "@fontsource-variable/newsreader/opsz-italic.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/700.css";
import "@fontsource/cormorant-garamond/600.css";
import "@fontsource/cormorant-garamond/700.css";
import "@fontsource/eb-garamond/400.css";
import "@fontsource/eb-garamond/400-italic.css";
import "@fontsource/eb-garamond/600.css";
import "@fontsource/cinzel/600.css";
import "@fontsource/cinzel/700.css";
import "@fontsource/cinzel/900.css";
import "@fontsource/spectral/400.css";
import "@fontsource/spectral/400-italic.css";
import "@fontsource/spectral/500.css";
import "@fontsource/space-mono/400.css";
import "@fontsource/space-mono/700.css";
```

- [ ] **Step 3: Replace the base of `frontend/src/index.css`**

Replace lines 1–18 (`:root { … }` through `body { … }`) with:

```css
/* Tokens are applied to :root by ThemeProvider — no hardcoded :root values.
   Fallbacks below match the codex theme for the pre-hydration frame. */
* { box-sizing: border-box; }
html, body, #root { height: 100%; }
body {
  margin: 0;
  background: var(--bg, #e7e1d4);
  color: var(--ink, #1a1712);
  font-family: var(--fb, Georgia, serif);
}
/* per-theme background overlay (Manuscript wash / Astral starfield) */
body::before {
  content: ""; position: fixed; inset: 0; z-index: 0;
  pointer-events: none; background: var(--fx, none);
}
#root { position: relative; z-index: 1; display: flex; flex-direction: column; }

/* ---- view transition (never animate opacity from 0) ---- */
@keyframes view-in { from { transform: translateY(6px); } to { transform: translateY(0); } }
.view-anim { animation: view-in 0.3s ease; }

/* ---- page scaffold ---- */
.page { width: 100%; max-width: 940px; margin: 0 auto; padding: 44px 56px; }
.page-narrow { max-width: 680px; }
.page-head {
  display: flex; align-items: flex-end; justify-content: space-between; gap: 16px;
  border-bottom: var(--rw3) solid var(--rule); padding-bottom: 14px;
}
.page-h1 {
  font-family: var(--fd); font-weight: 900; font-size: 64px; line-height: 0.86;
  margin: 0; text-transform: uppercase; letter-spacing: 0.01em;
  text-shadow: var(--glow);
}
.page-narrow .page-h1 { font-size: 52px; }
.count-label {
  font-family: var(--fm); font-size: 11px; color: var(--muted);
  letter-spacing: 0.1em; text-transform: uppercase; margin: 10px 0 0;
}
.back-link {
  display: inline-block; margin-bottom: 14px; font-family: var(--fm);
  font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--muted); text-decoration: none; background: none; border: none;
  padding: 0; cursor: pointer;
}
.back-link:hover { color: var(--accent); }

/* ---- buttons ---- */
.btn-accent, .btn-chrome, .btn-outline {
  font-family: var(--fm); font-weight: 700; font-size: 12px;
  letter-spacing: 0.1em; text-transform: uppercase;
  padding: 11px 20px; cursor: pointer; border: var(--rw) solid var(--rule);
}
.btn-accent { background: var(--accent); color: var(--on-accent); box-shadow: var(--sh4); }
.btn-accent:disabled { background: var(--disabled); color: var(--bg); box-shadow: none; cursor: default; }
.btn-chrome { background: var(--chrome); color: var(--chrome-text); }
.btn-outline { background: transparent; color: var(--ink); }
.btn-outline:hover, .btn-chrome:hover { box-shadow: var(--sh2); }

/* ---- joined control (input + button sharing one shadow) ---- */
.joined { display: flex; box-shadow: var(--sh3); }
.joined > input, .joined > textarea {
  flex: 1; min-width: 0; background: var(--surface); color: var(--ink);
  border: var(--rw) solid var(--rule); border-right: none; padding: 10px 12px;
  font-family: var(--fb); font-size: 15px;
}
.joined > button { flex: none; box-shadow: none; }

/* ---- section label (character detail, config) ---- */
.section-label {
  font-family: var(--fm); font-size: 10px; color: var(--accent);
  letter-spacing: 0.16em; text-transform: uppercase;
  border-bottom: var(--rw2) solid var(--rule); padding-bottom: 6px;
  margin: 26px 0 10px;
}
```

Also update the generic input/field styling in the same file — in the `.field input…`, `.picker input…`, `.config input…`, `.inputbar textarea`, `.wizard-location input…` rules, change `color: var(--fg)` → `color: var(--ink)`, `font-family: var(--font-body)` → `font-family: var(--fb)`, and set `font-size: 17px` on `.field input[type="text"], .field textarea, .field select`. Leave `border-radius: var(--radius)` occurrences in place (alias `0px` neutralizes them; Task 15 deletes them).

Update the chip rule (`.chip` at ~line 123) to:

```css
.chip {
  font-family: var(--fm); font-size: 10px; letter-spacing: 0.08em;
  text-transform: uppercase; background: transparent; color: var(--ink);
  border: var(--rw2) solid var(--rule); padding: 4px 10px; cursor: pointer;
}
.chip.on { background: var(--accent); color: var(--on-accent); border-color: var(--rule); }
.chip.solid { background: var(--chrome); color: var(--chrome-text); border-color: var(--chrome); }
```

- [ ] **Step 4: Verify build + tests**

Run from `frontend/`: `npx tsc -b && npx vitest run && npm run build`
Expected: all PASS; build succeeds (fonts bundle). If a `@fontsource` subpath fails to resolve, check the package's `files` listing under `frontend/node_modules/@fontsource/<pkg>` and adjust the import path (weights are `<weight>.css`, italics `<weight>-italic.css`).

- [ ] **Step 5: Visual smoke check**

Run `npm run dev` from `frontend/` (backend not required for this check), open the app, confirm: paper background `#e7e1d4`, serif body text, no console font errors. Stop the dev server.

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/main.tsx frontend/src/index.css
git commit -m "feat(theme): self-hosted fonts and codex base stylesheet primitives"
```

---

### Task 3: Backend deltas — default theme, labels config, message speaker, campaign counts

**Files:**
- Modify: `backend/src/grimoire/store/config.py`
- Modify: `backend/src/grimoire/store/scenes.py`
- Modify: `backend/src/grimoire/routes.py` (config response models + campaigns listing)
- Test: `backend/tests/test_config_store.py`, `backend/tests/test_scenes_store.py` (or the existing scene-store test file — find with `grep -rl "append_message" backend/tests`), `backend/tests/test_api_campaigns.py` (or equivalent — find with `grep -rl "list_campaigns\|/api/campaigns" backend/tests`)

**Interfaces:**
- Produces: config keys `user_label` (default `"You"`), `assistant_label` (default `"Grimoire"`); `DEFAULT_THEME = "codex"`; message dicts may carry `"speaker": str`; `GET /api/campaigns` items gain `scenes: int` and `last_scene: str`.
- Frontend consumers (Tasks 5, 11, 13) rely on exactly these field names.

- [ ] **Step 1: Write failing config tests**

In `backend/tests/test_config_store.py`, update the two `assert cfg["theme"] == "occult"` lines to `== "codex"`, and add:

```python
def test_label_defaults_and_write(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import config as config_store

    cfg = config_store.read_config()
    assert cfg["user_label"] == "You"
    assert cfg["assistant_label"] == "Grimoire"

    cfg = config_store.write_config(user_label="Kestrel", assistant_label="Narrator")
    assert cfg["user_label"] == "Kestrel"
    assert config_store.read_config()["assistant_label"] == "Narrator"
```

(Match the import/monkeypatch style already used in that file — follow its existing fixtures.)

- [ ] **Step 2: Write failing scene-speaker test**

Find the scene store test file: `grep -rl "append_message" backend/tests`. Add:

```python
def test_message_speaker_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import campaigns, scenes, worlds

    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("C", wid)
    sid = scenes.create_scene(cid, "S")

    scenes.append_message(cid, sid, "user", "I open the door.")
    scenes.append_message(cid, sid, "assistant", "“At last,” she says.", speaker="Seraphine Vale")

    msgs = scenes.read_scene(cid, sid)["messages"]
    assert "speaker" not in msgs[0]
    assert msgs[1]["speaker"] == "Seraphine Vale"
    assert msgs[1]["content"] == "“At last,” she says."

    # edit_message must preserve the speaker
    scenes.edit_message(cid, sid, 1, "Edited.")
    msgs = scenes.read_scene(cid, sid)["messages"]
    assert msgs[1]["speaker"] == "Seraphine Vale"
```

(Adapt `create_world`/`create_campaign` call signatures to the helpers that file already uses.)

- [ ] **Step 3: Write failing campaign-list test**

Find the campaigns API/store test file (`grep -rl "list_campaigns" backend/tests`). Add:

```python
def test_list_campaigns_scene_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import campaigns, scenes, worlds

    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("C", wid)
    scenes.create_scene(cid, "First Light")
    scenes.create_scene(cid, "The Salt Road")

    from grimoire import routes  # response assembled in routes
    listing = [c for c in routes.list_campaigns() if c["id"] == cid]
    assert listing[0]["scenes"] == 2
    assert listing[0]["last_scene"] in ("First Light", "The Salt Road")
```

(If the project tests routes through FastAPI's `TestClient`, use the same pattern that file uses: `client.get("/api/campaigns")` and assert on the JSON. `last_scene` is the title of the most recently updated scene, `""` when there are none.)

- [ ] **Step 4: Run tests to verify they fail**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q -k "label or speaker or scene_counts or config"`
Expected: FAIL (KeyError `user_label`, TypeError `speaker`, KeyError `scenes`), plus the two updated `codex` assertions fail.

- [ ] **Step 5: Implement**

`backend/src/grimoire/store/config.py`:

```python
DEFAULT_THEME = "codex"
DEFAULT_USER_LABEL = "You"
DEFAULT_ASSISTANT_LABEL = "Grimoire"
_CONFIG_KEYS = ("openrouter_key", "model", "theme", "context_scan_depth", "system_prompt",
                "quote_color", "recap_depth", "user_label", "assistant_label")
```

and in `read_config` defaults add:

```python
                "user_label": DEFAULT_USER_LABEL, "assistant_label": DEFAULT_ASSISTANT_LABEL,
```

`backend/src/grimoire/store/scenes.py` — extend the marker format so a speaker rides in the label: `**Grimoire (Seraphine Vale):**`.

```python
_MARKER = re.compile(r"^\*\*(You|Grimoire)(?: \(([^)\n]+)\))?:\*\*[ ]?", re.MULTILINE)
```

`_parse_messages`:

```python
def _parse_messages(body: str) -> list[dict]:
    matches = list(_MARKER.finditer(body))
    messages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        msg = {"role": LABEL_TO_ROLE[m.group(1)], "content": body[start:end].strip()}
        if m.group(2):
            msg["speaker"] = m.group(2)
        messages.append(msg)
    return messages
```

`append_message` gains a keyword arg and `_serialize_messages` mirrors it:

```python
def _label(role: str, speaker: str | None) -> str:
    base = ROLE_TO_LABEL[role]
    return f"{base} ({speaker})" if speaker else base


def append_message(cid: str, sid: str, role: str, content: str, speaker: str | None = None) -> None:
    ...
    block = f"**{_label(role, speaker)}:** {content.strip()}\n"
    ...


def _serialize_messages(messages: list[dict]) -> str:
    ...
    for m in messages:
        block = f"**{_label(m['role'], m.get('speaker'))}:** {m['content'].strip()}\n"
    ...
```

(Only the `block =` lines and the signature change; keep the surrounding code as-is. Speaker names containing `)` are not supported by the format — acceptable; nothing writes speakers yet.)

`backend/src/grimoire/routes.py` — in the `/api/campaigns` list handler, enrich each campaign:

```python
from .store import scenes as scenes_store  # if not already imported

# inside the list handler, replace `return campaigns.list_campaigns()` with:
out = []
for c in campaigns.list_campaigns():
    scene_list = scenes_store.list_scenes(c["id"])
    out.append({**c, "scenes": len(scene_list),
                "last_scene": scene_list[0]["title"] if scene_list else ""})
return out
```

Also update the config route's response/accepted fields if it whitelists keys (check the `PUT /api/config` handler — add `user_label`, `assistant_label` wherever `quote_color` appears).

- [ ] **Step 6: Run the backend suite**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS (fix any test that asserted the old config key tuple or campaign list shape).

- [ ] **Step 7: Update frontend types to match**

In `frontend/src/api/client.ts`:

```ts
export type CampaignMeta = {
  id: string;
  name: string;
  world: string;
  created: string;
  updated: string;
  scenes: number;
  last_scene: string;
};
export type Message = { role: "user" | "assistant"; content: string; speaker?: string };
export type Config = {
  model: string; theme: string; key_set: boolean; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string;
};
```

and widen `putConfig`'s field type with `user_label: string; assistant_label: string`.

Run from `frontend/`: `npx tsc -b && npx vitest run`
Expected: PASS — fix any test fixture that builds a `Config`/`CampaignMeta` object (add the new fields with defaults `"You"`/`"Grimoire"`/`0`/`""`).

- [ ] **Step 8: Commit**

```bash
git add backend frontend/src/api/client.ts frontend/src -A
git commit -m "feat(backend): codex default theme, transcript labels config, message speaker, campaign scene counts"
```

---

### Task 4: Global chrome top bar

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/index.css` (`.topbar` section)
- Test: `frontend/src/App.test.tsx` (create if absent)

**Interfaces:**
- Consumes: `keySet` state already in App; `frontend/public/grimoire-128.png` (exists — verify with `ls frontend/public`).
- Produces: the `.topbar` DOM later tasks assume exists above their routes.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/App.test.tsx` (mirror the mocking style of `ConfigView.test.tsx` — mock `../api/client` the same way):

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { api } from "./api/client";

vi.mock("./api/client", () => ({
  api: {
    getConfig: vi.fn().mockResolvedValue({
      model: "m", theme: "codex", key_set: true, system_prompt: "",
      quote_color: "off", user_label: "You", assistant_label: "Grimoire",
    }),
    listCampaigns: vi.fn().mockResolvedValue([]),
    listWorlds: vi.fn().mockResolvedValue([]),
  },
}));

it("renders the chrome top bar with brand, nav, and connection status", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText("GRIMOIRE")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /campaigns/i })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /worlds/i })).toBeInTheDocument();
  expect(screen.getByText(/openrouter · connected/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /config/i })).toBeInTheDocument();
});
```

Note: `App` renders `<Routes>` — the default route mounts `CampaignsView`, hence the `listCampaigns`/`listWorlds` mocks. If `App.tsx` uses `BrowserRouter` internally, wrap accordingly (check `main.tsx` for where the router lives).

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/App.test.tsx`
Expected: FAIL — "GRIMOIRE" not found (current brand text is lowercase "✦ grimoire") and no status line.

- [ ] **Step 3: Implement the top bar**

Replace the `.topbar` JSX in `frontend/src/App.tsx`:

```tsx
import { NavLink, Route, Routes } from "react-router-dom";
// (drop the plain Link import if now unused)

      <header className="topbar">
        <NavLink to="/" className="brand">
          <img src="/grimoire-128.png" alt="" width={30} height={30} />
          <span>✦ GRIMOIRE</span>
        </NavLink>
        <nav>
          <NavLink to="/" end className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Campaigns
          </NavLink>
          <NavLink to="/worlds" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Worlds
          </NavLink>
        </nav>
        <div className="topbar-right">
          <span className="status">
            <span className="dot">●</span> OPENROUTER · {keySet ? "CONNECTED" : "NO KEY"}
          </span>
          <span className="divider" />
          <NavLink to="/config" className={({ isActive }) => "config-link" + (isActive ? " active" : "")}>
            Config
          </NavLink>
        </div>
      </header>
```

Campaign routes still render below inside `<Routes>` — untouched here.

Replace the `.topbar` CSS block in `index.css` (old lines 19–26):

```css
/* ---- chrome top bar ---- */
.topbar { display: flex; align-items: stretch; background: var(--chrome); flex: none; }
.topbar .brand {
  display: flex; align-items: center; gap: 10px;
  font-family: var(--fd); font-weight: 900; font-size: 24px; letter-spacing: 0.03em;
  padding: 13px 22px; color: var(--chrome-text); text-decoration: none;
  border-right: var(--rw) solid var(--chrome-text); text-shadow: var(--glow);
  text-transform: uppercase;
}
.topbar .brand img { display: block; }
.topbar nav { display: flex; }
.topbar .nav-btn {
  display: flex; align-items: center; padding: 0 20px;
  font-family: var(--fm); font-weight: 700; font-size: 12px; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--chrome-text); text-decoration: none;
  border-right: var(--rw) solid var(--chrome-text);
}
.topbar .nav-btn.active { background: var(--accent); color: var(--on-accent); }
.topbar-right {
  margin-left: auto; display: flex; align-items: center; gap: 14px; padding: 0 18px;
  font-family: var(--fm); font-size: 10px; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--chrome-muted);
}
.topbar-right .dot { color: var(--accent); }
.topbar-right .divider { width: 1px; align-self: stretch; margin: 10px 0; background: var(--chrome-rule); }
.topbar-right .config-link { color: var(--chrome-muted); text-decoration: none; }
.topbar-right .config-link.active { color: var(--chrome-text); text-decoration: underline; }
```

Also change `.layout { … height: calc(100vh - 49px); }` to `.layout { display: flex; flex: 1; min-height: 0; }` (the topbar height is no longer 49px; `#root` is a flex column from Task 2, so the layout can flex-fill instead of hardcoding).

- [ ] **Step 4: Run tests**

Run: `npx vitest run && npx tsc -b`
Expected: PASS. If other tests queried the old topbar text ("✦ grimoire" lowercase), update them.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/index.css
git commit -m "feat(chrome): brutalist codex top bar with brand block, nav cells, connection status"
```

---

### Task 5: Campaigns dashboard

**Files:**
- Modify: `frontend/src/routes/CampaignsView.tsx`
- Modify: `frontend/src/index.css` (add `.list-block` section)
- Test: `frontend/src/routes/CampaignsView.test.tsx`

**Interfaces:**
- Consumes: `CampaignMeta.scenes` / `.last_scene` (Task 3), `.page` scaffold + `.btn-accent` (Task 2). World **names** come from joining `campaign.world` (a world id) against `listWorlds()` — the view already fetches both.

- [ ] **Step 1: Update the test**

In `CampaignsView.test.tsx`, update mocked campaigns to include the new fields and assert the new row content (adapt to the file's existing mock style):

```tsx
// mock data
{ id: "c1", name: "Ashes of the Verdigris Crown", world: "w1", created: "", updated: "",
  scenes: 4, last_scene: "Verdigris & Ash" }
// mocked listWorlds returns [{ id: "w1", name: "Saltmarch", created: "", updated: "", counts: {} }]
```

New assertions:

```tsx
expect(await screen.findByText("Ashes of the Verdigris Crown")).toBeInTheDocument();
expect(screen.getByText(/WORLD ▸ Saltmarch · 4 SCENES · LAST: Verdigris & Ash/i)).toBeInTheDocument();
expect(screen.getByRole("heading", { name: /campaigns/i })).toBeInTheDocument();
expect(screen.getByRole("button", { name: /new campaign/i })).toBeInTheDocument();
```

Keep (adapting selectors) the existing rename/delete coverage: the row still exposes rename and delete controls.

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run src/routes/CampaignsView.test.tsx` — Expected: FAIL.

- [ ] **Step 3: Implement**

Replace the returned JSX of `CampaignsView.tsx` (keep the state/handlers; add a `renaming` state replacing EditableRow's internal one):

```tsx
export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);

  useEffect(() => {
    api.listCampaigns().then(setCampaigns);
    api.listWorlds().then(setWorlds);
  }, []);

  const worldName = (id: string) => worlds.find((w) => w.id === id)?.name ?? id;

  async function rename() {
    if (!renaming) return;
    await api.renameCampaign(renaming.id, renaming.name);
    setRenaming(null);
    setCampaigns(await api.listCampaigns());
  }

  async function remove(c: CampaignMeta) {
    if (!window.confirm(`Delete '${c.name}'?`)) return;
    await api.deleteCampaign(c.id);
    setCampaigns(await api.listCampaigns());
  }

  return (
    <div className="page view-anim">
      <div className="page-head">
        <h1 className="page-h1">Campaigns</h1>
        <button className="btn-accent" onClick={() => navigate("/campaigns/new")} disabled={worlds.length === 0}>
          + New Campaign
        </button>
      </div>
      <div className="count-label">
        {campaigns.length} {campaigns.length === 1 ? "campaign" : "campaigns"}
      </div>
      {worlds.length === 0 && (
        <p className="page-note">
          Create a world first in <Link to="/worlds">Worlds</Link>, then start a campaign from it.
        </p>
      )}
      <div className="list-block">
        {campaigns.map((c) => (
          <div className="list-row" key={c.id}>
            {renaming?.id === c.id ? (
              <input
                className="row-rename" aria-label="Rename campaign" autoFocus
                value={renaming.name}
                onChange={(e) => setRenaming({ id: c.id, name: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") rename(); if (e.key === "Escape") setRenaming(null); }}
                onBlur={() => setRenaming(null)}
              />
            ) : (
              <button className="list-row-main" onClick={() => navigate(`/campaigns/${c.id}`)}>
                <span className="list-row-name">{c.name}</span>
                <span className="list-row-meta">
                  WORLD ▸ {worldName(c.world)} · {c.scenes} SCENES{c.last_scene ? ` · LAST: ${c.last_scene}` : ""}
                </span>
              </button>
            )}
            <div className="row-actions">
              <button aria-label={`Rename ${c.name}`} onClick={() => setRenaming({ id: c.id, name: c.name })}>✎</button>
              <button aria-label={`Delete ${c.name}`} onClick={() => remove(c)}>✕</button>
            </div>
            <span className="list-row-arrow" aria-hidden>→</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

(Drop the `EditableRow` import.) Add CSS to `index.css`:

```css
/* ---- dashboard list block ---- */
.list-block { border: var(--rw) solid var(--rule); background: var(--surface); margin-top: 22px; }
.list-row { display: flex; align-items: center; border-bottom: var(--rw2) solid var(--rule-soft); }
.list-row:last-child { border-bottom: none; }
.list-row-main {
  flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px;
  text-align: left; background: none; border: none; cursor: pointer;
  padding: 16px 20px; color: var(--ink);
}
.list-row-name { font-family: var(--fd); font-weight: 700; font-size: 26px; line-height: 1; }
.list-row-meta {
  font-family: var(--fm); font-size: 10.5px; color: var(--muted);
  letter-spacing: 0.06em; text-transform: uppercase;
}
.list-row-arrow { font-family: var(--fd); font-weight: 900; font-size: 22px; color: var(--accent); padding: 0 20px; }
.list-row .row-actions { opacity: 0; }
.list-row:hover .row-actions, .list-row:focus-within .row-actions { opacity: 1; }
.page-note { color: var(--subtle); font-style: italic; }
.page-note a { color: var(--accent); }
```

- [ ] **Step 4: Run tests**

Run: `npx vitest run src/routes/CampaignsView.test.tsx && npx tsc -b` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignsView.tsx frontend/src/routes/CampaignsView.test.tsx frontend/src/index.css
git commit -m "feat(campaigns): codex dashboard header and bordered campaign list"
```

---

### Task 6: Worlds dashboard

**Files:**
- Modify: `frontend/src/routes/WorldsView.tsx`
- Modify: `frontend/src/index.css` (add `.world-grid` section)
- Test: `frontend/src/routes/WorldsView.test.tsx`

**Interfaces:**
- Consumes: `WorldMeta.counts` keys `locations`, `lore`, `characters`, `pcs` (verified in `backend/src/grimoire/store/worlds.py:44`).

- [ ] **Step 1: Update the test**

Mock a world with counts and assert the card footer (adapt to existing mock style):

```tsx
{ id: "w1", name: "Saltmarch", created: "", updated: "",
  counts: { locations: 3, lore: 12, characters: 5, pcs: 1 } }
```

```tsx
expect(await screen.findByText("Saltmarch")).toBeInTheDocument();
expect(screen.getByText(/3 LOCATIONS · 6 CHARACTERS · 12 LORE/i)).toBeInTheDocument();
expect(screen.getByRole("button", { name: /^create$/i })).toBeInTheDocument();
```

(6 CHARACTERS = characters 5 + pcs 1.) Keep rename/delete coverage adapted to the card's action buttons.

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/routes/WorldsView.test.tsx` — FAIL.

- [ ] **Step 3: Implement**

Replace `countLabel` and the JSX in `WorldsView.tsx`:

```tsx
function footerLabel(counts: Record<string, number> | undefined): string {
  const c = counts ?? {};
  const chars = (c.characters ?? 0) + (c.pcs ?? 0);
  return `${c.locations ?? 0} LOCATIONS · ${chars} CHARACTERS · ${c.lore ?? 0} LORE`;
}
```

```tsx
  return (
    <div className="page view-anim">
      <div className="page-head">
        <h1 className="page-h1">Worlds</h1>
        <div className="joined">
          <input
            placeholder="World name…" aria-label="World name"
            value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") create(); }}
          />
          <button className="btn-accent" onClick={create} disabled={!name.trim()}>Create</button>
        </div>
      </div>
      <div className="count-label">{worlds.length} {worlds.length === 1 ? "world" : "worlds"}</div>
      <div className="world-grid">
        {worlds.map((w) => (
          <div className="world-card" key={w.id}>
            <button className="world-card-main" onClick={() => navigate(`/worlds/${w.id}`)}>
              <h3>{w.name}</h3>
              <footer>{footerLabel(w.counts)}</footer>
            </button>
            <div className="row-actions">
              <button aria-label={`Rename ${w.name}`} onClick={() => {
                const next = window.prompt("World name", w.name);
                if (next?.trim()) rename(w.id, next.trim());
              }}>✎</button>
              <button aria-label={`Delete ${w.name}`} onClick={() => remove(w)}>✕</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
```

CSS:

```css
/* ---- world cards ---- */
.world-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 22px; }
.world-card {
  position: relative; background: var(--surface);
  border: var(--rw) solid var(--rule); box-shadow: var(--sh5);
}
.world-card-main {
  display: block; width: 100%; text-align: left; background: none; border: none;
  cursor: pointer; padding: 20px 20px 16px; color: var(--ink);
}
.world-card h3 { font-family: var(--fd); font-weight: 800; font-size: 30px; margin: 0 0 14px; line-height: 1; }
.world-card footer {
  border-top: 1px solid var(--rule-soft); padding-top: 8px;
  font-family: var(--fm); font-size: 10px; color: var(--muted);
  letter-spacing: 0.06em; text-transform: uppercase;
}
.world-card .row-actions { position: absolute; top: 10px; right: 10px; opacity: 0; }
.world-card:hover .row-actions, .world-card:focus-within .row-actions { opacity: 1; }
```

(The handoff card shows an italic blurb between name and footer; `WorldMeta` has no blurb field — omitted rather than faked. Drop the `EditableRow` import.)

- [ ] **Step 4: Run tests** — `npx vitest run src/routes/WorldsView.test.tsx && npx tsc -b` — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/WorldsView.tsx frontend/src/routes/WorldsView.test.tsx frontend/src/index.css
git commit -m "feat(worlds): codex world card grid with joined create control"
```

---

### Task 7: World view shell + editor restyle

**Files:**
- Modify: `frontend/src/routes/WorldView.tsx` (header markup only)
- Modify: `frontend/src/index.css` (tab strip, editor rail, char cards, detail view)
- Modify: `frontend/src/components/CharacterEditor.tsx` (initials avatar fallback only)
- Test: `frontend/src/routes/WorldView.test.tsx` (assert new header), existing editor tests must keep passing

**Interfaces:**
- Consumes: `.page`, `.back-link` (Task 2).
- Produces: `.tabs`/`.tab` styling used by Task 9's world-copy view; `.initials-avatar` used in character grids.

- [ ] **Step 1: Update WorldView header markup**

In `WorldView.tsx`, replace the header block:

```tsx
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      <Link to="/worlds" className="back-link">‹ All Worlds</Link>
      <h1 className="page-h1">{name}</h1>
```

(Tab strip markup is unchanged — CSS carries the redesign. Keep the `TABS` array as is, including Tags.)

- [ ] **Step 2: Restyle the tab strip and editor chrome in `index.css`**

Replace the `.tabs`/`.tab` rules (old lines 97–107):

```css
.tabs { display: flex; border-bottom: var(--rw) solid var(--rule); margin: 22px 0 24px; }
.tab {
  background: transparent; border: none; border-bottom: 3px solid transparent;
  margin-bottom: -2px; padding: 10px 16px; cursor: pointer;
  font-family: var(--fm); font-size: 11px; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--muted);
}
.tab:hover { color: var(--ink); }
.tab.active { color: var(--ink); border-bottom-color: var(--accent); }
```

Replace the char-card rules (old lines 201–210):

```css
.char-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.char-card {
  display: flex; flex-direction: column; gap: 0; padding: 0;
  background: var(--surface); border: var(--rw) solid var(--rule); box-shadow: var(--sh4);
}
.char-card:hover { box-shadow: var(--sh5); }
.char-card-main { display: flex; flex-direction: column; gap: 0; padding: 0; width: 100%; background: transparent; border: none; cursor: pointer; color: var(--ink); font-family: var(--fb); }
.char-card-avatar { width: 100%; aspect-ratio: 1 / 1; object-fit: cover; display: block; background: var(--panel); }
.initials-avatar {
  display: flex; align-items: center; justify-content: center;
  height: 96px; font-family: var(--fd); font-weight: 900; font-size: 40px;
  background: var(--subtle); color: var(--on-accent); text-transform: uppercase;
}
.initials-avatar.pc { background: var(--chrome); color: var(--chrome-text); }
.char-card-name {
  font-family: var(--fd); font-weight: 700; font-size: 21px;
  padding: 12px 14px 4px; text-align: left; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 100%;
}
.char-card-actions { display: flex; gap: 6px; padding: 0 14px 12px; }
```

Update the editor rail + row rules (old lines 71–83, 130–136) — same structure, codex skin:

```css
.row {
  display: flex; align-items: center; gap: 6px; width: 100%;
  border: var(--rw2) solid var(--rule-soft); padding: 8px 10px; margin-bottom: 6px;
  background: var(--surface); color: var(--ink);
}
.row.active { border-color: var(--accent); border-left: 4px solid var(--accent); }
.row-subtitle { display: block; font-family: var(--fm); font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
.row-rename { width: 100%; background: var(--bg); color: var(--ink); border: var(--rw2) solid var(--accent); padding: 3px 6px; font-family: var(--fb); }
```

And the detail-view sidebar heading (old line 148):

```css
.side-section h4 {
  margin: 0 0 6px; font-family: var(--fm); font-size: 10px;
  text-transform: uppercase; letter-spacing: 0.16em; color: var(--muted);
}
```

- [ ] **Step 3: Initials fallback in CharacterEditor**

In `CharacterEditor.tsx`, find the grid card's empty-avatar branch (`char-card-avatar-empty` / “no image” placeholder). Replace it with an initials block:

```tsx
<div className="initials-avatar" aria-hidden>
  {name.split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("")}
</div>
```

(`name` = the card's character name variable in that map callback; PCs' editor `PCEditor.tsx` gets the same fallback with `className="initials-avatar pc"` if it renders a grid — check `grep -n "avatar" frontend/src/components/PCEditor.tsx`; if it has no grid/avatar, skip it.)

- [ ] **Step 4: Update WorldView test**

In `WorldView.test.tsx`, update the heading assertion to the new h1 (`getByRole("heading", { level: 1 })`) and the back-link text `‹ All Worlds`. Run:

`npx vitest run src/routes/WorldView.test.tsx src/components/CharacterEditor.test.tsx src/components/EntityEditor.test.tsx src/components/GreetingEditor.test.tsx`
Expected: PASS after fixture tweaks (the editors' structure is unchanged; only fix tests that queried `char-card-avatar-empty`).

- [ ] **Step 5: Full suite + commit**

Run: `npx vitest run && npx tsc -b` — PASS.

```bash
git add frontend/src
git commit -m "feat(world): codex tab strip, character card grid with initials avatars, editor rail skin"
```

---

### Task 8: Character detail restyle

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx` (detail view markup: section labels, version switcher, greeting blocks)
- Modify: `frontend/src/index.css`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `.section-label` (Task 2), `.initials-avatar` (Task 7).
- Produces: `.segmented` control style (reused nowhere else currently; still generic).

- [ ] **Step 1: Read the detail view**

Read the read-only detail block in `CharacterEditor.tsx` (the `.detail` / `.detail-head` JSX). The redesign keeps its data but adjusts presentation:

- Header: avatar 120×120 (image, or `.initials-avatar detail` fallback), name as `<h2 className="detail-name">`, `BY {creator} · {ROLE}` line in `.detail-byline` (creator/role fields only if present in `CharacterDetail` — check the type in `api/client.ts`; omit missing parts), tag chips unchanged.
- Version switcher (the existing version select/buttons): wrap as `.segmented` — one `<button>` per version, `active` class on the current one, caption `<span className="segmented-caption">Version</span>`.
- Card sections: wherever the detail renders "Description", "Personality", "Scenario", "First message" headings, use `<div className="section-label">…</div>`.
- Alternate greetings list: each greeting becomes `<blockquote className="greeting-quote">…</blockquote>`.

- [ ] **Step 2: Add CSS**

```css
/* ---- character detail ---- */
.detail-name { font-family: var(--fd); font-weight: 900; font-size: 48px; margin: 0; line-height: 0.9; text-transform: uppercase; }
.detail-byline { font-family: var(--fm); font-size: 11px; color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; margin: 8px 0; }
.detail-avatar { width: 120px; height: 120px; object-fit: cover; border: var(--rw) solid var(--rule); box-shadow: var(--sh5); }
.initials-avatar.detail { width: 120px; height: 120px; font-size: 48px; border: var(--rw) solid var(--rule); box-shadow: var(--sh5); }
.detail-head { border-bottom: var(--rw3) solid var(--rule); padding-bottom: 18px; }
.detail-text, .detail-rendered { font-size: 17px; line-height: 1.6; }
.segmented { display: inline-flex; }
.segmented button {
  font-family: var(--fm); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
  background: var(--surface); color: var(--ink); border: var(--rw2) solid var(--rule);
  border-right: none; padding: 6px 12px; cursor: pointer;
}
.segmented button:last-child { border-right: var(--rw2) solid var(--rule); }
.segmented button.active { background: var(--chrome); color: var(--chrome-text); }
.segmented-caption { display: block; font-family: var(--fm); font-size: 9.5px; color: var(--muted); letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 4px; }
.greeting-quote {
  margin: 0 0 12px; padding-left: 15px; border-left: 4px solid var(--accent);
  font-style: italic; font-size: 16.5px; line-height: 1.55;
}
```

Also replace the old `.detail-greeting` rule (line 225) — delete it; `.greeting-quote` supersedes it.

- [ ] **Step 3: Update tests, run**

Adjust `CharacterEditor.test.tsx` selectors that matched removed classes (e.g. `.detail-greeting`). Run:
`npx vitest run src/components/CharacterEditor.test.tsx && npx tsc -b` — PASS. Then full `npx vitest run` — PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat(character): codex detail header, section labels, segmented version switcher, greeting quotes"
```

---

### Task 9: Campaign world-copy route

**Files:**
- Modify: `frontend/src/routes/WorldView.tsx` (accept campaign scope)
- Modify: `frontend/src/App.tsx` (route)
- Modify: `frontend/src/components/EntityEditor.tsx` (scope prop)
- Modify: `frontend/src/index.css` (banner)
- Test: `frontend/src/routes/WorldView.test.tsx`

**Interfaces:**
- Consumes: `api.getCampaign(cid)` → `{ meta: { name, world } }`; `EntityScope` from `api/client.ts`; existing campaign-scoped entity endpoints.
- Produces: route `/campaigns/:cid/world` that Tasks 12–14 link to. `WorldView` gains optional props: `export default function WorldView({ campaign }: { campaign?: boolean })`. `EntityEditor` gains `scope?: EntityScope` (defaults to world scope).

- [ ] **Step 1: Write the failing test**

Add to `WorldView.test.tsx` (following its existing mock pattern; add `getCampaign` to the api mock returning `{ meta: { id: "c1", name: "Ashes of the Verdigris Crown", world: "w1" } }`):

```tsx
it("world-copy mode shows the fork banner and campaign back link", async () => {
  render(
    <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
      <Routes>
        <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(await screen.findByText(/ashes of the verdigris crown \/ world copy/i)).toBeInTheDocument();
  expect(screen.getByText(/campaign copy/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure** — `npx vitest run src/routes/WorldView.test.tsx` — FAIL (no `campaign` prop).

- [ ] **Step 3: Implement**

`WorldView.tsx` top:

```tsx
export default function WorldView({ campaign = false }: { campaign?: boolean }) {
  const { wid: widParam = "", cid = "" } = useParams();
  const navigate = useNavigate();
  const [wid, setWid] = useState(widParam);
  const [campaignName, setCampaignName] = useState("");
  // …existing state…

  useEffect(() => {
    if (campaign) {
      api.getCampaign(cid).then((c) => {
        setCampaignName(c.meta.name);
        setWid(c.meta.world);
        setName(c.meta.name); // placeholder until world loads below
        api.getWorld(c.meta.world).then((w) => setName(w.meta.name)).catch(() => {});
      });
    } else {
      setWid(widParam);
      api.getWorld(widParam).then((w) => setName(w.meta.name)).catch(() => setName(widParam));
    }
  }, [campaign, cid, widParam]);
```

Header:

```tsx
      {campaign ? (
        <>
          <button className="back-link" onClick={() => navigate(`/campaigns/${cid}`)}>
            ‹ {campaignName} / World Copy
          </button>
          <div className="fork-banner">
            ⌦ CAMPAIGN COPY — this world was forked when the campaign began.
            Changes here belong to the campaign; the original world is untouched.
          </div>
        </>
      ) : (
        <Link to="/worlds" className="back-link">‹ All Worlds</Link>
      )}
```

Entity scope threading — in `WorldView.tsx` compute once and pass down:

```tsx
const scope = campaign ? ({ kind: "campaign", id: cid } as const) : ({ kind: "world", id: wid } as const);
// locations / lore tabs:
{tab === "locations" && <EntityEditor wid={wid} scope={scope} kind="locations" onOpenLore={openLore} />}
// lore tab's EntityEditor likewise gets scope={scope}
```

`EntityEditor.tsx` — replace the hardcoded scope (line 17):

```tsx
export function EntityEditor({ wid, kind, scope: scopeProp, nav, onNavConsumed, onOpenOwner, onOpenLore }: {
  wid: string;
  scope?: EntityScope;
  // …existing props…
}) {
  const scope = scopeProp ?? { kind: "world" as const, id: wid };
```

(and add `scope?.id` to the `useCallback`/`useEffect` dep arrays that currently list `wid`). Characters / PCs / Tags / Greetings tabs keep using `wid` (the source world) — characters are not forked (verified: campaign copy-on-create copies entity kinds only, `backend/src/grimoire/store/campaigns.py:79-88`).

`App.tsx` route:

```tsx
<Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
```

Banner CSS:

```css
.fork-banner {
  border: var(--rw) solid var(--accent); background: var(--banner-bg); color: var(--banner-ink);
  font-family: var(--fm); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase;
  padding: 10px 14px; margin-bottom: 18px;
}
```

- [ ] **Step 4: Run tests** — `npx vitest run src/routes/WorldView.test.tsx src/components/EntityEditor.test.tsx && npx tsc -b` — PASS. Full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(world-copy): campaign-scoped world view with fork banner and scene back link"
```

---

### Task 10: Campaign wizard

**Files:**
- Modify: `frontend/src/routes/CampaignWizard.tsx` (step 1 fields + markup classes)
- Modify: `frontend/src/index.css` (stepper)
- Test: `frontend/src/routes/CampaignWizard.test.tsx`

**Interfaces:**
- Consumes: `api.createCampaign(name, world, region?)` (exists); `.btn-accent`, `.page-narrow`.
- Region values: reuse the option values from `components/CalendarConfig.tsx`'s region `<select>` (open it and copy the exact option list — the handoff names US / UK / Canada / Australia / Israel / None).

- [ ] **Step 1: Write the failing test**

Add to `CampaignWizard.test.tsx` (matching its mock style):

```tsx
it("step 1 has calendar and holidays selects and passes region on create", async () => {
  render(<MemoryRouter><CampaignWizard keySet /></MemoryRouter>);
  expect(await screen.findByLabelText(/calendar/i)).toBeInTheDocument();
  const holidays = screen.getByLabelText(/holidays/i);
  fireEvent.change(holidays, { target: { value: "UK" } });
  // …drive through to the step-3 CREATE CAMPAIGN action the way existing tests do…
  expect(api.createCampaign).toHaveBeenCalledWith(expect.any(String), expect.any(String), "UK");
});
```

(Use the exact region option value from CalendarConfig — if its UK value is `"GB"`, assert `"GB"`.)

- [ ] **Step 2: Run to verify failure** — FAIL (no calendar/holidays fields).

- [ ] **Step 3: Implement step 1 + stepper skin**

In `CampaignWizard.tsx` step-1 JSX, after the World select add:

```tsx
          <div className="field-row">
            <div className="field">
              <label htmlFor="wiz-calendar">Calendar</label>
              <select id="wiz-calendar" value="gregorian" onChange={() => {}}>
                <option value="gregorian">Gregorian</option>
              </select>
              <div className="field-caption">More providers to come</div>
            </div>
            <div className="field">
              <label htmlFor="wiz-holidays">Holidays</label>
              <select id="wiz-holidays" value={region} onChange={(e) => setRegion(e.target.value)}>
                {/* copy the exact <option> list from components/CalendarConfig.tsx, plus: */}
                <option value="">None</option>
              </select>
              <div className="field-caption">Regional holiday set</div>
            </div>
          </div>
```

with state `const [region, setRegion] = useState("US");` and the step-3 create call passing it: `api.createCampaign(name, world, region || undefined)`.

Wrap the wizard in the page scaffold: root `<div className="page page-narrow view-anim wizard">`, H1 `<h1 className="page-h1">New Campaign</h1>`, primary buttons get `className="btn-accent"` (NEXT ▸ / CREATE CAMPAIGN), step-4 FINISH ▸ gets `className="btn-chrome" style={{ boxShadow: "var(--sh4)" }}`. Keep all existing handlers.

Replace the stepper CSS (old lines 231–248):

```css
.wizard-steps { display: flex; list-style: none; margin: 18px 0 26px; padding: 0; border: var(--rw) solid var(--rule); background: var(--surface); }
.wizard-step { flex: 1; display: flex; align-items: center; gap: 8px; padding: 10px 12px; color: var(--muted); border-right: var(--rw) solid var(--rule); }
.wizard-step:last-child { border-right: none; }
.wizard-step .num {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; flex: none; border: var(--rw2) solid currentColor;
  font-size: 12px; font-family: var(--fm);
}
.wizard-step .label { font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; font-family: var(--fm); white-space: nowrap; }
.wizard-step.on { background: var(--accent); color: var(--on-accent); }
.wizard-step.done { background: var(--panel); color: var(--ink); }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.field-caption { font-family: var(--fm); font-size: 9.5px; color: var(--muted); letter-spacing: 0.1em; text-transform: uppercase; margin-top: 4px; }
.wizard-location { border: var(--rw) solid var(--rule); background: var(--surface); }
.wizard-location input[type="text"] { font-family: var(--fd); font-weight: 700; font-size: 18px; }
.wizard-add { width: 100%; border: var(--rw2) dashed var(--rule); background: none; color: var(--ink); font-family: var(--fm); font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; padding: 12px; cursor: pointer; }
```

(Delete the now-superseded old `.wizard-step .bar` rule; if the JSX renders a `.bar` spacer element, remove it.) Comma-keys input in location cards: add `style={{ fontFamily: "var(--fm)" }}` or a `.keys-input` class if it doesn't already use one.

- [ ] **Step 4: Run tests** — `npx vitest run src/routes/CampaignWizard.test.tsx && npx tsc -b` — PASS (fix existing wizard tests if the removed `.bar` or renamed classes broke queries). Full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(wizard): codex stepper cells and step-1 calendar/holidays selects"
```

---

### Task 11: Config view

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx`
- Modify: `frontend/src/routes/ModelCombobox.tsx` (class names only, if needed) + `frontend/src/index.css`
- Test: `frontend/src/routes/ConfigView.test.tsx`

**Interfaces:**
- Consumes: `Config.user_label` / `.assistant_label` (Task 3), theme cards from `themeList` (Task 1), `.joined`, `.section-label`.

- [ ] **Step 1: Write the failing test**

Add to `ConfigView.test.tsx` (its api mock's `getConfig` must now return `user_label: "You", assistant_label: "Grimoire"`):

```tsx
it("edits transcript labels and saves them", async () => {
  render(<MemoryRouter><ConfigView /></MemoryRouter>);
  const user = await screen.findByLabelText(/your label/i);
  fireEvent.change(user, { target: { value: "Kestrel" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith(expect.objectContaining({ user_label: "Kestrel" })));
});

it("shows the three theme cards", async () => {
  render(<MemoryRouter><ConfigView /></MemoryRouter>);
  expect(await screen.findByText("CODEX")).toBeInTheDocument();
  expect(screen.getByText("MANUSCRIPT")).toBeInTheDocument();
  expect(screen.getByText("ASTRAL")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

`ConfigView.tsx` changes (keep all handlers; `save` signature widens to include the new fields):

- Root: `<div className="page page-narrow view-anim config">`, H1: `<h1 className="page-h1">Configuration</h1>` inside a `.page-head` (no button on the right).
- Each `<label>` becomes `<div className="section-label">…</div>` with the input below (keep `htmlFor`/`aria-label` associations via visually-hidden labels or `aria-label` on inputs — preserve every existing `aria-label` the tests use).
- Storage location: wrap input+button in `.joined`, button text stays `Move`; the input gets `style={{ fontFamily: "var(--fm)", fontSize: 13 }}` (or class `.mono-input`). Confirmation message keeps its logic; give it `className="save-flash"`.
- New labels block after the quote-color checkbox:

```tsx
      <div className="section-label">Transcript labels</div>
      <div className="field-row">
        <div className="field">
          <label htmlFor="cfg-user-label">Your label</label>
          <input id="cfg-user-label" value={userLabel}
                 onChange={(e) => setUserLabel(e.target.value)} placeholder="You" />
        </div>
        <div className="field">
          <label htmlFor="cfg-assistant-label">Narrator label</label>
          <input id="cfg-assistant-label" value={assistantLabel}
                 onChange={(e) => setAssistantLabel(e.target.value)} placeholder="Grimoire" />
        </div>
      </div>
```

with state seeded from config (`const [userLabel, setUserLabel] = useState(""); … setUserLabel(c.user_label)` in the load effect) and the Save button including them:

```tsx
save({ model, system_prompt: systemPrompt, user_label: userLabel,
       assistant_label: assistantLabel, ...(key ? { openrouter_key: key } : {}) })
```

- Theme cards + saved flash:

```tsx
      <div className="section-label">Theme</div>
      <div className="theme-cards">
        {themeList.map((t) => (
          <button
            key={t.name}
            className={"theme-card" + (config.theme === t.name ? " active" : "")}
            onClick={() => save({ theme: t.name })}
          >
            {t.label}
          </button>
        ))}
      </div>
```

```css
.theme-cards { display: flex; gap: 12px; margin-top: 8px; }
.theme-card {
  flex: 1; border: var(--rw) solid var(--rule); background: var(--surface); color: var(--ink);
  font-family: var(--fm); font-size: 11px; font-weight: 700; letter-spacing: 0.1em;
  padding: 14px; cursor: pointer; text-transform: uppercase;
}
.theme-card.active { background: var(--accent); color: var(--on-accent); box-shadow: var(--sh3); }
.save-flash { color: var(--accent); font-family: var(--fm); font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; }
input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--accent); }
```

- Saved text: `{saved && <span className="save-flash" style={{ marginLeft: 12 }}>Saved ✓</span>}`.
- ModelCombobox CSS (existing classes, restyle in place):

```css
.combobox-list { background: var(--surface); border: var(--rw) solid var(--rule); box-shadow: var(--sh5); max-height: 264px; margin-top: -2px; }
.combobox input[type="text"] { font-family: var(--fm); font-size: 13px; background: var(--surface); color: var(--ink); border: var(--rw) solid var(--rule); }
.combobox-row { border-bottom: 1px solid var(--rule-soft); }
.combobox-row:hover { background: var(--panel2); }
.combobox-name { font-family: var(--fd); font-weight: 700; font-size: 17px; color: var(--ink); }
.combobox-price { font-family: var(--fm); font-size: 10px; color: var(--accent); }
.combobox-id, .combobox-ctx { font-family: var(--fm); font-size: 10px; color: var(--muted); }
```

(Old `--fg` references in the combobox rules are replaced by these.)

- [ ] **Step 4: Run tests** — `npx vitest run src/routes/ConfigView.test.tsx src/routes/ModelCombobox.test.tsx && npx tsc -b` — PASS. Full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(config): codex sections, theme cards, transcript label settings"
```

---

### Task 12: Scene workspace — sub-header and rail

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: route `/campaigns/:cid/world` (Task 9); `api.getSceneDatetime(cid, sid)` → includes `holidays_today: string[]` and the current date (check the `SceneDatetime` type in `api/client.ts` for the date field name — likely `datetime` ISO string); `api.getCampaign(cid)` for the world id/name.
- Produces: `.workspace`, `.subheader`, `.scene-rail` DOM that Tasks 13–14 style around.

- [ ] **Step 1: Write the failing test**

Add to `CampaignView.test.tsx` (extend its api mock: `getSceneDatetime` resolving `{ datetime: "2026-07-03", holidays_today: ["Independence Day"], upcoming: null, cast: [] }` — match the `SceneDatetime` type's actual field names; `getCampaign` returning `{ meta: { id: "c1", name: "Ashes", world: "w1" } }`):

```tsx
it("shows the campaign sub-header with world link, scene counter, and rail date", async () => {
  renderCampaignView(); // the file's existing render helper
  expect(await screen.findByText(/‹ campaigns/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /world ▸/i })).toHaveAttribute("href", "/campaigns/c1/world");
  expect(screen.getByText(/scenes \/ 0?1/i)).toBeInTheDocument();
  expect(screen.getByText(/FRI 3 JUL 2026/i)).toBeInTheDocument();
  expect(screen.getByText(/✦ Independence Day/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /campaign world/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure** — FAIL.

- [ ] **Step 3: Implement**

`CampaignView.tsx` — state additions:

```tsx
const [worldName, setWorldName] = useState("");
const [dt, setDt] = useState<SceneDatetime | null>(null);
const [showCalendar, setShowCalendar] = useState(false);
const navigate = useNavigate();
```

Load effect additions (inside the existing `[cid]` effect):

```tsx
api.getCampaign(cid).then((c) => {
  setName(c.meta.name);
  api.getWorld(c.meta.world).then((w) => setWorldName(w.meta.name)).catch(() => setWorldName(""));
});
```

and in `selectScene`, after loading the scene:

```tsx
api.getSceneDatetime(cid, id).then(setDt).catch(() => setDt(null));
```

Date formatter (top of file):

```tsx
function railDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" })
    .replace(/,/g, "").toUpperCase();
}
```

New JSX skeleton (replacing the current `.layout` return — the transcript/inspector interiors stay as-is until Tasks 13–14, they just move inside the new grid):

```tsx
  return (
    <div className="workspace">
      <div className="subheader">
        <Link to="/" className="sub-back">‹ Campaigns</Link>
        <span className="sub-divider" />
        <span className="sub-name">{name}</span>
        {worldName && (
          <Link to={`/campaigns/${cid}/world`} className="sub-world">WORLD ▸ {worldName} ↗</Link>
        )}
        <div className="sub-actions">
          <button className="sub-changes" onClick={() => setShowChanges((v) => !v)}>
            {showChanges ? "Close" : "Changes"}
          </button>
          <button className="sub-end btn-accent" onClick={endScene} disabled={!activeId || absorbing || busy}>
            {absorbing ? "Ending…" : "End Scene"}
          </button>
        </div>
      </div>
      <div className="layout">
        <aside className="scene-rail">
          <div className="rail-counter">Scenes / {String(scenes.length).padStart(2, "0")}</div>
          <button className="btn-chrome rail-new" onClick={newScene}>+ New Scene</button>
          <div className="rail-scenes">
            {scenes.map((s, i) => (
              <EditableRow
                key={s.id}
                label={`${String(scenes.length - i).padStart(2, "0")} · ${s.title}`}
                active={s.id === activeId}
                onSelect={() => selectScene(s.id)}
                onRename={(title) => renameScene(s.id, title)}
                onDelete={() => deleteScene(s)}
              />
            ))}
          </div>
          <div className="rail-foot">
            <button className="btn-outline rail-world" onClick={() => navigate(`/campaigns/${cid}/world`)}>
              Campaign World ↗
            </button>
            {dt && (
              <button className="rail-date" onClick={() => setShowCalendar((v) => !v)}>
                {railDate(dt.datetime)}
                {dt.holidays_today.length > 0 && (
                  <span className="rail-holiday">✦ {dt.holidays_today[0]}</span>
                )}
              </button>
            )}
          </div>
        </aside>
        <section className="main">
          {showCalendar && (
            <div className="panel-slot">
              <CalendarConfig cid={cid} />
            </div>
          )}
          {/* existing: showChanges banner, absorb panel, banners, CastPanel, stream, inputbar */}
        </section>
        {activeId && (
          <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey}
                          onSceneChanged={() => selectScene(activeId)} />
        )}
      </div>
    </div>
  );
```

(Add `useNavigate` to the react-router import and `SceneDatetime` to the `api/client` type import.)

(Scene numbering: `list_scenes` sorts by `updated` **descending**, so the newest is first — number rows `scenes.length - i` so the oldest reads 01. Remove the old `<details className="calendar-config-wrap">` block and the old `.campaign-header` div; keep everything else in `.main` unchanged for now.)

CSS:

```css
/* ---- scene workspace ---- */
.workspace { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.subheader { display: flex; align-items: center; gap: 14px; background: var(--chrome); padding: 0 18px; min-height: 46px; }
.sub-back { font-family: var(--fm); font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--chrome-muted); text-decoration: none; }
.sub-back:hover { color: var(--chrome-text); }
.sub-divider { width: 1px; align-self: stretch; margin: 10px 0; background: var(--chrome-rule); }
.sub-name { font-family: var(--fd); font-weight: 700; font-size: 20px; color: var(--chrome-text); }
.sub-world { font-family: var(--fm); font-size: 10px; letter-spacing: 0.08em; color: var(--accent); text-decoration: underline; text-transform: uppercase; }
.sub-actions { margin-left: auto; display: flex; align-items: stretch; align-self: stretch; }
.sub-changes { background: none; border: none; border-left: 1px solid var(--chrome-rule); color: var(--chrome-muted); font-family: var(--fm); font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; padding: 0 16px; cursor: pointer; }
.sub-changes:hover { color: var(--chrome-text); }
.sub-end { border: none; box-shadow: none; }

.layout { display: grid; grid-template-columns: 236px 1fr 286px; flex: 1; min-height: 0; }
.scene-rail { background: var(--panel); border-right: var(--rw) solid var(--rule); display: flex; flex-direction: column; min-height: 0; padding: 14px; }
.rail-counter { font-family: var(--fm); font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); margin-bottom: 10px; }
.rail-new { width: 100%; margin-bottom: 12px; }
.rail-scenes { flex: 1; overflow-y: auto; min-height: 0; }
.rail-scenes .row { background: none; border: none; border-bottom: 1px solid var(--rule-soft); font-size: 15px; color: var(--subtle); padding: 9px 8px; margin: 0; }
.rail-scenes .row.active { background: var(--accent); color: var(--on-accent); font-family: var(--fd); font-weight: 700; font-size: 16px; border-left: none; }
.rail-scenes .row.active .row-actions button { color: var(--on-accent); }
.rail-foot { margin-top: 12px; display: flex; flex-direction: column; gap: 10px; }
.rail-world { width: 100%; }
.rail-date { background: none; border: none; cursor: pointer; text-align: left; font-family: var(--fm); font-size: 10px; letter-spacing: 0.1em; color: var(--muted); text-transform: uppercase; padding: 0; }
.rail-holiday { display: block; margin-top: 3px; font-size: 9.5px; color: var(--accent); }
.panel-slot { background: var(--panel2); border-bottom: var(--rw) solid var(--rule); padding: 14px 18px; }
@media (max-width: 1100px) { .layout { grid-template-columns: 236px 1fr; } .inspector { display: none; } }
```

(Delete the old `.sidebar` rules and `.campaign-header` rules, old lines 28–33, 84, 313–315.)

- [ ] **Step 4: Run tests** — `npx vitest run src/routes/CampaignView.test.tsx && npx tsc -b` — PASS (update existing CampaignView tests for the removed "Calendar" `<details>` and renamed header). Full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(scene): chrome sub-header and codex scene rail with date and holiday footer"
```

---

### Task 13: Transcript — speaker spines, labels, composer

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx` (message rendering + composer)
- Modify: `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `Message.speaker?` and `Config.user_label`/`.assistant_label` (Task 3); `quotePlugin` (existing — emits `.quoted` spans).

- [ ] **Step 1: Write the failing test**

```tsx
it("renders vertical speaker spines with configured labels and message speakers", async () => {
  // in this test's mock: getConfig → { …, user_label: "Kestrel", assistant_label: "Grimoire" }
  // getScene → messages: [
  //   { role: "user", content: "I open the door." },
  //   { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
  // ]
  renderCampaignView();
  expect(await screen.findByText("Kestrel")).toBeInTheDocument();
  expect(screen.getByText("Seraphine Vale")).toBeInTheDocument();
  expect(document.querySelector(".msg-card")).toBeNull();
});
```

- [ ] **Step 2: Run to verify failure** — FAIL ("You" renders, no spine, `.msg-card` present).

- [ ] **Step 3: Implement**

`CampaignView.tsx` — labels state:

```tsx
const [labels, setLabels] = useState({ user: "You", assistant: "Grimoire" });
// in the config effect:
api.getConfig().then((c) => {
  setColorQuotes(c.quote_color === "on");
  setLabels({ user: c.user_label || "You", assistant: c.assistant_label || "Grimoire" });
}).catch(() => {});
```

Scene title above the stream (inside `.main`, before `.stream`):

```tsx
{activeId && (
  <h2 className="scene-title">{scenes.find((s) => s.id === activeId)?.title ?? ""}</h2>
)}
```

Message rendering — replace the `.msg-card` map and streaming block:

```tsx
        <div className={"stream" + (colorQuotes ? " color-quotes" : "")} ref={streamRef}>
          {messages.map((m, i) => (
            <div className={`msg ${m.role}`} key={i}>
              <span className="spine">{m.speaker ?? labels[m.role]}</span>
              <div className="msg-body">
                {editing?.index === i ? (
                  <div className="msg-edit-form">
                    <textarea aria-label="Edit message" rows={4} value={editing.text}
                              onChange={(e) => setEditing({ index: i, text: e.target.value })} />
                    <div className="form-actions">
                      <button className="subtle" onClick={() => setEditing(null)}>Cancel</button>
                      <button className="btn-accent" onClick={saveEdit}>Save</button>
                    </div>
                  </div>
                ) : (
                  <RenderedMarkdown content={m.content} />
                )}
              </div>
              {editing?.index !== i && !busy && (
                <button className="msg-edit" aria-label={`Edit message ${i + 1}`}
                        onClick={() => setEditing({ index: i, text: m.content })}>✎</button>
              )}
            </div>
          ))}
          {streaming && (
            <div className="msg assistant">
              <span className="spine">{labels.assistant}</span>
              <div className="msg-body">
                <RenderedMarkdown content={streaming} />
                <span className="cursor" />
              </div>
            </div>
          )}
        </div>
```

Composer (replace `.inputbar` contents’ classes only):

```tsx
        <div className="inputbar">
          <textarea
            rows={3}
            placeholder="Speak your intent…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="send" onClick={send} disabled={busy}>{busy ? "…" : "Send ▸"}</button>
        </div>
```

CSS — replace `.stream`, `.msg-card*`, `.inputbar`, `.cursor` rules:

```css
/* ---- transcript ---- */
.main { display: flex; flex-direction: column; min-height: 0; min-width: 0; background: var(--page); color: var(--page-ink); }
.scene-title {
  font-family: var(--fd); font-weight: 900; font-size: 34px; text-transform: uppercase;
  margin: 0; padding: 22px 26px 12px; border-bottom: var(--rw) solid var(--rule);
  text-shadow: var(--glow); line-height: 0.95;
}
.stream { flex: 1; overflow-y: auto; padding: 24px 26px; }
.msg { display: flex; gap: 14px; margin-bottom: 26px; position: relative; }
.spine {
  writing-mode: vertical-rl; transform: rotate(180deg); align-self: flex-start;
  flex: none; font-family: var(--fm); font-size: 10px; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--page-muted);
}
.msg.user .spine { color: var(--quote); }
.msg-body { flex: 1; min-width: 0; font-size: 16.5px; line-height: 1.62; }
.msg-body > :first-child p:first-child, .msg-body p:first-child { margin-top: 0; }
.msg-edit { position: absolute; top: 0; right: 0; opacity: 0; background: none; border: none; color: var(--page-muted); cursor: pointer; font-size: 13px; }
.msg:hover .msg-edit, .msg:focus-within .msg-edit { opacity: 1; }
.msg-edit:hover { color: var(--accent); }
.msg-edit-form textarea { width: 100%; background: var(--surface); color: var(--ink); border: var(--rw2) solid var(--accent); padding: 8px; font-family: var(--fb); resize: vertical; }
.color-quotes .quoted { color: var(--quote); font-weight: 500; }
.cursor { display: inline-block; width: 9px; height: 16px; background: var(--accent); vertical-align: -2px; animation: blink 1s steps(1) infinite; }
.inputbar { display: flex; gap: 0; padding: 0; border-top: var(--rw) solid var(--rule); background: var(--page); flex: none; }
.inputbar textarea {
  flex: 1; background: transparent; color: var(--page-ink); border: none;
  padding: 14px 18px; font-family: var(--fb); font-size: 15.5px; resize: none; outline: none;
}
.inputbar .send {
  background: var(--chrome); color: var(--chrome-text); border: none;
  border-left: var(--rw) solid var(--rule); padding: 0 22px; cursor: pointer;
  font-family: var(--fm); font-weight: 700; font-size: 12px; letter-spacing: 0.1em; text-transform: uppercase;
}
.inputbar .send:disabled { color: var(--chrome-muted); cursor: default; }
```

(Existing banners / CastPanel / absorb sit inside `.main` — they inherit `--page` background until Task 14 restyles them; fine mid-migration.)

- [ ] **Step 4: Run tests** — `npx vitest run src/routes/CampaignView.test.tsx && npx tsc -b` — PASS (update tests that queried `.msg-card` / "You"/"Grimoire" head text / the old Edit button label). Full suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(transcript): vertical speaker spines, configurable labels, page-styled composer"
```

---

### Task 14: Inspector, absorb panel, changes banner

**Files:**
- Modify: `frontend/src/components/SceneInspector.tsx` (context header/dots markup)
- Modify: `frontend/src/components/ChangesPanel.tsx` (class names only if needed)
- Modify: `frontend/src/index.css`
- Test: `frontend/src/components/SceneInspector.test.tsx`

**Interfaces:**
- Consumes: `SceneContext.sections[].{label,text,tokens}` (existing), `ctx.total_tokens`, model context window (existing `pct` logic in the component).

- [ ] **Step 1: Update SceneInspector context section**

The component already renders captioned `.side-section` blocks and `<details className="ctx-section">` rows. Adjust markup:

Context header (replace the `<h4>` at line ~157):

```tsx
      <div className="side-section">
        <div className="ctx-head">
          <h4>Context</h4>
          {ctx && <span className="ctx-pct">{pctNumber(ctx.total_tokens)}%</span>}
        </div>
        {ctx && (
          <>
            <div className="ctx-bar"><div className="ctx-bar-fill" style={{ width: `${Math.min(100, pctNumber(ctx.total_tokens))}%` }} /></div>
            <div className="ctx-tokens">{ctx.total_tokens.toLocaleString()} / {windowTokens.toLocaleString()} tok</div>
            <div className="ctx-caption">Breakdown · click a row to inspect</div>
          </>
        )}
        {ctx?.sections.map((s) => (
          <details className="ctx-section" key={s.label}>
            <summary>
              <span className={"ctx-dot" + (s.label.toLowerCase().includes("transcript") ? " hot" : "")} />
              <span className="ctx-label">{s.label}</span>
              <span className="ctx-meta">{s.tokens.toLocaleString()} · {pctNumber(s.tokens)}%</span>
            </summary>
            <div className="ctx-mini"><div style={{ width: `${Math.min(100, pctNumber(s.tokens))}%` }} /></div>
            <pre className="ctx-text">{s.text}</pre>
          </details>
        ))}
      </div>
```

where `pctNumber(t)` is the existing pct helper returning a number (adapt: the current `pct()` returns a formatted string — add `const pctNumber = (t: number) => windowTokens ? Math.round((t / windowTokens) * 100) : 0;` using the component's existing `models.find(...)?.context` value, named `windowTokens`).

- [ ] **Step 2: CSS**

Replace `.inspector*`, `.ctx-*`, `.absorb-panel`, `.cast-panel` skins:

```css
/* ---- inspector ---- */
.inspector {
  width: auto; background: var(--panel); border-left: var(--rw) solid var(--rule);
  padding: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 0; min-height: 0;
}
.inspector .side-section { padding: 14px 16px; border-bottom: var(--rw) solid var(--rule); }
.inspector .side-section:last-child { border-bottom: none; }
.inspector h4 { margin: 0 0 10px; font-family: var(--fm); font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); font-weight: 400; }
.inspector-row {
  display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;
  text-align: left; background: none; color: var(--ink); border: none;
  border-bottom: 1px solid var(--rule-soft); padding: 8px 0; margin: 0; cursor: pointer;
}
.inspector-row:hover { color: var(--accent); }
.inspector-row .role { float: none; }
.ctx-head { display: flex; justify-content: space-between; align-items: baseline; }
.ctx-pct { font-family: var(--fm); font-size: 12px; font-weight: 700; color: var(--accent); }
.ctx-bar { height: 10px; border: var(--rw2) solid var(--rule); background: var(--track); margin: 8px 0 6px; }
.ctx-bar-fill { height: 100%; background: var(--accent); }
.ctx-tokens, .ctx-caption { font-family: var(--fm); font-size: 10px; color: var(--muted); letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 6px; }
.ctx-section { border: none; border-bottom: 1px solid var(--rule-soft); margin: 0; }
.ctx-section > summary { display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 7px 0; list-style: none; }
.ctx-section > summary::-webkit-details-marker { display: none; }
.ctx-dot { width: 7px; height: 7px; background: var(--ink); flex: none; }
.ctx-dot.hot { background: var(--accent); }
.ctx-label { flex: 1; font-size: 14px; }
.ctx-meta { font-family: var(--fm); font-size: 10px; color: var(--muted); }
.ctx-mini { height: 4px; background: var(--track); margin: 2px 0 6px; }
.ctx-mini > div { height: 100%; background: var(--accent); }
.ctx-text {
  white-space: pre-wrap; word-break: break-word; max-height: 150px; overflow-y: auto;
  margin: 0 0 8px; padding: 8px; font-family: var(--fm); font-size: 10.5px;
  background: var(--panel2); border: 1px solid var(--rule-soft); color: var(--ink);
}
```

Absorb + changes:

```css
.absorb-panel { background: var(--panel2); border-bottom: var(--rw) solid var(--rule); padding: 16px 22px; margin: 0; color: var(--ink); }
.absorb-panel h4 { font-family: var(--fd); font-weight: 800; font-size: 22px; text-transform: uppercase; margin: 0 0 10px; }
.absorb-panel input, .absorb-panel textarea { width: 100%; background: var(--surface); color: var(--ink); border: var(--rw2) solid var(--rule); padding: 8px; margin-bottom: 8px; font-family: var(--fb); resize: vertical; }
.absorb-panel .form-actions .subtle { border: var(--rw2) solid var(--rule); background: none; color: var(--ink); }
.absorb-panel .form-actions .primary, .absorb-panel .form-actions .btn-accent { background: var(--chrome); color: var(--chrome-text); border: var(--rw) solid var(--rule); }
.changes-banner, .changes-panel { background: var(--panel2); border-bottom: var(--rw) solid var(--rule); padding: 12px 22px; font-family: var(--fm); font-size: 11px; line-height: 1.7; color: var(--subtle); }
.changes-panel strong, .changes-banner strong { color: var(--ink); }
.changes-panel .caption { color: var(--accent); text-transform: uppercase; letter-spacing: 0.1em; }
.cast-panel { border: var(--rw2) solid var(--rule); background: var(--surface); color: var(--ink); margin: 14px 22px; }
```

Check `ChangesPanel.tsx`'s root class (`grep -n "className" frontend/src/components/ChangesPanel.tsx | head -3`) and align the CSS selector above to it (add the class to the component root if it has none).

- [ ] **Step 3: Run tests** — `npx vitest run src/components/SceneInspector.test.tsx src/components/ChangesPanel.test.tsx src/components/CastPanel.test.tsx && npx tsc -b` — PASS (update selectors for the reshaped context summary). Full suite — PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "feat(inspector): codex context breakdown with bars and dots; panel2 absorb and changes"
```

---

### Task 15: Cleanup — remove compat aliases, sweep, verify

**Files:**
- Modify: `frontend/src/theme/themes/{codex,manuscript,astral}.ts` (drop aliases)
- Modify: `frontend/src/index.css` (purge old token references)
- Modify: `frontend/src/theme/themes/tokens.test.ts`

- [ ] **Step 1: Add the failing guard test**

Append to `tokens.test.ts`:

```ts
  it("carries no legacy token aliases", () => {
    for (const t of themeList) {
      for (const legacy of ["--fg", "--font-display", "--font-body", "--radius", "--mono"]) {
        expect(t.tokens[legacy], `${t.name} still defines ${legacy}`).toBeUndefined();
      }
    }
  });
```

Run `npx vitest run src/theme` — FAIL.

- [ ] **Step 2: Purge legacy references**

Search and replace remaining uses in CSS/TSX:

```bash
grep -rn "var(--fg\|var(--font-display\|var(--font-body\|var(--radius\|var(--mono" frontend/src
```

For each hit in `index.css` (remaining unmigrated selectors: `.field`, `.table`, `.drawer*`, `.record-drawer`, `.tagline-modal`, `.opener-preview`, `.import-section`, `.diff-*`, `.greeting-row`, `.localize-*`, `.gallery-*`, `.owner-*`, `.rail-group*`, `.editor*`, `.detail-view` remnants, `.subtle`, `.banner`, `.form-actions`): replace `var(--fg)`→`var(--ink)`, `var(--font-display)`→`var(--fd)`, `var(--font-body)`→`var(--fb)`, `var(--mono, monospace)`→`var(--fm)`, and **delete** every `border-radius: var(--radius);` declaration plus literal radii (`border-radius: 6px`, `8px`, and the `50%` on `.row-avatar` — zero-radius law, avatars go square). Also update `.banner` (key-missing / error banner) to the codex look:

```css
.banner { background: var(--banner-bg); border: var(--rw) solid var(--accent); color: var(--banner-ink); padding: 10px 16px; margin: 12px 22px; font-family: var(--fm); font-size: 11px; }
.banner a { color: var(--banner-ink); }
.subtle { background: transparent; color: var(--subtle); border: var(--rw2) solid var(--rule); padding: 6px 12px; cursor: pointer; font-family: var(--fm); font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; }
.subtle:hover { color: var(--accent); border-color: var(--accent); }
button.send, button.primary { background: var(--accent); color: var(--on-accent); border: var(--rw) solid var(--rule); padding: 8px 14px; cursor: pointer; font-family: var(--fm); font-weight: 700; font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; }
```

Then remove the five alias entries from each of the three theme files.

- [ ] **Step 3: Verify no raw-hex leaks in components**

```bash
grep -rn "#[0-9a-fA-F]\{3,6\}" frontend/src --include="*.tsx" | grep -v test
```

Expected: no hits (any hit gets converted to a token).

- [ ] **Step 4: Full verification**

```bash
cd frontend && npx vitest run && npx tsc -b && npm run build
cd .. && C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q
```

Expected: all PASS.

- [ ] **Step 5: Visual pass against the reference**

Start the app (backend + `npm run dev`), and compare against `design_handoff_codex_redesign/screenshots/`:
- `codex-01-campaigns.png` — dashboard: H1 weight/rule, list block, accent button shadow.
- `codex-02-scene-workspace.png` — rail/transcript/inspector proportions, spine labels, quote coloring, context bar.
- `codex-03-wizard.png` — stepper cells, step-1 selects.
- `codex-04-character-detail.png` — header, section labels, greeting quotes.
- `codex-05-config.png` — theme cards, combobox.
- Switch theme to Manuscript and Astral in Config; compare `manuscript-01/02`, `astral-01/02` — hairline borders, blurred/glow shadows, background overlays, fonts all swap with **zero layout shift**.

Fix discrepancies against the prototype (`Grimoire.dc.html` is authoritative). Take notes of anything deferred.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "refactor(theme): drop legacy token aliases; finish codex sweep"
```

---

## Post-plan checklist

- Run the superpowers:verification-before-completion flow before claiming done.
- Use superpowers:requesting-code-review for the final diff.
- Integration (rebase-merge to `main` per user preference) happens via superpowers:finishing-a-development-branch — not part of this plan.
