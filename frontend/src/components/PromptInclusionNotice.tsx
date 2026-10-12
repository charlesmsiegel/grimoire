import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type PromptLayout } from "../api/client";

/** Inclusion is a layout preference, not a second per-record switch. Missing
 *  IDs in an older layout are enabled, and layout bypass enables everything. */
export function PromptInclusionNotice({ sectionId }: { sectionId: string }) {
  const [layout, setLayout] = useState<PromptLayout | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    api.getPromptLayout().then((value) => { if (live) setLayout(value); })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, []);
  const disabled = layout?.enabled && layout.sections.find((s) => s.id === sectionId)?.enabled === false;
  if (!failed && !disabled) return null;
  return <div className="field-hint prompt-inclusion" role="status">
    {failed ? "Prompt inclusion is unknown: the prompt layout could not be read. "
      : "This section is disabled in the prompt layout and will not be included. "}
    <Link to="/config">Prompt layout settings</Link>
  </div>;
}
