import { Link } from "react-router-dom";

import type { PresentCastChipData } from "./presentCastShape";

interface Props {
  chip: PresentCastChipData;
  campaignId: string;
}

export function PresentCastChip({ chip, campaignId }: Props) {
  const castUrl = `/campaigns/${encodeURIComponent(campaignId)}/cast?character=${encodeURIComponent(chip.character_id)}`;

  return (
    <Link
      to={castUrl}
      className="hud-present-cast-chip"
      aria-label={`${chip.name} — view in cast`}
    >
      {chip.name}
    </Link>
  );
}
