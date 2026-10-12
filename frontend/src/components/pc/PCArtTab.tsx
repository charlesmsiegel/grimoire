import { RecordArtPanel } from "../art/RecordArtPanel";
import type { RecordArtController } from "../art/useRecordArt";

/** Version selection and art reads belong to the page. Its preview, count,
 * and mutation panel therefore cannot acquire different actor versions. */
export function PCArtTab({ controller }: { controller: RecordArtController }) {
  return <RecordArtPanel controller={controller} />;
}
