import { RecordArtPanel } from "../art/RecordArtPanel";
import type { RecordArtController } from "../art/useRecordArt";
import { CharacterArtRelated, type CharacterArtRelatedProps } from "./CharacterArtRelated";

/** The page owns one controller for both the identity preview and this panel.
 * Greeting-specific borrowing and purged world versions remain a separate
 * bridge; they never become generic greeting mutation targets. */
export function ArtTab({ controller, related }: {
  controller: RecordArtController; related: CharacterArtRelatedProps;
}) {
  return <RecordArtPanel controller={controller} related={<CharacterArtRelated {...related} onView={controller.transition} />} />;
}
export default ArtTab;
