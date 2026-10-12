import { useCallback, useEffect, useState } from "react";
import { useArtNavigation } from "./ArtNavigationProvider";
import { REMOVED_IMAGE_NOTICE, type ImageViewerCloseReason, type ImageDescriptor } from "./types";

export function useImageViewer(recordOwner: string) {
  // Related shelves and Markdown viewers also outlive same-record Back. Give
  // every viewer accepted-route ownership; refused navigation keeps this key.
  const navigation = useArtNavigation();
  const owner = JSON.stringify([recordOwner, navigation.locationKey]);
  const [selection, setSelection] = useState<{ owner: string; images: ImageDescriptor[]; initialKey: string; opener: HTMLElement } | null>(null);
  const [notice, setNotice] = useState<{ owner: string; text: string } | null>(null);
  // Clearing the previous owner's selection prevents it reopening when the
  // user later returns to that record; hiding it for one render is insufficient.
  useEffect(() => {
    setSelection((current) => current?.owner === owner ? current : null);
    setNotice((current) => current?.owner === owner ? current : null);
  }, [owner]);
  const close = useCallback((reason?: ImageViewerCloseReason) => {
    setSelection(null);
    setNotice(reason === "unavailable" ? { owner, text: REMOVED_IMAGE_NOTICE } : null);
  }, [owner]);
  const open = useCallback((images: ImageDescriptor[], key: string, opener: HTMLElement) => {
    if (!images.some((image) => image.key === key)) return;
    navigation.requestArtTransition(() => {
      setNotice(null);
      setSelection({ owner, images, initialKey: key, opener });
    });
  }, [owner, navigation]);
  return { open, close, notice: notice?.owner === owner ? notice.text : null, viewerProps: selection?.owner === owner ? { ...selection, onClose: close } : null };
}
