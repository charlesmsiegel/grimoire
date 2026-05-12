/**
 * Lightweight runtime CSS scope-prefixer for per-mechanics theme stylesheets.
 *
 * Mechanics modules ship `theme.css`; the Frontend wraps each rendered sheet in
 * `.mechanics-<module-id>` and prefixes every selector in the module's CSS so
 * styles cannot leak between systems (spec 14 §Per-mechanics CSS theming).
 *
 * A full PostCSS plugin lives upstream of bundling for hand-authored CSS, but
 * we also need to handle theme.css dropped in at runtime — that's what this
 * utility is for. The parser is intentionally small: it walks rules, prefixes
 * each comma-separated selector, and recurses into @media / @supports blocks.
 *
 * Selectors already starting with the scope class (`.mechanics-foo .x`) are
 * left alone; `:root` and `html`/`body` are rewritten to target the scope
 * itself.
 */

export function scopeCss(css: string, scopeClass: string): string {
  const scope = `.${scopeClass}`;
  return rewriteBlock(stripComments(css), scope);
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

function rewriteBlock(css: string, scope: string): string {
  let out = "";
  let i = 0;
  while (i < css.length) {
    const next = findRuleStart(css, i);
    if (next === -1) {
      out += css.slice(i);
      break;
    }
    const prelude = css.slice(i, next).trim();
    const blockEnd = matchBrace(css, next);
    const body = css.slice(next + 1, blockEnd);
    if (prelude.startsWith("@")) {
      const name = prelude.split(/\s+/, 1)[0] ?? "";
      if (name === "@media" || name === "@supports" || name === "@container" || name === "@layer") {
        out += `${prelude} {${rewriteBlock(body, scope)}}`;
      } else {
        // @keyframes, @font-face, @import, etc. — leave unchanged.
        out += `${prelude} {${body}}`;
      }
    } else {
      out += `${prefixSelectorList(prelude, scope)} {${body}}`;
    }
    i = blockEnd + 1;
  }
  return out;
}

function findRuleStart(css: string, from: number): number {
  for (let i = from; i < css.length; i++) {
    const ch = css[i];
    if (ch === "{") return i;
    if (ch === "}") return -1;
  }
  return -1;
}

function matchBrace(css: string, openIdx: number): number {
  let depth = 0;
  for (let i = openIdx; i < css.length; i++) {
    const ch = css[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return css.length - 1;
}

function prefixSelectorList(selectorList: string, scope: string): string {
  return selectorList
    .split(",")
    .map((sel) => prefixSelector(sel.trim(), scope))
    .join(", ");
}

function prefixSelector(selector: string, scope: string): string {
  if (!selector) return selector;
  if (selector.startsWith(scope)) return selector;
  if (/^(:root|html|body)\b/.test(selector)) {
    return selector.replace(/^(:root|html|body)/, scope);
  }
  return `${scope} ${selector}`;
}
