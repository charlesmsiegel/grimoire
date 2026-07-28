import { ClimateEditor } from "../components/ClimateEditor";

export default function ClimatesView() {
  return (
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <h1 className="page-h1">Climates</h1>
      </div>
      <ClimateEditor />
    </div>
  );
}
