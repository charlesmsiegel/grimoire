export type Size = { width: number; height: number };
export type Pan = { x: number; y: number };
const positive = (n: number) => Number.isFinite(n) && n > 0;

/** Scale is relative to retained pixels, so fit never magnifies a tiny icon.
 *  Zero measurements occur while a modal is attaching or rotating on a phone. */
export function fitScale(natural: Size, viewport: Size): number {
  if (![natural.width, natural.height, viewport.width, viewport.height].every(positive)) return 1;
  return Math.min(1, viewport.width / natural.width, viewport.height / natural.height);
}
export function clampPan(natural: Size, viewport: Size, scale: number, pan: Pan): Pan {
  const bound = (pixels: number, available: number, value: number) => {
    const max = positive(pixels) && positive(available) && positive(scale)
      ? Math.max(0, (pixels * scale - available) / 2) : 0;
    return Math.max(-max, Math.min(max, Number.isFinite(value) ? value : 0)) || 0;
  };
  return { x: bound(natural.width, viewport.width, pan.x), y: bound(natural.height, viewport.height, pan.y) };
}
/** The existing scalar focus follows whichever axis overflows the target.
 *  This defines previews/crop controls only; original pixels never change. */
export function focusWindow(natural: Size, targetAspect: number, focus: number): Size & Pan {
  if (![natural.width, natural.height, targetAspect].every(positive)) return { x: 0, y: 0, width: 0, height: 0 };
  const ratio = Math.max(0, Math.min(100, Number.isFinite(focus) ? focus : 50)) / 100;
  const width = Math.min(natural.width, natural.height * targetAspect);
  const height = Math.min(natural.height, natural.width / targetAspect);
  return { x: (natural.width - width) * ratio, y: (natural.height - height) * ratio, width, height };
}
