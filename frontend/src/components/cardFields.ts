/** The V3 card's prose fields, in the order a reader reads a card.
 *
 *  Label-only, and deliberately a second list rather than an import from
 *  `CharacterEditor`: that one is the *editing* order and carries an `area`
 *  flag that picks the form control, which a read-only view has no use for.
 *  Keyed the same otherwise, so a prose field added to the card belongs in both.
 *
 *  Not the whole card: a V3 card also carries greetings, tags, an embedded
 *  lorebook and `extensions`, none of which is prose and none of which is
 *  compared here. `IncomingReview` says so out loud when the compared fields
 *  come back identical, rather than letting a change it cannot show read as no
 *  change at all.
 */
export const CARD_TEXT_FIELDS: { key: string; label: string }[] = [
  // The name leads, and is the one entry `CharacterEditor`'s list does not have:
  // there it has its own control above the prose fields, but a world-side
  // rename is a change a reader has to be able to see before taking it.
  { key: "name", label: "Name" },
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
