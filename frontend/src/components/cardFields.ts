/** The V3 card's prose fields, in the order a reader reads a card.
 *
 *  Label-only, and deliberately a second list rather than an import from
 *  `CharacterEditor`: that one is the *editing* order and carries an `area`
 *  flag that picks the form control, which a read-only view has no use for.
 *  The two are keyed identically, so a field added to the card belongs in both.
 */
export const CARD_TEXT_FIELDS: { key: string; label: string }[] = [
  { key: "description", label: "Description" },
  { key: "personality", label: "Personality" },
  { key: "scenario", label: "Scenario" },
  { key: "first_mes", label: "First message" },
  { key: "mes_example", label: "Example dialogue" },
  { key: "system_prompt", label: "System prompt" },
  { key: "post_history_instructions", label: "Post-history instructions" },
  { key: "creator_notes", label: "Creator notes" },
];

/** A PC's persona, same idea: the fields a reader compares, in reading order. */
export const PERSONA_FIELDS: { key: string; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "pronouns", label: "Pronouns" },
  { key: "summary", label: "Summary" },
  { key: "description", label: "Description" },
];
