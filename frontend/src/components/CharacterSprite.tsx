/**
 * Per-character sprite component.
 *
 * Resolves the character's current (or as-of-turn) expression via the
 * REST endpoint and renders the sprite inside a fixed-aspect container
 * using `object-fit: contain`. Falls back to the character's name when
 * no sprite is available.
 */

import { useExpression } from "../api/expressions";

interface Props {
  campaignId: string;
  characterId: string;
  characterName: string;
  asOfTurn?: string | null;
  size?: "sm" | "md";
  expressionsEnabled?: boolean;
}

export function CharacterSprite({
  campaignId,
  characterId,
  characterName,
  asOfTurn,
  size = "md",
  expressionsEnabled = false,
}: Props) {
  const { data, loading } = useExpression(campaignId, characterId, asOfTurn, expressionsEnabled);

  const className = `character-sprite character-sprite-${size}`;

  if (loading) {
    return (
      <div
        className={`${className} character-sprite-loading`}
        aria-label={`${characterName} expression loading`}
      />
    );
  }

  if (!data?.sprite_url) {
    return (
      <span className={`${className} character-sprite-empty`} aria-label={characterName}>
        {characterName}
      </span>
    );
  }

  const altText =
    data.fallback_used && data.emotion === "neutral"
      ? `${characterName} (neutral)`
      : `${characterName} — ${data.emotion}`;

  return (
    <div
      className={className}
      data-emotion={data.emotion}
      data-fallback={data.fallback_used ? "true" : "false"}
    >
      <img src={data.sprite_url} alt={altText} loading="lazy" />
    </div>
  );
}
