import { useId, useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { CharacterSummary, GreetingSummary } from "../../api/wizard";
import type { WizardDraft } from "./types";

interface Props {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  greetings: GreetingSummary[];
  loading: boolean;
  error: string | null;
  castBySetting: Map<string, CharacterSummary[]>;
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
  castBySetting,
}: Props) {
  const selectedGreeting = greetings.find((g) => g.id === draft.greetingId) ?? null;

  const candidates = useMemo<CastCandidate[]>(() => {
    const byId = new Map<string, CastCandidate>();
    for (const pc of draft.pcs) {
      byId.set(pc.character_ref, {
        id: pc.character_ref,
        name: pc.name || pc.character_ref,
        source: "PC",
      });
    }
    for (const [settingId, chars] of castBySetting) {
      for (const c of chars) {
        if (byId.has(c.id)) continue;
        byId.set(c.id, { id: c.id, name: c.name ?? c.id, source: settingId });
      }
    }
    return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
  }, [draft.pcs, castBySetting]);

  const setCast = (next: string[]) => update({ startingCast: next });

  return (
    <div className="wizard-step">
      <h3>Step 6 — Starting scene</h3>
      <p className="wizard-step-help">
        Pick a greeting from the composed settings or skip to start with a blank scene. Confirm the
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
            const greeting = greetings.find((g) => g.id === id);
            update({
              greetingId: id,
              startingLocation: greeting?.starting_location ?? draft.startingLocation,
              startingTime: greeting?.starting_time ?? draft.startingTime,
            });
          }}
        >
          <option value="">— blank start —</option>
          {greetings.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name ?? g.id}
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
        <dt>Settings</dt>
        <dd>
          {draft.settingRefs.length === 0 ? (
            <em>none — pick at least one</em>
          ) : (
            draft.settingRefs.map((r) => r.setting_id).join(", ")
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

  const selected = new Set(value);
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
      if (matches[0]) {
        add(matches[0].id);
      } else if (query.trim()) {
        // Allow typing a free-form ref the user knows isn't in the list.
        add(query.trim());
      }
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
                <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => add(c.id)}>
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
