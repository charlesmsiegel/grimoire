import { useCallback, useEffect, useState } from "react";
import {
  api, STYLE_CLEAR, type ResponseBundle, type ResponseEffective, type ResponseFields,
  type ResponsePresetSummary, type Style,
} from "../api/client";

const EMPTY_FIELDS: ResponseFields = {
  response_preset: "", style_id: "",
  length_reply_words: "", length_blocks: "", length_paragraphs: "",
  length_speakers: "", length_blocks_per_speaker: "",
};

const KNOB_FIELDS: {
  key: Exclude<keyof ResponseFields, "response_preset" | "style_id">;
  label: string;
  effectiveKey: Exclude<keyof ResponseEffective, "style_id">;
  unit: string;
}[] = [
  { key: "length_reply_words", label: "Target words per reply", effectiveKey: "reply_words", unit: "words per reply" },
  { key: "length_blocks", label: "Max blocks per reply", effectiveKey: "blocks", unit: "blocks per reply" },
  { key: "length_paragraphs", label: "Max paragraphs per block", effectiveKey: "paragraphs", unit: "paragraphs per block" },
  { key: "length_speakers", label: "Max speaking characters", effectiveKey: "speakers", unit: "speaking characters" },
  { key: "length_blocks_per_speaker", label: "Max blocks per character", effectiveKey: "blocks_per_speaker", unit: "blocks per character" },
];

function scopeLabel(scope: string | undefined): string {
  switch (scope) {
    case "turn": return "this turn";
    case "scene": return "this scene";
    case "campaign": return "this campaign";
    case "global": return "the global default";
    case "default": return "the built-in default";
    default: return "an unknown scope";
  }
}

export function ResponsePresetPicker(
  { scope, cid, sid, onChanged }: {
    scope: "global" | "campaign" | "scene"; cid?: string; sid?: string;
    // Fired after a successful write so surfaces that render the RESOLVED
    // bundle elsewhere (the composer chip) can re-read it. Without this, a
    // change made here leaves the chip advertising the previous setting.
    onChanged?: () => void;
  },
) {
  const [presets, setPresets] = useState<ResponsePresetSummary[]>([]);
  const [styles, setStyles] = useState<Style[]>([]);
  const [fields, setFields] = useState<ResponseFields>(EMPTY_FIELDS);
  const [effective, setEffective] = useState<ResponseEffective | null>(null);
  const [provenance, setProvenance] = useState<ResponseBundle["provenance"]>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    const get = scope === "global" ? api.getGlobalResponse()
      : scope === "campaign" ? api.getCampaignResponse(cid!)
      : api.getSceneResponse(cid!, sid!);
    return get.then((r) => {
      const { effective: eff, provenance: prov, ...own } = r;
      setFields(own);
      setEffective(eff);
      setProvenance(prov);
    }).catch(() => {
      setFields(EMPTY_FIELDS);
      setEffective(null);
      setProvenance({});
    });
  }, [scope, cid, sid]);

  useEffect(() => {
    api.listResponsePresets().then(setPresets).catch(() => setPresets([]));
    // listStyles isn't relevant to every mount of this picker (e.g. it isn't
    // exercised by every scope's test double) — resolve defensively so an
    // unmocked/failed call can't crash the Overrides style picker.
    Promise.resolve(api.listStyles()).then((r) => setStyles(r ?? [])).catch(() => setStyles([]));
    load();
  }, [load]);

  async function persist(next: ResponseFields) {
    setFields(next);
    setError(null);
    setSaved(false);
    try {
      if (scope === "global") await api.setGlobalResponse(next);
      else if (scope === "campaign") await api.setCampaignResponse(cid!, next);
      else await api.setSceneResponse(cid!, sid!, next);
      setSaved(true);
      await load();
      onChanged?.();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  function choosePreset(id: string) {
    void persist({ ...fields, response_preset: id });
  }

  async function saveAsPreset() {
    if (!effective) return;
    const name = window.prompt("Name this preset?")?.trim();
    if (!name) return;
    setError(null);
    try {
      const { id } = await api.createResponsePreset({
        name,
        // A resolved style of "" means "no style at all", and the preset has to
        // SAY so: saved as "" it would read back as "no opinion" and applying
        // the preset under a campaign that has a style would resurrect it.
        style_id: effective.style_id || STYLE_CLEAR,
        knobs: {
          reply_words: effective.reply_words, blocks: effective.blocks, paragraphs: effective.paragraphs,
          speakers: effective.speakers, blocks_per_speaker: effective.blocks_per_speaker,
        },
      });
      await api.listResponsePresets().then(setPresets).catch(() => {});
      choosePreset(id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="response-preset-picker">
      {error && <div className="banner">{error}</div>}
      <label>
        Response preset
        <select aria-label="Response preset" value={fields.response_preset}
                onChange={(e) => choosePreset(e.target.value)}>
          <option value="">— none —</option>
          {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          {/* A scope can name a preset the list hasn't loaded yet, or one that
              was since deleted — show its id rather than silently falling
              back to the blank option, which would misreport "no preset". */}
          {fields.response_preset && !presets.some((p) => p.id === fields.response_preset) && (
            <option value={fields.response_preset}>{fields.response_preset}</option>
          )}
        </select>
      </label>

      {effective && (
        <ul className="response-effective">
          <li>{effective.reply_words} words per reply — from {scopeLabel(provenance.reply_words?.scope)}</li>
          <li>Up to {effective.blocks} blocks per reply — from {scopeLabel(provenance.blocks?.scope)}</li>
          <li>Up to {effective.paragraphs} paragraphs per block — from {scopeLabel(provenance.paragraphs?.scope)}</li>
          <li>Up to {effective.speakers} speaking characters — from {scopeLabel(provenance.speakers?.scope)}</li>
          <li>Up to {effective.blocks_per_speaker} blocks per character — from {scopeLabel(provenance.blocks_per_speaker?.scope)}</li>
          <li>Style: {effective.style_id || "no style"} — from {scopeLabel(provenance.style_id?.scope)}</li>
        </ul>
      )}

      <details className="response-overrides">
        <summary>Overrides</summary>
        <div className="response-overrides-body">
          <label>
            Style
            <select aria-label="Style" value={fields.style_id}
                    onChange={(e) => setFields((f) => ({ ...f, style_id: e.target.value }))}>
              <option value="">
                {effective ? `— inherit${effective.style_id ? ` (${effective.style_id})` : ""} —` : "— inherit —"}
              </option>
              {/* The clear sentinel. Distinct from inherit: it stops the walk
                  with an explicit clear, so a broader scope's style does NOT
                  apply. Without it the tri-state is unreachable from the UI —
                  and its value can never be a style id, so a user style called
                  "None" still lists (and selects) as itself below. */}
              <option value={STYLE_CLEAR}>— no style (clear inherited) —</option>
              {styles.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          {KNOB_FIELDS.map(({ key, label, effectiveKey }) => (
            <label key={key}>
              {label}
              <input
                type="number"
                aria-label={label}
                value={fields[key]}
                placeholder={effective ? String(effective[effectiveKey]) : undefined}
                onFocus={(e) => e.currentTarget.select()}
                onChange={(e) => setFields((f) => ({ ...f, [key]: e.target.value }))}
              />
            </label>
          ))}
          <button className="primary" type="button" onClick={() => persist(fields)}>Save</button>
          <button className="subtle" type="button" onClick={saveAsPreset}>Save as preset…</button>
          {saved && <span className="field-hint">Saved.</span>}
        </div>
      </details>
    </div>
  );
}
