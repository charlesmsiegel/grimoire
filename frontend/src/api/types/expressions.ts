export type ExpressionTarget = {
  scope: "world" | "campaign"; owner_id: string; kind: "characters" | "pcs"; actor_id: string; version_id: string;
};
export type SpritePresentation = { scale: number; offset_x: number; offset_y: number; anchor: "bottom-center" };
export type OutfitMetadata = { label: string; clothing_aliases: string[]; fallback_expression: string; presentation: SpritePresentation };
export type ExpressionImage = { image_id: string; available: boolean; token: string | null; revision: string;
  source: "local" | "inherited" | "overridden" };
export type ExpressionOutfit = OutfitMetadata & { source: ExpressionImage["source"]; expressions: Record<string, ExpressionImage> };
export type ExpressionCollection = {
  format: 1; revision: string | null; effective_revision: string; default_outfit: string | null;
  outfits: Record<string, ExpressionOutfit>; standard_labels: string[];
  hidden_outfits: string[]; hidden_expressions: Record<string, string[]>; warnings: string[];
};
export type ExpressionMutation =
  | { op: "create-outfit" | "patch-outfit"; outfit: string; metadata: Partial<OutfitMetadata> }
  | { op: "duplicate-outfit"; outfit: string; new_outfit: string }
  | { op: "remove-outfit" | "hide-outfit" | "restore-outfit" | "set-default"; outfit: string }
  | { op: "remove-expression" | "hide-expression" | "restore-expression"; outfit: string; expression: string };
export type ExpressionImportSelection = { file: string; outfit: string; expression: string; overwrite: boolean };
export type ExpressionImportPreview = {
  token: string; expires_at: number; effective_revision: string; issues: string[];
  images: { file: string; outfit: string; expression: string | null; ambiguous: boolean; ext: string }[];
  metadata: { format: 1; default_outfit?: string | null; outfits: Record<string, Partial<OutfitMetadata>> };
};
export type NativeActorTarget = Omit<ExpressionTarget, "version_id">;
export interface NativeActorPreview {
  token: string; expires_at: number; kind: "characters" | "pcs"; versions: string[];
  has_dependency: boolean; can_bind: boolean;
}
