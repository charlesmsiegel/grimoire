import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { CharacterSummary, GreetingSummary } from "../../api/wizard";
import type { WizardDraft } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  greetings: GreetingSummary[];
  loading: boolean;
  error: string | null;
  castByWorld: Map<string, CharacterSummary[]>;
}

interface CastCandidate {
  id: string;
  name: string;
  source: string;
}

export function StepStartingScene({
  draft,
  update,
  greetings,
  loading,
  error,
  castByWorld,
}: Props) {
  const pcRoleTagUnion = useMemo(() => {
    const tags = new Set<string>();
    for (const pc of draft.pcs) {
      for (const t of pc.role_tags) tags.add(t);
    }
    return tags;
  }, [draft.pcs]);

  const filteredGreetings = useMemo(() => {
    return greetings.filter((g) => {
      if (!g.role_tags || g.role_tags.length === 0) return true;
      if (pcRoleTagUnion.size === 0) return false;
      return g.role_tags.some((t) => pcRoleTagUnion.has(t));
    });
  }, [greetings, pcRoleTagUnion]);

  useEffect(() => {
    if (draft.greetingId && !filteredGreetings.some((g) => g.id === draft.greetingId)) {
      update({ greetingId: null });
    }
  }, [filteredGreetings, draft.greetingId, update]);

  const selectedGreeting = filteredGreetings.find((g) => g.id === draft.greetingId) ?? null;

  const candidates = useMemo<CastCandidate[]>(() => {
    // Refs are worldId-qualified so a character "protagonist" can coexist
    // across multiple worlds without collapsing. PCs already store their
    // full character_ref (e.g. "wod-london/alex") so they don't need
    // re-qualification.
    const byRef = new Map<string, CastCandidate>();
    for (const pc of draft.pcs) {
      byRef.set(pc.character_ref, {
        id: pc.character_ref,
        name: pc.name || pc.character_ref,
        source: "PC",
      });
    }
    for (const [worldId, chars] of castByWorld) {
      for (const c of chars) {
        const ref = `${worldId}/${c.id}`;
        if (byRef.has(ref)) continue;
        byRef.set(ref, { id: ref, name: c.name ?? c.id, source: worldId });
      }
    }
    return [...byRef.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [draft.pcs, castByWorld]);

  const setCast = (next: string[]) => update({ startingCast: next });

  return (
    <div className="wizard-step">
      <h3>Step 6 — Starting scene</h3>
      <p className="wizard-step-help">
        Pick a greeting from the composed worlds or skip to start with a blank scene. Confirm the
        opening location, time, and cast.
      </p>

      {loading && <p className="wizard-meta">Loading greetings…</p>}
      {error && <p className="wizard-error">{error}</p>}

      <label className="wizard-field">
        <span>Greeting</span>
        <select
          value={draft.greetingId ?? ""}
          onChange={(e) => {
            const id = e.target.value || null;
            const greeting = filteredGreetings.find((g) => g.id === id);
            update({
              greetingId: id,
              startingLocation: greeting?.starting_location ?? draft.startingLocation,
              startingTime: greeting?.starting_time ?? draft.startingTime,
            });
          }}
        >
          <option value="">— blank start —</option>
          {filteredGreetings.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name ?? g.id}
              {g.role_tags.length > 0 ? ` [${g.role_tags.join(", ")}]` : ""}
            </option>
          ))}
        </select>
        {selectedGreeting?.description && (
          <small className="wizard-meta">{selectedGreeting.description}</small>
        )}
      </label>

      <label className="wizard-field">
        <span>Starting location</span>
        <input
          type="text"
          value={draft.startingLocation}
          onChange={(e) => update({ startingLocation: e.target.value })}
          placeholder="Whitechapel chantry"
        />
      </label>

      <label className="wizard-field">
        <span>In-game time</span>
        <input
          type="text"
          value={draft.startingTime}
          onChange={(e) => update({ startingTime: e.target.value })}
          placeholder="1888-01-15 22:00"
        />
      </label>

      <CastInput value={draft.startingCast} onChange={setCast} candidates={candidates} />

      <h4>Review</h4>
      <dl className="wizard-summary">
        <dt>Id</dt>
        <dd>{draft.id || <em>missing</em>}</dd>
        <dt>Name</dt>
        <dd>{draft.name || <em>missing</em>}</dd>
        <dt>Worlds</dt>
        <dd>
          {draft.worldRefs.length === 0 ? (
            <em>none — pick at least one</em>
          ) : (
            draft.worldRefs.map((r) => r.world_id).join(", ")
          )}
        </dd>
        <dt>Mechanics</dt>
        <dd>{draft.mechanicsId ?? <em>none</em>}</dd>
        <dt>PCs</dt>
        <dd>
          {draft.pcs.length === 0 ? (
            <em>none — add at least one</em>
          ) : (
            draft.pcs.map((p) => p.name).join(", ")
          )}
        </dd>
      </dl>
    </div>
  );
}

interface CastInputProps {
  value: string[];
  onChange: (next: string[]) => void;
  candidates: CastCandidate[];
}

function CastInput({ value, onChange, candidates }: CastInputProps) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();

  const selected = useMemo(() => new Set(value), [value]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return candidates
      .filter((c) => !selected.has(c.id))
      .filter((c) => !q || c.name.toLowerCase().includes(q) || c.id.toLowerCase().includes(q))
      .slice(0, 8);
  }, [query, candidates, selected]);

  const add = (id: string) => {
    if (selected.has(id)) return;
    onChange([...value, id]);
    setQuery("");
    inputRef.current?.focus();
  };

  const remove = (id: string) => {
    onChange(value.filter((v) => v !== id));
  };

  const labelFor = (id: string): string => {
    const c = candidates.find((x) => x.id === id);
    return c?.name ?? id;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      // Only add when there's a matching candidate. Free-form refs (no
      // world prefix) would 400 at the backend, so we'd rather wait for
      // the user to pick or refine than commit something that won't work.
      if (matches[0]) add(matches[0].id);
    } else if (e.key === "Backspace" && query === "" && value.length > 0) {
      remove(value[value.length - 1]!);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="wizard-field">
      <span>Present cast</span>
      <div className="wizard-cast-input">
        <div className="wizard-cast-chips" onClick={() => inputRef.current?.focus()}>
          {value.map((id) => (
            <span key={id} className="wizard-cast-chip">
              <span>{labelFor(id)}</span>
              <button
                type="button"
                aria-label={`Remove ${labelFor(id)}`}
                onClick={() => remove(id)}
              >
                ⨯
              </button>
            </span>
          ))}
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => setTimeout(() => setOpen(false), 150)}
            onKeyDown={onKeyDown}
            placeholder={value.length === 0 ? "Type to add a character…" : ""}
            aria-autocomplete="list"
            aria-controls={listboxId}
            aria-expanded={open}
          />
        </div>
        {open && matches.length > 0 && (
          <ul id={listboxId} className="wizard-cast-suggestions" role="listbox">
            {matches.map((c) => (
              <li key={c.id} role="option">
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => add(c.id)}
                >
                  <strong>{c.name}</strong>
                  <small>
                    {c.id} · {c.source}
                  </small>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <small>Type a name to search the cast and PCs; Enter or click to add.</small>
    </div>
  );
}
