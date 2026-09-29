import { useCallback, useEffect, useState } from "react";
import { api, STYLE_CLEAR, type ResponseBundle, type ResponseFields, type Style } from "../api/client";
import { errorText } from "../api/errors";

const EMPTY: ResponseFields = {
  response_preset: "", style_id: "", length_reply_words: "", length_blocks: "",
  length_paragraphs: "", length_speakers: "", length_blocks_per_speaker: "",
  response_opening_words: "", response_opening_paragraphs: "",
  response_continuation_words: "", response_continuation_paragraphs: "",
};

const TARGETS = [
  { phase: "opening", unit: "words", label: "Opening words" },
  { phase: "opening", unit: "paragraphs", label: "Opening paragraphs" },
  { phase: "continuation", unit: "words", label: "Continuation words" },
  { phase: "continuation", unit: "paragraphs", label: "Continuation paragraphs" },
] as const;

type Props = {
  scope: "global" | "campaign" | "scene";
  cid?: string;
  sid?: string;
  onChanged?: () => void;
};

export function ResponseTargetsPicker({ scope, cid, sid, onChanged }: Props) {
  const [bundle, setBundle] = useState<ResponseBundle | null>(null);
  const [fields, setFields] = useState<ResponseFields>(EMPTY);
  const [styles, setStyles] = useState<Style[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    const response = scope === "global" ? await api.getGlobalResponse()
      : scope === "campaign" ? await api.getCampaignResponse(cid!)
      : await api.getSceneResponse(cid!, sid!);
    setBundle(response);
    setFields({ ...EMPTY, ...response });
  }, [scope, cid, sid]);

  useEffect(() => {
    void load().catch(() => setBundle(null));
    void api.listStyles().then(setStyles).catch(() => setStyles([]));
  }, [load]);

  async function save() {
    setSaved(false);
    setError(null);
    try {
      // Old fields may still exist on disk. Omit them so this control only
      // writes the supported targets and style at its selected scope.
      const next = Object.fromEntries(TARGETS.map(({ phase, unit }) => {
        const key = `response_${phase}_${unit}` as keyof ResponseFields;
        return [key, fields[key]];
      })) as Partial<ResponseFields>;
      next.style_id = fields.style_id;
      if (scope === "global") await api.setGlobalResponse(next);
      else if (scope === "campaign") await api.setCampaignResponse(cid!, next);
      else await api.setSceneResponse(cid!, sid!, next);
      await load();
      setSaved(true);
      onChanged?.();
    } catch (err: unknown) {
      setError(errorText(err));
    }
  }

  return (
    <div className="response-targets-picker">
      {error && <div className="banner">{error}</div>}
      <p className="field-hint">Approximate targets for each generated contribution.</p>
      {TARGETS.map(({ phase, unit, label }) => {
        const key = `response_${phase}_${unit}` as keyof ResponseFields;
        const source = bundle?.provenance[`${phase}.${unit}`]?.scope ?? "default";
        const value = bundle?.effective[phase][unit];
        return (
          <label key={key}>
            {label}
            <input type="number" min="1" aria-label={label} value={fields[key]}
              placeholder={value == null ? undefined : String(value)}
              onChange={(event) => setFields((current) => ({ ...current, [key]: event.target.value }))} />
            {bundle && <span className="field-hint">{value} from {source}</span>}
          </label>
        );
      })}
      <label>
        Style
        <select aria-label="Style" value={fields.style_id}
          onChange={(event) => setFields((current) => ({ ...current, style_id: event.target.value }))}>
          <option value="">Inherit</option>
          <option value={STYLE_CLEAR}>No style</option>
          {styles.map((style) => <option value={style.id} key={style.id}>{style.name}</option>)}
        </select>
      </label>
      <button type="button" className="primary" onClick={() => void save()}>Save response targets</button>
      {saved && <span className="field-hint">Saved.</span>}
    </div>
  );
}
