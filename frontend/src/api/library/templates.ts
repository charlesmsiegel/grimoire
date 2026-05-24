import { request } from "./request";

export interface TemplateSummary {
  name: string;
  variants: string[];
  active: string;
  editable: string[];
}

export interface TemplateListResponse {
  templates: TemplateSummary[];
  user_dir: string;
  default_variant: string;
}

export interface TemplateBody {
  name: string;
  variant: string;
  body: string;
  editable: boolean;
  path: string;
}

export const templatesApi = {
  list: () => request<TemplateListResponse>("GET", `/templates`),
  read: (name: string, variant: string) =>
    request<TemplateBody>(
      "GET",
      `/templates/${encodeURIComponent(name)}/${encodeURIComponent(variant)}`,
    ),
  write: (name: string, variant: string, body: string) =>
    request<{ ok: boolean; path: string }>(
      "PUT",
      `/templates/${encodeURIComponent(name)}/${encodeURIComponent(variant)}`,
      { body },
    ),
  remove: (name: string, variant: string) =>
    request<{ ok: boolean }>(
      "DELETE",
      `/templates/${encodeURIComponent(name)}/${encodeURIComponent(variant)}`,
    ),
  setActive: (name: string, variant: string | null) =>
    request<{ ok: boolean; active: string }>(
      "POST",
      `/templates/${encodeURIComponent(name)}/active`,
      { variant },
    ),
};
