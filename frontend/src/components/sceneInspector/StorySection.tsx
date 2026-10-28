import { useEffect, useState } from "react";
import { api, type ChronicleEntry } from "../../api/client";
import { SideSection, type SectionControls } from "./SideSection";

export function StorySection({ cid, sid, refreshKey, collapsed, toggleSection }:
  SectionControls & { cid: string; sid: string; refreshKey: number }) {
  const [story, setStory] = useState<{ cid: string; rows: ChronicleEntry[] } | null>(null);
  useEffect(() => {
    let live = true;
    api.getChronicle(cid).then((rows) => { if (live) setStory({ cid, rows }); })
      .catch(() => { if (live) setStory({ cid, rows: [] }); });
    return () => { live = false; };
  }, [cid, sid, refreshKey]);
  const recap = story?.cid === cid ? story.rows : [];
  return <>
      {recap.length > 0 && (
        <SideSection id="story" title="Story so far" collapsed={!!collapsed.story} onToggle={toggleSection}>
          {[...recap].reverse().map((r) => (
            <div className="field-hint" key={r.id}>{r.one_line || r.summary}</div>
          ))}
        </SideSection>
      )}
  </>;
}
