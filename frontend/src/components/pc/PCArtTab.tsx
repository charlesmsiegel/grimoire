import { useRef, useState } from "react";
import { api, type EntityScope } from "../../api/client";
import { errorText } from "../../api/errors";
import { thumbSet } from "../../api/thumbs";
import { AvatarFocusPicker } from "../AvatarFocusPicker";
import { ImageDescriptionField } from "../ImageDescriptionField";

type Image = { name: string; v: string };
export function PCArtTab({ scope, wid, pid, vid, images, descriptions, imageError,
                           avatarFocus, onRefresh, onError }: {
  scope: EntityScope; wid: string; pid: string; vid: string; images: Image[];
  descriptions: Record<string, string>; imageError: string | null;
  avatarFocus: number | null; onRefresh: () => Promise<void>;
  onError: (message: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [cropOpen, setCropOpen] = useState(false);
  const ordered = [...images].sort((a, b) => a.name === "avatar" ? -1 : b.name === "avatar" ? 1
    : Number(a.name.slice(8)) - Number(b.name.slice(8)));
  const avatar = ordered.find((i) => i.name === "avatar");
  const url = (image: Image, w?: number) =>
    api.actorImageUrl(scope, "pcs", pid, vid, image.name, { w, v: image.v });
  async function write(action: () => Promise<unknown>) {
    try { await action(); await onRefresh(); }
    catch (err: unknown) { onError(errorText(err)); }
  }
  async function add(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const next = avatar ? `gallery_${ordered.filter((i) => i.name.startsWith("gallery_"))
      .reduce((n, i) => Math.max(n, Number(i.name.slice(8))), 0) + 1}` : "avatar";
    await write(() => api.putPCImage(scope, pid, vid, next, file));
  }
  return <>
    {imageError && <div className="banner">Could not load art: {imageError}</div>}
    {!imageError && ordered.length === 0 && <p className="field-hint">No art for this version yet.</p>}
    {cropOpen && avatar && <AvatarFocusPicker src={url(avatar)} initial={avatarFocus ?? 50}
      onSave={(focus) => void write(async () => {
        await api.setPCAvatarFocus(scope, pid, vid, focus); setCropOpen(false);
      })} onClose={() => setCropOpen(false)} />}
    <div className="card-field"><div className="card-field-head"><span className="data-label">Images</span></div>
      <div className="images-shelf">
        {ordered.map((image) => <figure className="shelf-tile" key={image.name}>
          <a href={url(image)} target="_blank" rel="noreferrer">
            <img alt={image.name} {...thumbSet((w) => url(image, w), "96px")} />
          </a>
          <figcaption>{image.name}</figcaption>
          {image.name === "avatar"
            ? <button className="shelf-promote" onClick={() => setCropOpen(true)}>Adjust crop</button>
            : <button className="shelf-promote" onClick={() => void write(() =>
                api.promotePCImage(scope, pid, vid, image.name))}>Set as avatar</button>}
          <button className="shelf-promote" onClick={() => void write(() =>
            api.deletePCImage(scope, pid, vid, image.name))}>Remove</button>
          <ImageDescriptionField key={`${vid}:${image.name}`} name={image.name}
            value={descriptions[image.name]}
            onSave={async (description) => {
              await api.setPCImageDescription(scope, pid, vid, image.name, description);
              await onRefresh();
            }}
            onDraft={scope.kind === "world" ? () =>
              api.draftPCImageDescription(wid, pid, vid, image.name).then((r) => r.description)
              : undefined} />
        </figure>)}
        <button className="shelf-add" onClick={() => input.current?.click()}>+ add</button>
        <input ref={input} type="file" accept="image/png,image/jpeg,image/gif,image/webp"
               hidden aria-label="Add image" onChange={(event) => void add(event)} />
      </div>
    </div>
  </>;
}
