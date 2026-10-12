import { useNavigate } from "react-router-dom";
import { ImageViewer } from "./ImageViewer";
import type { RecordArtController } from "./useRecordArt";

export function RecordArtEntry({ controller, name, onOpenArt }: {
  controller: RecordArtController; name: string; onOpenArt: () => void;
}) {
  const image = controller.preview, actor = controller.target?.kind === "characters" || controller.target?.kind === "pcs";
  return <div className="record-art-entry">
    {image && <button className="record-art-preview" aria-label={"View " + name + " art"}
      style={{ aspectRatio: actor ? "1" : "1.6" }} onClick={(event) => controller.openImage(image, event.currentTarget)}>
      <img src={image.thumb} alt={name + " art preview"} style={{ objectPosition: (image.focus ?? 50) + "% " + (image.focus ?? 50) + "%" }} />
    </button>}
    <button type="button" ref={controller.entryRef} disabled={!!controller.busy} onClick={() => controller.transition(onOpenArt)}>
      {controller.count === null ? "Art" : "Art (" + controller.count + ")"}
    </button>
  </div>;
}
/** Mount once beside the selected record's header, independently of its tab.
 *  Header previews and panel tiles thereby open the same sequence and modal. */
export function RecordArtViewer({ controller }: { controller: RecordArtController }) {
  const navigate = useNavigate();
  return <>{controller.viewerNotice && <p role="status">{controller.viewerNotice}</p>}
    {controller.viewerProps && <ImageViewer {...controller.viewerProps} fallbackFocus={controller.focusRef.current ?? controller.entryRef.current} onOpenRecord={(destination) => {
    controller.closeViewer(); navigate(destination);
  }} />}</>;
}
