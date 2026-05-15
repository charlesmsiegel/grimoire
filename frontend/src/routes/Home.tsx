import { Link } from "react-router-dom";

export function Home() {
  return (
    <section className="route home" aria-labelledby="home-heading">
      <header className="home-hero">
        <h2 id="home-heading">Grimoire</h2>
        <p className="home-tagline">A local-first RPG campaign companion.</p>
      </header>

      <div className="home-body">
        <p>
          Grimoire sits between you and the language model so long-form RPG play stays
          coherent: characters keep their voices, scenes have boundaries, time advances,
          and foreshadowing isn&apos;t forgotten. A thin Orchestrator assembles context
          deterministically, calls the LLM, parses output into structured state, and
          updates a typed data model. The model becomes a service, not the driver.
        </p>
        <p>
          The name comes from the magical-book tradition — a single bound volume holding
          the spells, lore, beings, and rules of a world. Your Grimoire holds the
          settings you play in, the characters who inhabit them, the rules they play
          under, and the chronicles of what they&apos;ve done.
        </p>

        <h3>Three scopes</h3>
        <dl className="home-scopes">
          <div>
            <dt>Library</dt>
            <dd>
              Content you author — settings, characters, items, locations, lore,
              factions, style guides, and image presets.
            </dd>
          </div>
          <div>
            <dt>Mechanics</dt>
            <dd>
              The game system that governs a campaign — WoD, Ars Magica, Blades, D&amp;D.
              Installed as external modules.
            </dd>
          </div>
          <div>
            <dt>Plugins</dt>
            <dd>
              Shallow adapters for LLM providers, embeddings, image backends, and export
              formats.
            </dd>
          </div>
        </dl>

        <nav className="home-actions" aria-label="Get started">
          <Link to="/library" className="home-action">
            Open the Library
          </Link>
          <Link to="/campaigns" className="home-action">
            Browse Campaigns
          </Link>
          <Link to="/settings" className="home-action home-action-quiet">
            App Settings
          </Link>
        </nav>
      </div>
    </section>
  );
}
