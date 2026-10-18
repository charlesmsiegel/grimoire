import LibraryPage from "../components/LibraryPage";
import { StyleGuideEditor } from "../components/StyleGuideEditor";

export default function StyleGuidesView() {
  return (
    <LibraryPage>
      <div className="page-wide view-anim">
        <div className="library-page-head">
          <div className="eyebrow">The Library</div>
          <h1 className="screen-title">Style Guides</h1>
        </div>
        <StyleGuideEditor />
      </div>
    </LibraryPage>
  );
}
