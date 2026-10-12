import { useCallback, useEffect, useRef, useState } from "react";
import type { ImageDescriptionEditorState } from "../ImageDescriptionField";
import { useArtNavigation } from "./ArtNavigationProvider";

/** Existing library writers keep their own persistence contract. Their local
 * caption editors still share the viewer's discard/pending navigation rules. */
export function useLibraryDescriptionDrafts(owner: string) {
  const navigation = useArtNavigation(), [epoch, setEpoch] = useState(0);
  const context = useRef({ owner, generation: 0 });
  if (context.current.owner !== owner) context.current = { owner, generation: context.current.generation + 1 };
  const key = JSON.stringify([owner, context.current.generation, epoch]);
  const frame = useRef({ key, fields: new Map<string, ImageDescriptionEditorState>() });
  if (frame.current.key !== key) frame.current = { key, fields: new Map() };
  const lastLocation = useRef(navigation.locationKey);
  const track = useCallback((name: string, state: ImageDescriptionEditorState | null) => {
    if (frame.current.key !== key) return;
    if (state) frame.current.fields.set(name, state); else frame.current.fields.delete(name);
  }, [key]);
  const allow = useCallback(() => {
    if (frame.current.key !== key) return true;
    const fields = Array.from(frame.current.fields.values());
    return !fields.some(field => field.dirty)
      || window.confirm("Discard unsaved art changes? Saved images and descriptions will remain.");
  }, [key]);
  const discard = useCallback(() => {
    if (frame.current.key !== key) return;
    frame.current.fields.clear();
    setEpoch(previous => previous + 1);
  }, [key]);
  useEffect(() => navigation.registerDraftGuard(allow, discard), [navigation, allow, discard]);
  useEffect(() => {
    if (lastLocation.current === navigation.locationKey) return;
    lastLocation.current = navigation.locationKey;
    discard();
  }, [navigation.locationKey, discard]);
  function transition(action: () => void) {
    // Local viewer entry still waits for its caption write. Other context
    // navigation uses the registered dirty decision and retires this editor.
    if (Array.from(frame.current.fields.values()).some(field => field.busy)) return;
    if (navigation.provided) navigation.requestArtTransition(action);
    else if (allow()) { discard(); action(); }
  }
  return { key, track, transition };
}
