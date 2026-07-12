import { StyleGuideEditor } from "../components/StyleGuideEditor";

export default function StyleGuidesView() {
  return (
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <h1 className="page-h1">Style Guides</h1>
      </div>
      <StyleGuideEditor />
    </div>
  );
}
