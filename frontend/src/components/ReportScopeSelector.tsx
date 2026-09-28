import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { CampaignMeta } from "../api/types";
import { reportHref, type Report } from "../shell/rail";

export default function ReportScopeSelector({ report, cid, suffix = "" }: {
  report: Report; cid: string | null; suffix?: string;
}) {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let active = true;
    api.listCampaigns().then((rows) => {
      if (active) { setCampaigns(rows); setFailed(false); setReady(true); }
    }).catch(() => { if (active) { setFailed(true); setReady(true); } });
    return () => { active = false; };
  }, []);
  const missing = cid && !campaigns.some((row) => row.id === cid);
  return <label className="report-scope">
    <span className="section-label">Campaign</span>
    <select aria-label="Report campaign" value={cid ?? ""}
            onChange={(event) => navigate(reportHref(report, event.target.value || null) + suffix)}>
      <option value="">All campaigns</option>
      {campaigns.map((campaign) => <option key={campaign.id} value={campaign.id}>
        {campaign.name}
      </option>)}
      {missing && <option value={cid}>{cid} ({failed ? "list unavailable"
        : ready ? "unavailable" : "loading"})</option>}
    </select>
    {failed && <span className="field-hint">Campaign list could not be read.</span>}
  </label>;
}
