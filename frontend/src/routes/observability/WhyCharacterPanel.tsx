/**
 * "Why this character?" past-turn debug lens.
 *
 * Given a campaign, lists recent turn audits. When the user picks a
 * turn, fetches the audit's prompt sources, filters to character-kind,
 * groups by canonical character ref (parsed from `summary`), unions
 * their inclusion reasons, and renders one card per character.
 *
 * Spec: `docs/superpowers/specs/2026-05-20-why-this-character-design.md`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  observabilityApi,
  type ContextSourceFromAudit,
  type ContextTier,
  type InclusionReason,
  type TurnAuditSummary,
  type TurnPromptResponse,
} from "../../api/observability";
import { viewsApi } from "../../api/views";
import type { ResolvedCharacter } from "../../api/types";
import { useResource } from "../../api/useResource";
import { CardIconBar } from "../../components/CardIconBar";
import { REASON_LABELS } from "./inclusionReasonLabels";

interface Props {
  campaignId: string;
}

const TIER_ORDER: Record<ContextTier, number> = {
  "lock-in": 0,
  spotlight: 1,
  background: 2,
  archive: 3,
};

interface CharacterCard {
  ref: string;
  displayName: string;
  tier: ContextTier;
  tokens: number;
  reasons: InclusionReason[];
}

function extractCharacterRef(source: ContextSourceFromAudit): string {
  const s = source.summary;
  if (s.startsWith("Active PC: ")) return s.slice("Active PC: ".length);
  if (s.startsWith("voice:")) return s.slice("voice:".length);
  if (s.startsWith("transient:")) return s.slice("transient:".length);
  // "extras-breadcrumb:" must be checked before "extras:" — the latter
  // is a prefix of the former, so the order matters.
  if (s.startsWith("extras-breadcrumb:")) return s.slice("extras-breadcrumb:".length);
  if (s.startsWith("extras:")) return s.slice("extras:".length);
  return s;
}

function groupCharacters(
  sources: ContextSourceFromAudit[],
  nameByRef: Map<string, string>,
): CharacterCard[] {
  const byRef = new Map<string, CharacterCard>();
  for (const src of sources) {
    if (src.kind !== "character") continue;
    const ref = extractCharacterRef(src);
    const existing = byRef.get(ref);
    if (!existing) {
      byRef.set(ref, {
        ref,
        displayName: nameByRef.get(ref) ?? ref,
        tier: src.tier,
        tokens: src.tokens,
        reasons: [...src.inclusion_reasons],
      });
      continue;
    }
    if (TIER_ORDER[src.tier] < TIER_ORDER[existing.tier]) {
      existing.tier = src.tier;
    }
    existing.tokens += src.tokens;
    for (const r of src.inclusion_reasons) {
      if (!existing.reasons.includes(r)) existing.reasons.push(r);
    }
  }
  return [...byRef.values()].sort(
    (a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || b.tokens - a.tokens,
  );
}

function buildNameLookup(resolved: ResolvedCharacter[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const row of resolved) {
    const id = row.character.id;
    map.set(id, row.character.name);
    if (row.character.world_id) {
      map.set(`library:${row.character.world_id}/${id}`, row.character.name);
    }
  }
  return map;
}

export function WhyCharacterPanel({ campaignId }: Props) {
  const [turns, setTurns] = useState<TurnAuditSummary[] | null>(null);
  const [turnsError, setTurnsError] = useState<string | null>(null);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);

  // Characters and the selected turn's prompt are plain on-mount/on-change
  // reads. The turns list stays a hand-rolled effect because it also resets
  // the user's turn selection when the campaign changes.
  const charactersState = useResource(
    useCallback(() => viewsApi.listCharacters(campaignId), [campaignId]),
  );
  const characters = useMemo(() => charactersState.data ?? [], [charactersState.data]);

  const promptState = useResource(
    useCallback(
      () =>
        selectedTurnId
          ? observabilityApi.getTurnPrompt(selectedTurnId)
          : Promise.resolve<TurnPromptResponse | null>(null),
      [selectedTurnId],
    ),
  );
  const prompt = promptState.data;
  const promptError = promptState.error?.message ?? null;

  useEffect(() => {
    let cancelled = false;
    setTurns(null);
    setSelectedTurnId(null);
    observabilityApi
      .listTurns(campaignId)
      .then((rows) => {
        if (!cancelled) {
          setTurns(rows);
          setTurnsError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setTurnsError(err instanceof Error ? err.message : String(err));
          setTurns([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  const nameByRef = useMemo(() => buildNameLookup(characters), [characters]);
  const cards = useMemo(
    () => (prompt ? groupCharacters(prompt.sources, nameByRef) : []),
    [prompt, nameByRef],
  );

  return (
    <section className="why-character-panel" aria-label="Why this character?">
      <header>
        <h2>Why this character?</h2>
        <p className="why-character-sub">Past-turn debug view — per-character inclusion reasons.</p>
      </header>

      <div className="why-character-layout">
        <aside className="why-character-turns" aria-label="Turns">
          {turns === null && <p className="why-character-loading">Loading turns…</p>}
          {turnsError && <p className="why-character-error">{turnsError}</p>}
          {turns !== null && turns.length === 0 && !turnsError && (
            <p className="empty-state">No audits yet for this campaign.</p>
          )}
          {turns !== null && turns.length > 0 && (
            <ul>
              {turns.map((t) => (
                <li key={t.turn_id}>
                  <button
                    type="button"
                    aria-pressed={selectedTurnId === t.turn_id}
                    className={selectedTurnId === t.turn_id ? "is-active" : ""}
                    onClick={() => setSelectedTurnId(t.turn_id)}
                  >
                    <span className="why-character-turn-id">{t.turn_id}</span>
                    <span className="why-character-turn-time">
                      {new Date(t.started_at).toLocaleString()}
                    </span>
                    <span className="why-character-turn-input">
                      {t.player_input.slice(0, 60)}
                      {t.player_input.length > 60 ? "…" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <main className="why-character-cards" aria-label="Character cards">
          {!selectedTurnId && <p className="empty-state">Pick a turn to inspect.</p>}
          {selectedTurnId && !prompt && !promptError && (
            <p className="why-character-loading">Loading audit…</p>
          )}
          {promptError && <p className="empty-state">No audit available for that turn.</p>}
          {prompt && cards.length === 0 && (
            <p className="empty-state">This turn's context had no character sources.</p>
          )}
          {cards.map((card) => (
            <article
              key={card.ref}
              data-testid={`character-card-${card.ref}`}
              className={`card why-character-card why-character-tier-${card.tier}`}
            >
              <header>
                <h3>{card.displayName}</h3>
                {card.displayName !== card.ref && (
                  <small className="why-character-ref">{card.ref}</small>
                )}
                <span className="why-character-tier">{card.tier}</span>
                <span className="why-character-tokens">{card.tokens.toLocaleString()} tok</span>
              </header>
              <ul className="why-character-reasons">
                {card.reasons.length === 0 ? (
                  <li className="empty-state">(no declared reason)</li>
                ) : (
                  card.reasons.map((r) => (
                    <li key={r} className={`chip why-character-reason why-character-reason-${r}`}>
                      {REASON_LABELS[r] ?? r}
                    </li>
                  ))
                )}
              </ul>
              <CardIconBar actions={[]} />
            </article>
          ))}
        </main>
      </div>
    </section>
  );
}
