import LibraryPage from "../components/LibraryPage";
import { ClimateEditor } from "../components/ClimateEditor";

export default function ClimatesView() {
  return (
    <LibraryPage>
      <div className="page view-anim">
        <div className="page-head">
          <h1 className="page-h1">Climates</h1>
        </div>
        <ClimateEditor />
      </div>
    </LibraryPage>
  );
}
