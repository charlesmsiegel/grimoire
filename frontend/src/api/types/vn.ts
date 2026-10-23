export type VNActorOverride = { outfit?: string | undefined; expression?: string | undefined; hidden?: boolean };
export type VNOverride = { actors: Record<string, VNActorOverride>; background: string | null };
export type VNScene = { scene_identity: string; title: string; posts: { post_id: string; content: string; role: string; speaker: string | null; excluded: boolean }[] };
export type VNActor = { ref: string; version_id: string; name: string; speaking: boolean; automatic_outfit: string | null; hidden?: boolean; candidates: { url: string; presentation: { scale: number; offset_x: number; offset_y: number; anchor: string } }[]; outfits: { id: string; label: string; expressions: string[] }[] };
export type VNStage = { scene_identity: string; post_id: string; revision: string; actors: VNActor[]; backgrounds: { name: string; url: string }[]; background_url: string | null; location_name: string | null; notices: string[]; override: VNOverride };
export type VNPresentationInput = VNOverride & { scene_identity: string; expected_revision: string };
