/** LLM-backed endpoints 502 with an object detail; coerce so it renders as text. */
export function errMsg(err: any): string {
  const d = err?.detail;
  return typeof d === "string" ? d : (d?.detail ?? String(err));
}
