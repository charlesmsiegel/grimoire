import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <section className="route not-found" aria-labelledby="not-found-heading">
      <h2 id="not-found-heading">Not found</h2>
      <p>
        <Link to="/library">Return to the library</Link>.
      </p>
    </section>
  );
}
