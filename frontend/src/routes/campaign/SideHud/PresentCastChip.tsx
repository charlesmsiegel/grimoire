import { Link } from "react-router-dom";

import type { PresentCastChipData } from "./presentCastShape";

interface Props {
  chip: PresentCastChipData;
  campaignId: string;
}

export function PresentCastChip({ chip, campaignId }: Props) {
  const params = new URLSearchParams({ character: chip.character_id });
  if (chip.character_ref) params.set("ref", chip.character_ref);
  const castUrl = `/campaigns/${encodeURIComponent(campaignId)}/cast?${params}`;

  return (
    <Link
      to={castUrl}
      className="chip hud-present-cast-chip"
      aria-label={`${chip.name} — view in cast`}
    >
      {chip.name}
    </Link>
  );
}
