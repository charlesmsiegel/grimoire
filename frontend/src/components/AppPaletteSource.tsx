import { useCallback, useEffect, useState } from "react";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { LIBRARY_SECTIONS } from "../librarySections";
import { usePalette, usePaletteSource, type PaletteItem } from "./palette";

/** The offers that exist on every route: campaigns, worlds, the library's
 *  sections, config. Registered by the shell so the palette is never empty
 *  even on a page that contributes nothing of its own.
 *
 *  Everything here files under ELSEWHERE, which sorts last. A page that knows
 *  about the campaign you are actually in registers its own rows under
 *  IN THIS CAMPAIGN and SCENES, and those come first. */
export default function AppPaletteSource() {
  const { open } = usePalette();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);

  // Fetched when the palette opens rather than on mount: these are two GETs
  // that parse every campaign's scene headers server-side, and paying for them
  // on a route that never opens the palette would be the shell taxing play.
  useEffect(() => {
    if (!open) return;
    let live = true;
    api.listCampaigns().then((c) => { if (live) setCampaigns(c); }).catch(() => {});
    api.listWorlds().then((w) => { if (live) setWorlds(w); }).catch(() => {});
    return () => { live = false; };
  }, [open]);

  const source = useCallback((): PaletteItem[] => {
    const out: PaletteItem[] = [];
    for (const c of campaigns) {
      out.push({
        id: `campaign:${c.id}`, group: "ELSEWHERE", label: c.name,
        meta: "campaign", to: `/campaigns/${c.id}`,
      });
    }
    for (const w of worlds) {
      out.push({
        id: `world:${w.id}`, group: "ELSEWHERE", label: w.name,
        meta: "world", to: `/worlds/${w.id}`,
      });
    }
    // Home. Named as a place rather than left implicit behind the brand mark:
    // with no nav sidebar, "get me back to the shelf" has to be typeable.
    out.push({ id: "section:/", group: "ELSEWHERE", label: "Campaigns",
               meta: "the shelf", to: "/" });
    for (const s of LIBRARY_SECTIONS) {
      out.push({
        id: `section:${s.to}`, group: "ELSEWHERE", label: s.label,
        meta: "library section", to: s.to,
      });
    }
    out.push({ id: "section:/library", group: "ELSEWHERE", label: "The Library",
               meta: "library section", to: "/library" });
    out.push({ id: "section:/connections", group: "ELSEWHERE", label: "Connections",
               meta: "library section", to: "/connections" });
    out.push({ id: "action:new-campaign", group: "ELSEWHERE", label: "New campaign",
               meta: "start one", action: true, to: "/campaigns/new" });
    out.push({ id: "section:/config", group: "ELSEWHERE", label: "Configuration",
               meta: "storage, model, appearance", to: "/config" });
    return out;
  }, [campaigns, worlds]);

  usePaletteSource(source);
  return null;
}
