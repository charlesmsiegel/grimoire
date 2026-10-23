import { useState } from 'react';
import type { VNActor } from '../../api/client';
export function ActorSprite({ actor }: { actor: VNActor }) {
  const [candidate, setCandidate] = useState(0);
  const image = actor.candidates[candidate];
  return <figure className={`vn-actor${actor.speaking ? ' vn-speaking' : ''}`} aria-label={`${actor.name}${actor.speaking ? ', speaking' : ''}`}>
    {image ? <div className="vn-portrait"><img src={image.url} alt={actor.name} onError={() => setCandidate(n => n + 1)} style={{ left: `${50 + image.presentation.offset_x * 100}%`, bottom: `${image.presentation.offset_y * 100}%`, transform: `translateX(-50%) scale(${image.presentation.scale})`, transformOrigin: image.presentation.anchor.replace('-', ' ') }} /></div> : <div className="vn-initials" aria-label={`${actor.name} portrait unavailable`}>{actor.name.split(/\s+/).map(n => n[0]).slice(0, 2).join('')}</div>}
    <figcaption>{actor.name}</figcaption>
  </figure>;
}
