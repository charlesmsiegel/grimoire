import { useEffect, useState } from "react";
import {
  api, type CapabilityNeed, type DecidesNatively, type DecisionMode, type ModelCapabilities,
} from "../../api/client";
import { onConfigChanged } from "../../appEvents";

/** The four capability warnings of spec 10, verbatim. The first names the
 *  role it is on, so a Fast model that cannot generate says Fast. */
export const WARNINGS = {
  generate: (role: string) => `This model can't generate text, so it can't be ${role}.`,
  vision: "This route sends images; the chosen model is unverified for vision.",
  decide: "No native decision API; structured generation will be used.",
  embed: "This model can't create embeddings.",
};

/** How a decision is answered, by the resolution's `decision_mode` (spec 10,
 *  I9) -- what it says, not a warning: `native` is the provider's decisions
 *  endpoint, and `structured` on a model whose native API is there or
 *  unknown is no missing API. Structured on one KNOWN to have none
 *  (`decides_natively: "no"`) is `WARNINGS.decide`. */
export const DECIDE_WORDS = {
  native: "Answered by the provider's decisions endpoint.",
  structured: "Answered by structured generation.",
};

/** The warning one capabilities answer gives for a model, or null. Reads
 *  only what the server grouped: a hidden model is one it knows cannot. How a
 *  decision is answered is the resolution's to say (`DecideNote`), so a
 *  decide answer warns only of a model that can do neither. */
export function warningOf(answer: ModelCapabilities, model: string, role: string): string | null {
  const hidden = answer.reason !== null || answer.hidden.some((h) => h.id === model);
  switch (answer.need) {
    case "generate":
      return hidden ? WARNINGS.generate(role) : null;
    case "embed":
      return hidden ? WARNINGS.embed : null;
    case "vision":
      return answer.groups.unverified.some((r) => r.id === model) ? WARNINGS.vision : null;
    case "decide":
      return hidden ? WARNINGS.generate(role) : null;
  }
}

/** The capability warning for `model` on `provider` against `need`, from the
 *  capabilities API narrowed to that model. Nothing to ask without both. */
export function useWarning(provider: string, model: string, need: CapabilityNeed | null,
                    role: string): string | null {
  const [warning, setWarning] = useState<string | null>(null);
  // Asked again on any model-settings change -- a landed test, a facts edit
  // (both announce) -- or a warning the test just disproved outlives it.
  const [asked, setAsked] = useState(0);
  useEffect(() => onConfigChanged(() => setAsked((n) => n + 1)), []);
  useEffect(() => {
    setWarning(null);
    if (!provider || !model || !need) return;
    let current = true;
    api.readConnectionCapabilities(provider, need, model)
      .then((a) => { if (current) setWarning(warningOf(a, model, role)); })
      // A failed read warns of nothing: the role's `problem` is the seam's word.
      .catch(() => {});
    return () => { current = false; };
  }, [provider, model, need, role, asked]);
  return warning;
}

export function Warning({ text }: { text: string | null }) {
  return text ? <p className="field-hint field-warning" role="note">{text}</p> : null;
}

export function Problem({ text }: { text: string | null }) {
  return text ? <p className="field-hint problem">{text}</p> : null;
}

/** How a card's or row's model answers a decision: the server's
 *  `decision_mode` and `decides_natively` (I9), and nothing read here. A
 *  refused one (`""`) says nothing: its `problem` is the refusal's own
 *  sentence. A structured one always says which kind it is, and warns of
 *  no native API only when that is known (`"no"`): an `unknown` one -- a
 *  catalog that says nothing, or a preset whose models nobody has marked --
 *  is simply answered by structured generation. */
export function DecideNote({ mode, decidesNatively }: {
  mode: DecisionMode; decidesNatively: DecidesNatively;
}) {
  if (mode === "native") return <p className="field-hint">{DECIDE_WORDS.native}</p>;
  if (mode !== "structured") return null;
  return decidesNatively === "no"
    ? <Warning text={WARNINGS.decide} />
    : <p className="field-hint">{DECIDE_WORDS.structured}</p>;
}
