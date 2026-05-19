/**
 * Inline dropdown the active PC uses to choose their expression for
 * the post they're about to submit.
 *
 * Lists ``CoreExpression`` labels plus any installed mechanics module's
 * extensions, defaulting to ``neutral``. The composer calls
 * ``setPcExpression`` after the post is submitted, passing the just-
 * minted post_id, so the chosen emotion is recorded against that post.
 */

import { useEffect, useState } from "react";

import { fetchVocabulary, type ExpressionVocabulary } from "../../api/expressions";

interface Props {
  value: string;
  onChange: (emotion: string) => void;
  disabled?: boolean;
}

const FALLBACK_VOCABULARY: ExpressionVocabulary = {
  core: [
    "neutral",
    "happy",
    "sad",
    "angry",
    "surprised",
    "fearful",
    "disgusted",
    "smug",
    "thoughtful",
    "embarrassed",
    "determined",
    "hurt",
    "tired",
    "suspicious",
  ],
  extensions: {},
};

function flattenVocabulary(vocab: ExpressionVocabulary): string[] {
  const labels = [...vocab.core];
  for (const [moduleId, exts] of Object.entries(vocab.extensions)) {
    for (const label of exts) {
      labels.push(`${moduleId}.${label}`);
    }
  }
  return labels;
}

export function ExpressionPicker({ value, onChange, disabled = false }: Props) {
  const [vocab, setVocab] = useState<ExpressionVocabulary>(FALLBACK_VOCABULARY);

  useEffect(() => {
    let active = true;
    fetchVocabulary()
      .then((v) => {
        if (active) setVocab(v);
      })
      .catch(() => {
        // Network blip: stick with the fallback core list.
      });
    return () => {
      active = false;
    };
  }, []);

  const options = flattenVocabulary(vocab);

  return (
    <label className="expression-picker">
      <span className="expression-picker-label">Expression</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        aria-label="Set PC expression for this post"
      >
        {options.map((label) => (
          <option key={label} value={label}>
            {label}
          </option>
        ))}
      </select>
    </label>
  );
}
