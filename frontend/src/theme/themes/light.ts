import type { Theme } from "../types";

/** The same one theme in daylight. Structure, spacing and type are identical
 *  to `dark` — only the values move, and two things go away entirely: the
 *  starfield (`--fx`) and every glow. Light mode separates surfaces with
 *  borders, never with light.
 *
 *  The trap this palette exists to survive: an element that inherits `color`
 *  instead of setting it renders dark-mode ink on off-white and disappears.
 *  Buttons are the usual culprit — they do not inherit `color` at all — so
 *  `index.css` sets it at every panel root and on every `<button>`.
 *
 *  `--muted` is the contrast floor for labels. Do not go lighter. */
const light: Theme = {
  name: "light",
  label: "LIGHT",
  tokens: {
    // --- the redesign's names ---
    "--bg": "#ecebe6",
    "--panel": "#f6f5f1",
    "--surface": "#e4e3dd",
    "--ink": "#15161c",
    "--subtle": "#3c4050",
    "--muted": "#5b6070",
    "--accent": "#0d6c70",
    // Accent *text* on a light panel, and the pressed state of an accent
    // button. On light, pressing darkens — the dark-mode lighten would wash out.
    "--accent-strong": "#0a5457",
    "--alert": "#8a2a6b",
    "--line": "rgba(21,22,28,.16)",
    "--line-strong": "rgba(21,22,28,.22)",

    // --- inherited aliases, same values ---
    "--panel2": "#eeede8",
    "--rule": "rgba(21,22,28,.22)",
    "--rule-soft": "rgba(21,22,28,.16)",
    "--track": "rgba(21,22,28,.10)",
    "--chrome": "#f6f5f1",
    "--chrome-text": "#0a5457",
    "--chrome-muted": "#5b6070",
    "--chrome-rule": "rgba(21,22,28,.22)",
    "--on-accent": "#ffffff",
    "--quote": "#8a2a6b",
    "--on-quote": "#ffffff",
    "--page": "#f6f5f1",
    "--page-ink": "#15161c",
    "--page-muted": "#5b6070",
    "--banner-bg": "#f3e4ec",
    "--banner-ink": "#8a2a6b",
    "--disabled": "#a4a49c",

    // --- one shadow of each kind, not a ramp ---
    // A popover still needs to read as lifted off the desk; nothing else does.
    "--shadow-pop": "0 10px 28px rgba(21,22,28,.16)",
    "--shadow-glow": "none",

    // --- type ---
    "--fd": "'Cinzel',serif",
    "--fb": "'Spectral',Georgia,serif",
    "--fm": "'Space Mono',monospace",

    "--fx": "none",
    "--glow": "none",
  },
};
export default light;
