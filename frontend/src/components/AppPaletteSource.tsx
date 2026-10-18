import { useCallback, useEffect, useState } from "react";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { LIBRARY_SECTIONS } from "../librarySections";
import { byName } from "../sortByName";
import { sectionHref } from "../worldPaths";
import type { ShellPayload } from "../api/types";
import { LINKS, SECTIONS } from "../configSections";
import { ledgerHref } from "../ledgerPaths";
import { APP_ROWS, CAMPAIGN_ROWS, campaignPath } from "../shell/rail";
import { useFocus } from "./focus";
import { usePalette, usePaletteSource, type PaletteItem } from "./palette";

/** The offers that exist on every route: campaigns, worlds, the library's
 *  sections, config. Registered by the shell so the palette is never empty
 *  even on a page that contributes nothing of its own.
 *
 *  Everything here files under ELSEWHERE, which sorts last. A page that knows
 *  about the campaign you are actually in registers its own rows under
 *  IN THIS CAMPAIGN and SCENES, and those come first. */
export default function AppPaletteSource({ payload = null }: { payload?: ShellPayload | null }) {
  const { open } = usePalette();
  const { focus, setFocus } = useFocus();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);

  // Fetched when the palette opens rather than on mount: these are two GETs
  // that parse every campaign's scene headers server-side, and paying for them
  // on a route that never opens the palette would be the shell taxing play.
  useEffect(() => {
    if (!open) return;
    let live = true;
    // A list that will not load leaves its half of the palette empty: every
    // entry in it is a page the rail and the shelves also reach.
    api.listCampaigns().then((c) => { if (live) setCampaigns(c); }).catch(() => {});
    api.listWorlds().then((w) => { if (live) setWorlds(w); }).catch(() => {});
    return () => { live = false; };
  }, [open]);

  const source = useCallback((query: string): PaletteItem[] => {
    const out: PaletteItem[] = [];
    // A-Z, because with no query typed this list IS the offer: the palette
    // ranks by what you type, and until you type anything the order it was
    // handed - newest first - is the only order there is. Sorted here rather
    // than at the fetch so the two lists cannot drift apart.
    for (const c of byName(campaigns)) {
      out.push({
        id: `campaign:${c.id}`, group: "ELSEWHERE", label: c.name,
        meta: "campaign", to: `/campaigns/${c.id}`,
      });
    }
    for (const w of byName(worlds)) {
      out.push({
        id: `world:${w.id}`, group: "ELSEWHERE", label: w.name,
        meta: "world",
        to: sectionHref({ kind: "world", id: w.id }, { kind: "section", at: "overview" }),
      });
    }
    // Navigation comes from the rail's tables and the settings column. Adding
    // a destination makes it typeable without maintaining another route list;
    // conditional offers use the same campaign payload as the visible rail.
    const ctx = { cid: payload?.campaign?.id ?? null };
    for (const row of APP_ROWS) {
      const to = row.to(ctx, payload);
      if (to) out.push({ id: `section:${to}`, group: "ELSEWHERE", label: row.label,
        meta: `app navigation · ${row.keywords ?? ""}`, to });
    }
    for (const row of CAMPAIGN_ROWS) {
      const to = row.to(ctx, payload);
      if (to) out.push({ id: `campaign-page:${row.id}`, group: "IN THIS CAMPAIGN", label: row.label,
        meta: `${payload?.campaign?.name} · campaign navigation · ${row.keywords ?? ""}`, to });
    }
    const campaign = payload?.campaign;
    const campaignRoot = campaignPath(ctx);
    if (campaign && campaignRoot) {
      out.push(
        { id: "campaign-page:timeline", group: "IN THIS CAMPAIGN", label: "Timeline",
          meta: `${campaign.name} · dates and events`, to: `${campaignRoot}/timeline` },
        { id: "campaign-page:knowledge", group: "IN THIS CAMPAIGN", label: "Knowledge",
          meta: `${campaign.name} · who knows and suspects`, to: ledgerHref(campaign.id, { section: "knowledge" }) },
        { id: "campaign-page:continuity", group: "IN THIS CAMPAIGN", label: "Continuity",
          meta: `${campaign.name} · review findings`, to: ledgerHref(campaign.id, { section: "continuity", group: "overlaps" }) },
        { id: "campaign-page:settings", group: "IN THIS CAMPAIGN", label: "Campaign settings",
          meta: campaign.name, to: `${campaignRoot}/settings` },
      );
    }
    for (const s of LIBRARY_SECTIONS) {
      out.push({
        id: `section:${s.to}`, group: "ELSEWHERE", label: s.label,
        meta: "library section", to: s.to,
      });
    }
    out.push({ id: "action:new-campaign", group: "ELSEWHERE", label: "New campaign",
               meta: "start one", action: true, to: "/campaigns/new" });
    for (const section of SECTIONS) {
      const to = `/config?section=${section.id}`;
      out.push({ id: `section:${to}`, group: "ELSEWHERE", label: section.label,
        meta: `Settings → ${section.group}`, to });
    }
    for (const link of LINKS) {
      out.push({ id: `section:${link.to}`, group: "ELSEWHERE", label: link.label,
        meta: `Settings → ${link.group}`, to: link.to });
    }
    // Typeable as well as clickable, for the same reason every route is: the
    // header button is the only other way in, and it is one of the things this
    // hides. Offered in both directions so the palette never describes a state
    // you are already in.
    out.push({ id: "action:focus", group: "ELSEWHERE",
               label: focus ? "Leave focus mode" : "Focus mode",
               meta: focus ? "bring the bars back" : "hide every bar but the composer",
               action: true, run: () => setFocus(!focus) });
    // Full-text search, offered from the palette rather than from a topbar box
    // that would sit unused on every screen: the palette matches names, and
    // this row is how the same keystrokes reach bodies, transcripts and facts.
    // Last, and only once there is something to search FOR — it is the offer
    // for when nothing NAMED what you typed, so it must never outrank a record
    // that did.
    if (query) {
      out.push({
        id: "action:search", group: "ELSEWHERE", label: `Search for “${query}”`,
        meta: "everything · content and facts", action: true,
        to: `/search?q=${encodeURIComponent(query)}`,
      });
    }
    return out;
  }, [campaigns, worlds, focus, setFocus, payload]);

  usePaletteSource(source);
  return null;
}
