import { Navigate, useParams } from "react-router-dom";
import { taskHash } from "../components/models/taskHash";

/** `/models/route/:key`, an address older links and bookmarks carry: the
 *  task's row on the edit form now. The router hands the key decoded, and
 *  `taskHash` encodes it once. */
export function RoutePageRedirect() {
  const { key = "" } = useParams();
  return <Navigate to={`/models/edit${taskHash(key)}`} replace />;
}
