import { Markdown } from "../components/Markdown";

const placeholder = `# Library

The Library houses **settings**, **style guides**, **image presets**, installed
**mechanics modules** and **plugins**. Specific views (settings, characters,
items, locations, lore, factions, mechanics, plugins) are introduced in later
frontend tasks.
`;

export function LibraryView() {
  return (
    <section className="route library-view" aria-labelledby="library-heading">
      <header>
        <h2 id="library-heading">Library</h2>
      </header>
      <Markdown>{placeholder}</Markdown>
    </section>
  );
}
