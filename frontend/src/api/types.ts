/** The API's data model: every payload shape the backend sends or takes, plus
 *  the handful of constants and pure helpers that describe them.
 *
 *  Split out of `client.ts`, which had grown to 2100 lines by holding both the
 *  types and the calls -- two groups of exports that never reference each
 *  other, which is exactly the seam the code-health report named. `client.ts`
 *  re-exports everything here, so `import { api, type Card } from
 *  "../api/client"` keeps working and no caller had to change.
 *
 *  The shapes themselves live in `./types/`, one module per domain, because
 *  this file in turn grew past four thousand lines. This module is their one
 *  public face: import from `../api/types` (or through `../api/client`), never
 *  from a module under `./types/`, so the domain split stays an internal
 *  arrangement that can move without touching a caller. The domain modules
 *  import each other with `import type` only, and in one direction -- no
 *  cycles among them.
 *
 *  Nothing in this module may import from `client.ts`: the types describe the
 *  wire format and know nothing about how a request is made.
 */
export * from "./types/inference";
export * from "./types/config";
export * from "./types/campaigns";
export * from "./types/records";
export * from "./types/play";
export * from "./types/context";
export * from "./types/review";
export * from "./types/ledger";
export * from "./types/graph";
export * from "./types/mechanics";
export * from "./types/app";
export * from "./types/expressions";

export * from "./types/vn";
