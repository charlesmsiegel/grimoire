import { encodeSegment } from "../../urlSegment";
import { request, requestForm } from "../transport";
import type { ExpressionCollection, ExpressionImportPreview, ExpressionImportSelection, ExpressionMutation, ExpressionTarget, NativeActorPreview, NativeActorTarget } from "../types";

function base(t: ExpressionTarget) {
  return `/api/${t.scope}s/${encodeSegment(t.owner_id)}/${t.kind}/${encodeSegment(t.actor_id)}/versions/${encodeSegment(t.version_id)}/expressions`;
}
function actorBase(t: NativeActorTarget) {
  return `/api/${t.scope}s/${encodeSegment(t.owner_id)}/${t.kind}/${encodeSegment(t.actor_id)}/bundle`;
}
export const expressionsApi = {
  nativeActorExportUrl: (target: NativeActorTarget) => actorBase(target),
  previewNativeActor: (target: NativeActorTarget, file: File) => {
    const form = new FormData(); form.append("file", file);
    return requestForm<NativeActorPreview>(actorBase(target) + "/imports/preview", form);
  },
  publishNativeActor: (target: NativeActorTarget, token: string, disposition: "bind" | "detached") =>
    request<NativeActorTarget>("POST", `${actorBase(target)}/imports/${encodeSegment(token)}/commit`, { disposition }),
  getExpressions: (target: ExpressionTarget) => request<ExpressionCollection>("GET", base(target)),
  cleanupExpressionRevisions: (target: ExpressionTarget) =>
    request<{ removed_revisions: number }>("POST", base(target) + "/cleanup"),
  mutateExpressions: (target: ExpressionTarget, revision: string, mutations: ExpressionMutation[]) =>
    request<ExpressionCollection>("POST", base(target) + "/mutations", { expected_effective_revision: revision, mutations }),
  uploadExpression: (target: ExpressionTarget, revision: string, outfit: string, expression: string, file: File) => {
    const form = new FormData(); form.append("file", file); form.append("expected_effective_revision", revision);
    return requestForm<ExpressionCollection>(`${base(target)}/${encodeSegment(outfit)}/${encodeSegment(expression)}/image`, form, "PUT");
  },
  expressionImageUrl: (target: ExpressionTarget, outfit: string, expression: string, token: string | null, thumbnail = true) =>
    `${base(target)}/${encodeSegment(outfit)}/${encodeSegment(expression)}/image?v=${encodeURIComponent(token ?? "")}${thumbnail ? "&w=320" : ""}`,
  expressionExportUrl: (target: ExpressionTarget) => base(target) + "/export",
  previewExpressionPack: (target: ExpressionTarget, file: File, mode: "grimoire" | "sillytavern", outfit: string) => {
    const form = new FormData(); form.append("file", file); form.append("mode", mode);
    if (mode === "sillytavern") form.append("outfit_id", outfit);
    return requestForm<ExpressionImportPreview>(base(target) + "/imports/preview", form);
  },
  previewExpressionFolder: (target: ExpressionTarget, files: File[], mode: "grimoire" | "sillytavern", outfit: string) => {
    const form = new FormData(); form.append("mode", mode);
    if (mode === "sillytavern") form.append("outfit_id", outfit);
    for (const file of files) {
      const parts = (file.webkitRelativePath || file.name).split("/");
      const path = parts.length === 1 || mode === "grimoire" && parts[0] === "expressions" ? parts.join("/") : parts.slice(1).join("/");
      form.append("files", file, path);
    }
    return requestForm<ExpressionImportPreview>(base(target) + "/imports/folder", form);
  },
  publishExpressionPack: (target: ExpressionTarget, preview: ExpressionImportPreview, selection: ExpressionImportSelection[]) =>
    request<ExpressionCollection>("POST", `${base(target)}/imports/${preview.token}/commit`, {
      expected_effective_revision: preview.effective_revision, selection,
    }),
};
