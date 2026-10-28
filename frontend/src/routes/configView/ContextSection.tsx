import { Link } from "react-router-dom";
import type { Config } from "../../api/client";
import type { Draft, DraftField } from "../../configSections";
import { ContextBudgetBar } from "../../components/ContextBudgetBar";
import { EMBEDDINGS_COPY } from "../../components/inference/copy";
import { NumField } from "./NumField";
import type { Probe } from "./settingsDraft";

/** Why images are not reaching the model even though the switch is on (#377).
 *  Silent when they are, or when the switch is off: the hint exists so turning
 *  the setting on cannot quietly do nothing. Reads the SAVED answer, so it
 *  speaks about the connection as stored, not about an unsaved draft. */
function ImagesReachHint({ reach, on }: { reach?: string | undefined; on: boolean }) {
  if (!on) return null;
  if (reach === "unknown") {
    return (
      <p className="field-hint">
        Images are not being sent: the connection your scenes use is not known to read images.
        Open its provider under Providers to refresh its model list, or set the model's
        vision there.
      </p>
    );
  }
  if (reach === "none") {
    return (
      <p className="field-hint">
        Images are not being sent: no connection is set up for your scenes yet.
      </p>
    );
  }
  if (reach === "no") {
    return (
      <p className="field-hint">
        Images are not being sent: the connection your scenes use cannot read images, so it
        is sent their descriptions.
      </p>
    );
  }
  return null;
}

