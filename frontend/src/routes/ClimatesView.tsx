import LibraryPage from "../components/LibraryPage";
import { ClimateEditor } from "../components/ClimateEditor";

export default function ClimatesView() {
  return (
    <LibraryPage>
      <div className="page-wide view-anim">
        <div className="library-page-head">
          <div className="eyebrow">The Library</div>
          <h1 className="screen-title">Climates</h1>
        </div>
        <ClimateEditor />
      </div>
    </LibraryPage>
  );
}
