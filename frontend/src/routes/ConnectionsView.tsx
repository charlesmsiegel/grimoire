import { ConnectionEditor } from "../components/ConnectionEditor";

export default function ConnectionsView() {
  return (
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <h1 className="page-h1">Connections</h1>
      </div>
      <ConnectionEditor />
    </div>
  );
}
