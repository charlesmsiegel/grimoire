import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type EntityScope, type Greeting } from "../../api/client";
import { ErrorNote } from "../ErrorNote";
import { GreetingMarkdown } from "../GreetingMarkdown";

/** The same effective scoped records supply this list and the tab count. */
export function GreetingsTab({ scope, greetings, error, greetingHref }: {
  scope: EntityScope;
  greetings: Greeting[] | null;
  error: unknown;
  greetingHref: (id: string) => string;
}) {
  if (error) return <div role="alert"><ErrorNote err={error} /></div>;
  if (greetings === null) return <p className="field-hint">Reading greetings...</p>;
  if (greetings.length === 0) return <p className="empty-state">
    No playable greetings feature this character. Import card greetings or add this character
    to a greeting's cast to make it appear here.
  </p>;
  return <section aria-label="Playable greetings">
    {greetings.map((g, index) => <Fragment key={`${scope.kind}/${scope.id}/${g.id}`}>
      {index > 0 && <hr />}
      <article>
        <h3><Link to={greetingHref(g.id)}>{g.name}</Link></h3>
        <GreetingBody scope={scope} id={g.id} />
      </article>
    </Fragment>)}
  </section>;
}

/** Mounted only for visible greetings. The parent's scope/id key retires both
 * the text and its pending read when navigation replaces the record. */
function GreetingBody({ scope, id }: { scope: EntityScope; id: string }) {
  const { kind, id: ownerId } = scope;
  const [body, setBody] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let alive = true;
    api.readGreeting({ kind, id: ownerId }, id)
      .then(g => { if (alive) setBody(g.body); })
      .catch((err: unknown) => { if (alive) setError(err); });
    return () => { alive = false; };
  }, [kind, ownerId, id]);
  if (error) return <div role="alert"><ErrorNote err={error} /></div>;
  if (body === null) return <p className="field-hint" role="status">Loading greeting...</p>;
  return <GreetingMarkdown>{body}</GreetingMarkdown>;
}
