// The Rules page (Sprint 116): the library's mechanics modules (086) and a campaign's recorded rolls (075)
// (docs/RULES_PAGE.md). Addresses:
//   #/rules                                       mechanics module authoring: open a module by its id, or start one
//   #/rules?module=<id>                           one module's draft (edit, check, save, publish) and its revisions
//   #/rules?module=<id>&revision=<sha256:...>     one published revision, read-only, and trying it (never saved)
//   #/rules/<campaign id>?chat=<ChatRef>          a campaign's recorded rolls, and rolling a check for a character
//   ...&audience=director                         the director's view: every roll, and the NPCs' rolls too
// Everything is read from, and written through, the rules.* ops. Every write is reviewed first (a plan op writes
// nothing) and then made exactly as reviewed; something changed in between refuses it and the page reads again.

import { mountAuthoring } from "./authoring.js";
import { mountRolls } from "./rolls.js";

export async function mount(root, ctx) {
  ctx.styles("page.css");
  ctx.layout("page");
  ctx.setTitle("Rules");
  const campaign = ctx.path[0] || null;
  if (campaign) return mountRolls(root, ctx, campaign);
  return mountAuthoring(root, ctx);
}
