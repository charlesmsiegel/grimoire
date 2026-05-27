/**
 * Greeting form value type + helpers for converting between the form
 * shape and the (frontmatter, body) payload the library API expects.
 * Lives in its own module so `GreetingFormFields.tsx` exports only a
 * component (keeps react-refresh fast-refresh working).
 */

export interface GreetingFormValue {
  name: string;
  tagsText: string;
  roleTagsText: string;
  body: string;
  presentCharacters: string[];
  povCharacter: string;
  startingLocation: string;
  startingTime: string;
  mood: string;
}

export const emptyGreetingForm = (): GreetingFormValue => ({
  name: "",
  tagsText: "",
  roleTagsText: "",
  body: "",
  presentCharacters: [],
  povCharacter: "",
  startingLocation: "",
  startingTime: "",
  mood: "",
});

/** Build the `(frontmatter, body)` payload the library endpoints expect.
 *
 *  All optional fields are emitted even when empty so a cleared field
 *  overwrites the previous value on PATCH (which merges by key).
 *  Empty strings become `null` for fields the backend types as nullable,
 *  keeping the YAML readable.
 */
export function greetingFormToPayload(
  value: GreetingFormValue,
  id: string,
): { frontmatter: Record<string, unknown>; body: string } {
  const tags = value.tagsText
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const roleTags = value.roleTagsText
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const trimmedLocation = value.startingLocation.trim();
  const trimmedTime = value.startingTime.trim();
  const frontmatter: Record<string, unknown> = {
    id,
    name: value.name.trim(),
    tags,
    role_tags: roleTags,
    present_characters: value.presentCharacters,
    pov_character: value.povCharacter || null,
    starting_location: trimmedLocation || null,
    starting_time: trimmedTime || null,
    mood: value.mood.trim(),
  };
  return { frontmatter, body: value.body };
}
