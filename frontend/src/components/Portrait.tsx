import { useEffect, useState } from "react";

export function initialsOf(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("").toUpperCase();
}

/** Square portrait with an initials fallback (no src, or the file 404s). */
export function Portrait({ src, name }: { src: string | null; name: string }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [src]);
  if (!src || broken) {
    return <span className="portrait-initials" aria-hidden>{initialsOf(name)}</span>;
  }
  return <img className="portrait" alt={`${name} portrait`} src={src}
              onError={() => setBroken(true)} />;
}
