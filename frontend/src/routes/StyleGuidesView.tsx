import LibraryPage from "../components/LibraryPage";
import { StyleGuideEditor } from "../components/StyleGuideEditor";

export default function StyleGuidesView() {
  return (
    <LibraryPage>
      <div className="page view-anim">
        <div className="page-head">
          <h1 className="page-h1">Style Guides</h1>
        </div>
        <StyleGuideEditor />
      </div>
    </LibraryPage>
  );
}
