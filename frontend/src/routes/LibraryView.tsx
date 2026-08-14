import { Navigate } from "react-router-dom";

/** The library's card hub is gone.
 *
 *  It was a landing page in front of six list routes: it answered "what is in
 *  the library" once, and then cost a click on every visit afterwards. The six
 *  sections are the context column now — permanently visible, with the same
 *  counts, switchable in one click — so there is nothing left for a hub page
 *  to do that the column does not do better.
 *
 *  The route survives as a redirect rather than being deleted, because
 *  `/library` is a URL people have bookmarked and the palette still offers
 *  "The Library" as a place to go. Worlds is the first section and the one
 *  everything else is built on. */
export default function LibraryView() {
  return <Navigate to="/worlds" replace />;
}
