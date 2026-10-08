import type { CapabilityName, CapabilityValue } from "../../api/client";

/** The four capabilities a provider's model list shows (spec 10), each with
 *  the word it is badged as. */
const SHOWN: { name: CapabilityName; label: string }[] = [
  { name: "generate", label: "generate" },
  { name: "vision", label: "vision" },
  { name: "embed", label: "embed" },
  { name: "decide_native", label: "decide" },
];

/** How a value reads. `unknown` is "unverified", never a no: it hides nothing,
 *  and a failed test lands there too (spec 6.2). */
const WORD: Record<CapabilityValue["value"], string> = {
  yes: "yes", no: "no", unknown: "unverified",
};

function title(cap: CapabilityValue | undefined): string {
  if (!cap) return "Nothing has said whether this model can do this.";
  const from = `Source: ${cap.source}`;
  return cap.error ? `${from}. The last test failed: ${cap.error}` : from;
}

/** Yes, no or unverified for each capability, as the server resolved it. A
 *  capability the answer leaves out is unverified; the source -- and a failed
 *  test's error -- is in the badge's title. */
export function CapabilityBadges({ capabilities }:
  { capabilities: Partial<Record<CapabilityName, CapabilityValue>> }) {
  return (
    <span className="chips cap-badges">
      {SHOWN.map(({ name, label }) => {
        const cap = capabilities[name];
        const value = cap?.value ?? "unknown";
        return (
          <span key={name} className={`chip cap-badge ${value}`} title={title(cap)}>
            {label}: {WORD[value]}
          </span>
        );
      })}
    </span>
  );
}
