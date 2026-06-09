import { z } from "zod";

/**
 * Wire shapes for `/api/library/worlds*` (`WorldMeta` / `Greeting` in
 * `backend/src/grimoire/types/composition.py`). Both the library UI and the
 * campaign views read these endpoints; this is the single declaration for
 * both. Only fields the frontend reads are declared; extras are tolerated.
 */

export const WorldMetaSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  tags: z.array(z.string()),
  pc_role_tags: z.array(z.string()),
  genre: z.string(),
  calendar: z.record(z.string(), z.unknown()),
  calendar_ids: z.array(z.string()),
  holiday_set_ids: z.array(z.string()),
  display_calendar_id: z.string().nullable(),
  atmosphere: z.record(z.string(), z.unknown()),
  defaults: z.record(z.string(), z.unknown()),
  version: z.number(),
});
export type WorldMeta = z.infer<typeof WorldMetaSchema>;

export const GreetingSchema = z.object({
  id: z.string(),
  world_id: z.string(),
  name: z.string(),
  starting_location: z.string().nullable(),
  starting_time: z.string().nullable(),
  present_characters: z.array(z.string()),
  pov_character: z.string().nullable(),
  mood: z.string(),
  body: z.string(),
  tags: z.array(z.string()),
  role_tags: z.array(z.string()),
});
export type Greeting = z.infer<typeof GreetingSchema>;
