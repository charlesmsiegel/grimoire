import { useCallback } from "react";
import type { Briefing } from "../../api/client";
import {
  type Actor,
  type SceneMeta
} from "../../api/client";
import { usePaletteSource, type PaletteItem } from "../../components/palette";
import { initialsOf } from "../../components/Portrait";
import { sectionHref } from "../../worldPaths";
import { sceneNumber } from "./campaignShared";

export function useCampaignPalette({ cast, openActor, briefing, scenes, goToScene, newScene, cid }: {
  cast: Actor[];
  openActor: (kind: string, id: string) => void;
  briefing: Briefing | null;
  scenes: SceneMeta[];
  goToScene: (id: string) => void;
  newScene: () => void;
  cid: string;
}) {

  /** What this page contributes to ⌘K: its scenes, its cast, and the two
   *  actions that used to be buttons in a chrome bar.
   *
   *  This is where the scene rail went. A rail is a list you pay for in width
   *  on every turn of every scene, to answer a question you ask a few times a
   *  session; the same list behind ⌘K costs nothing until asked and searches,
   *  which the rail never did. */
  const paletteSource = useCallback((): PaletteItem[] => {
    const out: PaletteItem[] = [];
    for (const a of cast) {
      out.push({
        id: `cast:${a.kind}/${a.id}`, group: "IN THIS CAMPAIGN", label: a.name,
        meta: `${a.role === "player" ? "player" : "character"} · in scene`,
        badge: initialsOf(a.name),
        run: () => openActor(a.kind, a.id),
      });
    }
    for (const t of briefing?.plot ?? []) {
      out.push({
        id: `thread:${t.id}`, group: "IN THIS CAMPAIGN", label: t.title,
        meta: `thread · ${t.status}`
      });
    }
    for (const c of briefing?.commitments ?? []) {
      out.push({
        id: `owed:${c.id}`, group: "IN THIS CAMPAIGN", label: c.title,
        meta: `${c.kind} · ${c.due || "no deadline"}`
      });
    }
    for (const [i, sc] of scenes.entries()) {
      out.push({
        id: `scene:${sc.id}`, group: "SCENES", label: sc.title,
        meta: [`scene ${sceneNumber(sc.id, scenes.length - i)}`,
        sc.done ? "absorbed" : null].filter(Boolean).join(" · "),
        run: () => goToScene(sc.id),
      });
    }
    out.push({
      id: "action:new-scene", group: "SCENES", label: "New scene",
      meta: "in this campaign", action: true, run: newScene
    });
    out.push({
      id: "action:campaign-world", group: "ELSEWHERE", label: "This campaign's world",
      meta: "locations, lore, cast",
      to: sectionHref({ kind: "campaign", id: cid }, { kind: "section", at: "characters" })
    });
    return out;
  }, [cid, cast, briefing, scenes, openActor, goToScene, newScene]);

  usePaletteSource(paletteSource);
  return {};
}
