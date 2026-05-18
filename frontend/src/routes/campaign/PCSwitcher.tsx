/**
 * Rich PC switcher (spec frontend §8).
 *
 * Replaces the bare ``<select>`` with a Radix Popover-backed listbox that
 * renders rich rows: "Aleksandr (vampire) — scene 47, Camden club, last
 * played 12m ago". Selecting a row fires ``onChange`` (the play state hook
 * then POSTs ``/pcs/{ref}/set-active`` and refreshes so the active scene
 * re-orients to that PC's ``current_scene_id``).
 */

import * as Popover from "@radix-ui/react-popover";
import { useMemo, useState } from "react";

import type { PCEntry } from "../../api/campaign";

interface Props {
  pcs: PCEntry[];
  activePcRef: string | null;
  onChange: (ref: string) => void;
}

export function PCSwitcher({ pcs, activePcRef, onChange }: Props) {
  const [open, setOpen] = useState(false);

  const active = useMemo<PCEntry | null>(
    () => pcs.find((p) => p.character_ref === activePcRef) ?? pcs[0] ?? null,
    [pcs, activePcRef],
  );

  if (pcs.length === 0) {
    return (
      <div className="pc-switcher pc-switcher-empty" aria-live="polite">
        No PCs in this campaign yet.
      </div>
    );
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          className="pc-switcher pc-switcher-trigger"
          aria-label="Active PC — click to switch"
          aria-haspopup="listbox"
          aria-expanded={open}
        >
          <span className="pc-switcher-label">PC</span>
          <span className="pc-switcher-name">{active?.name ?? "—"}</span>
          <span className="pc-switcher-caret" aria-hidden="true">
            ▾
          </span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="pc-switcher-popover"
          sideOffset={6}
          align="start"
          role="listbox"
          aria-label="Switch active PC"
        >
          <ul className="pc-switcher-list">
            {pcs.map((pc) => {
              const isActive = pc.character_ref === active?.character_ref;
              return (
                <li key={pc.character_ref}>
                  <button
                    type="button"
                    className={
                      isActive
                        ? "pc-switcher-row pc-switcher-row-active"
                        : "pc-switcher-row"
                    }
                    role="option"
                    aria-selected={isActive}
                    onClick={() => {
                      onChange(pc.character_ref);
                      setOpen(false);
                    }}
                  >
                    <div className="pc-switcher-row-head">
                      <strong>{pc.name}</strong>
                      {isActive && <span className="badge">active</span>}
                    </div>
                    <div className="pc-switcher-row-meta">
                      {pc.current_scene_id && <span>scene {shortId(pc.current_scene_id)}</span>}
                      {pc.current_location_ref && (
                        <span>{prettyRef(pc.current_location_ref)}</span>
                      )}
                      {pc.last_played_at && (
                        <span>last played {formatRelative(pc.last_played_at)}</span>
                      )}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function shortId(id: string): string {
  // Scene ids are typically slug-shaped; keep them short in the row.
  const trimmed = id.replace(/^scene[-_]?/i, "");
  return trimmed.length > 20 ? `${trimmed.slice(0, 18)}…` : trimmed;
}

function prettyRef(ref: string): string {
  // "library:worlds/wod-london/locations/camden-club" -> "camden-club"
  const tail = ref.split("/").pop();
  return tail ?? ref;
}

function formatRelative(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}
