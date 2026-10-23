import { api, type ExpressionOutfit, type ExpressionTarget } from "../../api/client";

export function SpritePreview({ target, id, label, outfit, close }: {
  target: ExpressionTarget; id: string; label: string; outfit: ExpressionOutfit; close: () => void;
}) {
  const row = outfit.expressions[label];
  const region = useRef<HTMLDivElement>(null);
  useEffect(() => { region.current?.scrollIntoView({ block: "center" }); }, [label]);
  return <div ref={region} className="expression-preview" role="region" aria-label="Sprite preview">
    <div className="expression-preview-stage"><img alt={`${outfit.label}: ${label}`}
      src={api.expressionImageUrl(target, id, label, row?.token ?? null, false)}
      style={{ left: `${50 + outfit.presentation.offset_x * 100}%`, bottom: `${outfit.presentation.offset_y * 100}%`,
        transformOrigin: "bottom center", transform: `translateX(-50%) scale(${outfit.presentation.scale})` }} /></div>
    <button onClick={close}>Close preview</button>
  </div>;
}
import { useEffect, useRef } from "react";
