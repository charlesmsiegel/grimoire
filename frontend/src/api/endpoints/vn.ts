import { request } from '../transport';
import type { VNScene, VNStage, VNPresentationInput } from '../types/vn';
const root = (cid: string, sid: string) => `/api/campaigns/${encodeURIComponent(cid)}/scenes/${encodeURIComponent(sid)}/vn`;
export const vnApi = {
  getVNScene: (cid: string, sid: string) => request<VNScene>('GET', root(cid, sid), undefined, { fresh: true }),
  getVNPost: (cid: string, sid: string, post: string) => request<VNStage>('GET', `${root(cid, sid)}/posts/${encodeURIComponent(post)}`, undefined, { fresh: true }),
  saveVNPresentation: (cid: string, sid: string, post: string, body: VNPresentationInput) => request<VNStage>('PUT', `${root(cid, sid)}/posts/${encodeURIComponent(post)}/presentation`, body),
};
