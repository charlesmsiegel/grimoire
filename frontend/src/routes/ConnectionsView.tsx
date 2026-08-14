import LibraryPage from "../components/LibraryPage";
import { ConnectionEditor } from "../components/ConnectionEditor";

export default function ConnectionsView() {
  return (
    <LibraryPage>
      <div className="page view-anim">
        <div className="page-head">
          <h1 className="page-h1">Connections</h1>
        </div>
        <ConnectionEditor />
      </div>
    </LibraryPage>
  );
}