export function ContextSection({ draft, edit, reach, probe }: {
  draft: Draft;
  edit: (field: DraftField, value: string) => void;
  reach: Config["send_images_reach"];
  probe: Probe;
}) {
  return (
    <>
      <p className="config-copy">
        The token ceiling a scene's prompt is packed into. Over it, whole sections are
        dropped — recalled scenes first, then the older conversation, then the standing
        frame; the system prompts, the characters and the reply format are never dropped.
        The scene inspector shows what was cut. <code>0</code> means no ceiling, and
        nothing is ever dropped.
      </p>
      <p className="config-copy">
        Context scan depth is how far back the keyword scan reads: the last so many
        messages of the transcript, and nothing older. World info, recalled scenes,
        keyed mechanics rules and the semantic-recall query all share that window, so it
        decides whether an entry last named twenty posts ago is still in play — and, set
        high enough, whether one scene drags the whole world in. <code>0</code> empties
        it: nothing in the transcript activates anything, though a scene's opener and a
        director's note still do, being this turn's own input.
      </p>
      <p className="config-copy">
        Lore recursion depth lets world info that was let in name more of it: an entry's
        text is scanned for other entries' keys, up to this many steps out. Each step can
        bring in more lore than the transcript ever mentioned, so it costs budget.{" "}
        <code>0</code> leaves it off; an entry can opt out of pulling or being pulled.
      </p>
      <p className="config-copy">
        Kept turn prompts is how many past turns each campaign keeps a frozen copy of
        the exact prompt for, readable from the scene inspector's Turn history. They
        hold whole prompts, so the count is per campaign rather than per scene — playing
        one scene for long enough ages out another's. Decisions (speaker picks,
        scene-break and voice checks) are kept separately, as many again, so they
        never age out a turn. <code>0</code> records none.
      </p>
      <p className="config-copy">
        Named off-scene characters bounds the one-line directory of characters the
        campaign can see but has never cast. It grows with the world rather than with
        the story, so on a large library it quietly eats the budget everything below it
        is competing for. Past the ceiling, the characters the present cast's own cards
        mention are kept first and the rest are left out — the scene inspector prices
        the tier on its own row, so you can see what it costs before changing this.{" "}
        <code>0</code> names every one of them.
      </p>
      <div className="config-fields">
        <NumField id="cfg-context-budget" label="Context budget" unit="tokens"
                  placeholder="0" caption="0 = no ceiling" value={draft.context_budget}
                  onChange={(v) => edit("context_budget", v)} />
        <NumField id="cfg-context-scan-depth" label="Context scan depth"
                  unit="messages" placeholder="8"
                  caption="0 = no scan window"
                  value={draft.context_scan_depth}
                  onChange={(v) => edit("context_scan_depth", v)} />
        <NumField id="cfg-lore-recursion-depth" label="Lore recursion depth"
                  unit="levels" placeholder="0" caption="0 = off, max 3"
                  value={draft.lore_recursion_depth}
                  onChange={(v) => edit("lore_recursion_depth", v)} />
        <NumField id="cfg-archive-depth" label="Recalled scenes" placeholder="3"
                  caption="older scenes a keyword match may pull back"
                  value={draft.archive_depth}
                  onChange={(v) => edit("archive_depth", v)} />
        <NumField id="cfg-prompt-log-depth" label="Kept turn prompts" placeholder="50"
                  caption="per campaign; decisions are kept separately, as many again"
                  value={draft.prompt_log_depth}
                  onChange={(v) => edit("prompt_log_depth", v)} />
        <NumField id="cfg-offscene-known-limit" label="Named off-scene characters"
                  placeholder="40" caption="0 = name every one of them"
                  value={draft.offscene_known_limit}
                  onChange={(v) => edit("offscene_known_limit", v)} />
      </div>
      <p className="config-copy">Each present character writes a separate response. Continue, or a reply chip with an empty composer, requests one additional contribution; a reply chip with text posts it with that character leading the round.</p>
      <label className="checkbox-row">
        <input
          type="checkbox"
          aria-label="Name an active speaker in group scenes"
          checked={draft.speaker_turn_taking === "on"}
          onChange={(e) => edit("speaker_turn_taking", e.target.checked ? "on" : "off")}
        />
        Name an active speaker in group scenes
      </label>
      <p className="config-copy">
        With three or more characters in a scene, every card goes into the prompt and the
        model decides for itself who answers — which is how one character monologues for
        three turns while the others stand there. This names one to carry each turn: whoever
        the last post spoke to, or failing that whoever has been quiet longest. It is worked
        out from the transcript each turn and stored nowhere, so a reroll picks the same
        character rather than advancing a rotation you never saw. It adds a short section to
        every group-scene prompt, and does nothing in a scene with fewer than two characters.
      </p>
      <label className="checkbox-row">
        <input
          type="checkbox"
          aria-label="Send post images to models that can read them"
          checked={draft.send_images === "on"}
          onChange={(e) => edit("send_images", e.target.checked ? "on" : "off")}
        />
        Send post images to models that can read them
      </label>
      <div className="config-fields">
        <NumField id="cfg-send-images-limit" label="Images sent" placeholder="3"
                  caption="the newest in the conversation, at most 20; 0 sends none"
                  value={draft.send_images_limit}
                  disabled={draft.send_images !== "on"}
                  onChange={(v) => edit("send_images_limit", v)} />
      </div>
      <p className="config-copy">
        A picture in the transcript otherwise reaches the model as its description. With
        this on, the newest few go to the model as images — on every turn they stay in
        the conversation, so they are paid for on every one. A model without image input
        is always sent the descriptions, and under a tight budget pictures are the
        first thing given up. Whether a model reads images comes from its provider's model
        list, or from the model's vision setting on the Providers page.
      </p>
      <ImagesReachHint reach={reach} on={draft.send_images === "on"} />
      {probe && (
        <>
          <ContextBudgetBar
            ctx={probe.ctx}
            label={`LAST TURN IN ${probe.campaign.toUpperCase()}, AGAINST THIS BUDGET`}
          />
          <p className="config-copy">
            The bar is the last prompt that campaign actually built — the numbers
            already exist in the context builder, and reading them here is what makes
            the budget field mean something before you spend a turn finding out.
            {probe.ctx.budget_tokens !== Number(draft.context_budget) && (
              <> It was packed to a ceiling of{" "}
                {probe.ctx.budget_tokens > 0
                  ? probe.ctx.budget_tokens.toLocaleString()
                  : "no"}{" "}
                tokens, which is not what the field above says now.</>
            )}
          </p>
        </>
      )}
      <p className="config-copy">
        World info activates on keywords. Semantic recall adds a second pass over the
        entries the keywords missed, picking the ones closest in meaning to what has just
        been said — so the lore about a character's inherited sword can surface when the
        scene talks about the blade her mother left her. It only ever adds, never removes,
        and lore owned by an absent character stays hidden either way. Set recalled entries
        to <code>0</code> to turn recall off. It needs the Embedding role.
      </p>
      <h2 className="section-label">Historical recall</h2>
      <p className="config-copy">
        Recall bounded summaries and excerpts from earlier scenes. Set recalled scenes
        to <code>0</code> to turn it off. Embeddings are optional: structural relationships
        and keyword matches also find scenes. With an Embedding role configured, semantic
        recall sends recent scene text to its provider and may cost money. An endpoint
        that reports no price (a local or OpenAI-compatible server) makes the Costs
        totals read incomplete until you set per-token rates for its model.
      </p>
      <p className="config-copy">
        Each speaker recalls for itself: every contribution the narrator or a character
        writes runs its own retrieval, so with an Embedding role a round in which two
        characters speak embeds two queries, and three if the narrator writes too. A
        character sees only what it was present for or you marked it as knowing. Reranking adds a decision call for the
        narrator and may cost money. The live inspector previews the narrator's structural and
        keyword recall without embedding or reranking calls.
      </p>
      <div className="config-fields">
        <NumField id="cfg-history-depth" label="Historical scene depth" placeholder="0"
                  caption="0 = history recall off" value={draft.history_recall_depth}
                  onChange={(v) => edit("history_recall_depth", v)} />
        <NumField id="cfg-history-budget" label="History recall budget" placeholder="1200"
                  unit="tokens" caption="also bounded by the model window and context budget"
                  value={draft.history_recall_budget}
                  onChange={(v) => edit("history_recall_budget", v)} />
        <NumField id="cfg-history-threshold" label="History minimum cosine" decimal
                  placeholder="0.35" caption="minimum cosine similarity"
                  value={draft.history_recall_threshold}
                  onChange={(v) => edit("history_recall_threshold", v)} />
        <label className="toggle">
          <input type="checkbox" checked={draft.history_recall_rerank === "on"}
                 onChange={(e) => edit("history_recall_rerank", e.target.checked ? "on" : "off")} />
          Rerank history
        </label>
      </div>
      <p className="config-copy">
        History planning spends one short call on the Fast role when recalled history
        looks thin: it asks what earlier events the turn needs and searches once more.
        The repair step may add a decision call and one more planning call. Each may
        cost money, a model with no stated price makes the Costs totals read
        incomplete until you set its rates, and a turn that plans can wait a few seconds
        longer before its reply starts.
      </p>
      <div className="config-fields">
        <label className="toggle">
          <input type="checkbox" checked={draft.history_plan === "auto"}
                 onChange={(e) => edit("history_plan", e.target.checked ? "auto" : "off")} />
          Plan history recall
        </label>
        <label className="toggle">
          <input type="checkbox" checked={draft.history_plan_repair !== "off"}
                 disabled={draft.history_plan !== "auto"}
                 onChange={(e) => edit("history_plan_repair", e.target.checked ? "on" : "off")} />
          Repair a thin plan once
        </label>
      </div>
      <p className="config-copy">{EMBEDDINGS_COPY}</p>
      <div className="config-fields">
        <NumField id="cfg-semantic-depth" label="Recalled entries" placeholder="0"
                  caption="0 = recall off" value={draft.semantic_recall_depth}
                  onChange={(v) => edit("semantic_recall_depth", v)} />
        <NumField id="cfg-semantic-threshold" label="Similarity threshold" decimal
                  placeholder="0.4" caption="0 to 1" value={draft.semantic_recall_threshold}
                  onChange={(v) => edit("semantic_recall_threshold", v)} />
      </div>
      <p className="config-copy">
        What counts as "close enough" differs between embedding models, so tune the
        threshold (0 to 1) against the scene inspector, which shows what actually
        activated.
      </p>
      <p className="config-copy">
        <strong>This sends text to the Embedding role's provider.</strong> With the
        Embedding role set, these go to that provider as well as to the providers your
        other roles use — a second place your campaign is read: recent scene text and the
        world info being searched (recall), image descriptions (the art catalogue), the
        scenes and records being searched when you search the library by meaning, and
        plot-thread and commitment summaries after each wrap-up (finding possible
        overlaps). Choose a local provider for
        the <Link to="/models/edit">Embedding role</Link> to keep it on your
        machine.
      </p>
    </>
  );
}
