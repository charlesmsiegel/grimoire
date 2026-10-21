import { memo } from "react";
import { Link } from "react-router-dom";
import { api, type CharacterSummary, type EntityScope } from "../../api/client";
import { thumbSet } from "../../api/thumbs";
import { characterHref, focusStyle, initialsOf } from "../character/shared";

/** How wide a card's portrait is drawn: `.char-grid` is three columns of
 *  `.page-wide`, which stops growing at 1120px -- so at most
 *  (1120 - 68 padding - 36 gaps) / 3 on a desktop, and under a third of the
 *  viewport below that. A phone is spelled out rather than left to the third:
 *  there the grid has two columns, 14px gutters and an 18px gap. Keeping
 *  that measure here lets the browser choose the right thumbnail bucket.
 *  A roster is the one screen where a whole library's art can be on screen at
 *  once, so this is where a thumbnail the size of the card matters most. */
const CARD_SIZES = "(max-width: 720px) calc((100vw - 46px) / 2), (max-width: 1120px) 33vw, 340px";

/** The count chips under a card's name: versions, greetings, gallery and
 *  localized images -- each drawn only when there is one to count. */
function CardBadges({ c }: { c: CharacterSummary }) {
  const greetings = c.greeting_count ?? 0;
  const gallery = c.gallery_count ?? 0;
  const localized = c.localized_count ?? 0;
  const versions = c.versions.length;
  if (gallery === 0 && localized === 0 && greetings === 0 && versions <= 1) return null;
  return (
    <span className="char-card-badges">
      {versions > 1 && <span className="chip">{versions} versions</span>}
      {greetings > 0 && (
        <span className="chip">{c.greeting_count} greeting{c.greeting_count === 1 ? "" : "s"}</span>
      )}
      {gallery > 0 && <span className="chip">{c.gallery_count} gallery</span>}
      {localized > 0 && <span className="chip">{c.localized_count} localized</span>}
    </span>
  );
}

/** One card. Memoized, with only primitives and stable callbacks for props
 *  besides the row itself, because the grid re-renders for reasons no card
 *  cares about -- the describe count landing, the module context arriving
 *  after the list, a tagline batch's progress line ticking once per
 *  character -- and every card rebuilding its portrait's `srcset` for each of
 *  them is the grid paying for its whole roster to redraw a toolbar. */
export const CharacterCard = memo(function CharacterCard(
  { c, scopeKind, scopeId }: {
    c: CharacterSummary;
    scopeKind: EntityScope["kind"];
    scopeId: string;
  },
) {
  const scope: EntityScope = { kind: scopeKind, id: scopeId };
  return (
    <div className="char-card">
      <Link className="char-card-main" to={characterHref(scope, c.id)}>
        {c.has_avatar
          ? <img className="char-card-avatar" alt="" style={focusStyle(c.avatar_focus)}
                 loading="lazy" decoding="async"
                 {...thumbSet((w) => api.actorImageUrl(scope, "characters", c.id,
                                                       c.default_version, "avatar",
                                                       { w, v: c.avatar_v }),
                              CARD_SIZES)} />
          : <div className="initials-avatar" aria-hidden>{initialsOf(c.name)}</div>}
        <span className="char-card-name">{c.name}</span>
        {c.tagline ? <span className="char-card-tagline">{c.tagline}</span> : null}
        <CardBadges c={c} />
      </Link>

    </div>
  );
});
