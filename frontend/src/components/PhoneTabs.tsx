import { NavLink, useLocation } from "react-router-dom";
import type { ShellPayload } from "../api/types";
import { phoneTabs } from "../shell/tabs";

/** The phone's navigation: a bar you land on rather than a drawer you open.
 *
 *  Below `PHONE_PX` the rail is a drawer, and a drawer is the wrong shape for
 *  the only way to move around an app — every move costs a deliberate open,
 *  and the thing you opened covers what you were reading while you choose.
 *  The bar is always there, five destinations wide, and the drawer stays
 *  underneath it as `More` for everything that does not fit.
 *
 *  It renders nothing on a scene route. The design gives that screen its own
 *  bottom furniture — the composer, and the pill that raises the context sheet
 *  — and two bars stacked at the foot of a 720px viewport is most of what is
 *  left of the transcript. Leaving is the back control in the scene's own bar;
 *  that slice is not built yet, so today the drawer is still how you leave,
 *  which is the same way you leave a focus-mode transcript. */
export default function PhoneTabs(
  { payload, cid, onOpenRail }: {
    payload: ShellPayload | null;
    cid: string | null;
    onOpenRail: () => void;
  },
) {
  const { pathname } = useLocation();
  const ctx = { cid };
  const tabs = phoneTabs(ctx, payload);

  // A scene owns its own foot. See the docstring.
  if (/\/campaigns\/[^/]+\/scenes\/[^/]+/.test(pathname)) return null;

  return (
    <nav className="phone-tabs" aria-label="Sections">
      {tabs.map((t) => {
        const active = t.match(pathname, ctx);
        const body = (
          <>
            <span className="phone-tab-icon" aria-hidden>{t.icon}</span>
            <span className="phone-tab-label">{t.label}</span>
            {/* `0` is a real answer and shows; `undefined` means nobody
                computed it and shows nothing. The rail draws the same
                distinction and for the same reason. */}
            {t.badge !== undefined && (
              <span className="phone-tab-badge" aria-hidden>{t.badge}</span>
            )}
            {t.badgeLabel && <span className="sr-only">{t.badgeLabel}</span>}
          </>
        );
        if (t.opensRail) {
          return (
            <button key={t.id} type="button" className="phone-tab" onClick={onOpenRail}>
              {body}
            </button>
          );
        }
        return (
          <NavLink key={t.id} to={t.to ?? "/"} end
                   className={"phone-tab" + (active ? " on" : "")}
                   aria-current={active ? "page" : undefined}>
            {body}
          </NavLink>
        );
      })}
    </nav>
  );
}
