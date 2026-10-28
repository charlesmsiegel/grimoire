import type { CampaignBudget, Message, SceneUsage } from "../../api/client";

/** A post a `rewrite_stored` rule changed as it landed (spec 6.3). `index` is
 *  its absolute transcript index, which is what a restore addresses. */
export type RewrittenPost = { index: number; message: Message };

export type InspectorProps = { cid: string; sid: string; refreshKey: number; onSceneChanged: () => void;
    onSceneRenamed?: (id: string) => void; pcless?: boolean;
    /** A turn is streaming into this scene, so anything that can rename its
     *  file has to wait: the id is the filename, and moving it mid-turn strands
     *  `finalize`, `_persist_reply` and the abort write alike (#95). The first
     *  date set re-slugs, so both date actions below are rename surfaces. */
    sceneLocked?: boolean;
    /** Reports a scene-renaming request in and out of flight. The parent blocks
     *  new turns while one is pending: until the PUT answers, the scene's id is
     *  in doubt, and a turn handed the old one writes nowhere (#95). */
    onRenaming?: (active: boolean) => void;
    /** How many posts the scene already has, which is the only thing the
     *  briefing's default open/closed state depends on (#118). Absent counts as
     *  a fresh scene, so a caller that does not track it gets the briefing open.
     *  The caller's count is windowed (#94) and so understates a long
     *  transcript — which does not matter here, because a window is a whole page
     *  and the only distinction this draws is "barely started or not". */
    posts?: number | undefined;
    /** The reads the Cost section renders, when the caller already holds them
     *  (see `CostPanel`); left out, that section reads them itself. */
    usage?: SceneUsage | null;
    budget?: CampaignBudget | null;
    onBudgetSaved?: (cid: string, budget: CampaignBudget) => void;
    /** The posts on screen a stored rewrite changed (spec 6.3), listed under
     *  Turn history. The caller's window, like `posts`. */
    rewritten?: RewrittenPost[];
    /** A restore rewrote the transcript. Left out, `onSceneChanged` stands in;
     *  the play view passes its own so a restore asks for the follow-ups any
     *  edit does. */
    onTranscriptEdited?: (() => void) | undefined;
    /** The manual tasks a composer quick reply is running for this scene, so
     *  the same task cannot be started twice from two surfaces. */
    stripTasks?: { rolling_summary?: boolean; scene_break?: boolean };
    /** Reports this panel's own Refresh now / Ask now in and out of flight,
     *  for the same reason in the other direction. */
    onTaskBusy?: (task: "rolling_summary" | "scene_break", busy: boolean) => void };

/** Explicit values forwarded by the composition root, including absent options. */
export type InspectorInputs<K extends keyof InspectorProps> = { [P in K]: InspectorProps[P] };
