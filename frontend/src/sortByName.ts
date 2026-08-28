/** One comparator for every list of named records the app shows A–Z.
 *
 *  Five surfaces sort by name — the campaign shelf, the Worlds grid, the
 *  campaigns page's world filter column, the wizard's world picker, and the
 *  palette's empty-query listing. Five hand-written `localeCompare` calls is
 *  five chances for two of them to disagree about case, about digits, or about
 *  the article rule below, which reads as a bug in whichever one the user
 *  looked at second.
 *
 *  `sensitivity: "base"` so a world typed in lower case does not sort into its
 *  own block below the capitalised ones (and so an accented letter files with
 *  its unaccented form rather than after Z), and `numeric: true` so "Chapter 2"
 *  precedes "Chapter 10" rather than following it. Both are what a reader
 *  scanning for a name expects, and neither is the default.
 *
 *  Copies first: every caller holds its array in React state, and `sort`
 *  mutates in place.
 */
const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

/** A leading English article, which a shelf sorts *past* rather than on.
 *
 *  A library naming things the way people name stories fills its T section
 *  with everything called "The ..." — which is the one letter that then tells
 *  the reader nothing, and it is worst exactly where the sort was supposed to
 *  help most. So "The Verdigris Crown" files under V, the way a bookshelf, a
 *  record shop and a library catalogue all file it.
 *
 *  Only these three, and only followed by a space: this is a spelling rule
 *  about English articles, not a general stop-word list. Stripping a word
 *  because it is short and common is how "A Long Run" and "An Ending" get
 *  filed correctly and "Of Ash and Salt" gets filed under A.
 */
const ARTICLE = /^(?:the|an|a)\s+/i;

/** What a name files under. The name itself is never rewritten — this decides
 *  position only, so what the reader sees is still what the store holds. */
function fileUnder(name: string): string {
  const trimmed = name.trim();
  // A record actually called "The" keeps it: dropping the article would leave
  // an empty key, and everything with one would then tie at the very top.
  return trimmed.replace(ARTICLE, "") || trimmed;
}

export function byName<T extends { name: string }>(rows: readonly T[]): T[] {
  return [...rows].sort((a, b) =>
    collator.compare(fileUnder(a.name), fileUnder(b.name))
    // The full name breaks a tie, so "Ashfall" and "The Ashfall" land in a
    // stable order rather than wherever the sort happened to leave them.
    || collator.compare(a.name, b.name));
}
