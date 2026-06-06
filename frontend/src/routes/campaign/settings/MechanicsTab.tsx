import { useEffect, useState } from "react";

import { api } from "../../../api/client";
import { campaignApi, type MissingSheet } from "../../../api/campaign";
import { mechanicsApi } from "../../../api/library";
import { type MechanicsModuleSummary, fetchInstalledMechanics } from "../../../api/wizard";
import { BulkSheetCreation } from "../BulkSheetCreation";
import { type CampaignRecord, errorMessage } from "./shared";

type PreRollPolicy = "never" | "always" | "high_stakes";

const PRE_ROLL_POLICIES: { value: PreRollPolicy; label: string }[] = [
  { value: "never", label: "Never confirm" },
  { value: "always", label: "Always confirm" },
  { value: "high_stakes", label: "High-stakes rolls only" },
];

export function MechanicsTab({
  campaign,
  onUpdate,
}: {
  campaign: CampaignRecord;
  onUpdate: (next: CampaignRecord) => void;
}) {
  const [modules, setModules] = useState<MechanicsModuleSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(campaign.mechanics_module ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bulk, setBulk] = useState<{
    moduleId: string;
    themeCss: string | null;
    missing: MissingSheet[];
  } | null>(null);
  const [confirmPolicy, setConfirmPolicy] = useState<PreRollPolicy>("never");
  const [policySaving, setPolicySaving] = useState(false);
  const [policyError, setPolicyError] = useState<string | null>(null);

  useEffect(() => {
    void fetchInstalledMechanics().then(setModules);
  }, []);

  useEffect(() => {
    void api
      .get<{ pre_roll?: { confirm_before_executing?: PreRollPolicy } }>(
        `/api/campaigns/${encodeURIComponent(campaign.id)}/orchestrator-config`,
      )
      .then((cfg) => {
        const v = cfg?.pre_roll?.confirm_before_executing;
        if (v === "never" || v === "always" || v === "high_stakes") {
          setConfirmPolicy(v);
        }
      })
      .catch(() => undefined);
  }, [campaign.id]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await campaignApi.switchMechanics(campaign.id, selected);
      onUpdate({ ...campaign, mechanics_module: result.current });
      if (result.current && result.missing_sheets.length > 0) {
        let themeCss: string | null = null;
        try {
          const installed = await mechanicsApi.listInstalled();
          themeCss = installed.find((m) => m.manifest.id === result.current)?.theme_css ?? null;
        } catch {
          themeCss = null;
        }
        setBulk({
          moduleId: result.current,
          themeCss,
          missing: result.missing_sheets,
        });
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  async function savePolicy() {
    setPolicySaving(true);
    setPolicyError(null);
    try {
      await api.patch<unknown>(
        `/api/campaigns/${encodeURIComponent(campaign.id)}/orchestrator-config`,
        { pre_roll: { confirm_before_executing: confirmPolicy } },
      );
    } catch (err) {
      setPolicyError(errorMessage(err));
    } finally {
      setPolicySaving(false);
    }
  }

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Active mechanics module. Switching modules preserves existing sheets under their old module
        id and opens a wizard to create new sheets where needed.
      </p>
      <label className="wizard-field">
        <span>Module</span>
        <select value={selected ?? ""} onChange={(e) => setSelected(e.target.value || null)}>
          <option value="">No mechanics (narrative only)</option>
          {modules.map((m) => (
            <option key={m.id} value={m.id} disabled={Boolean(m.load_error)}>
              {m.name ?? m.id}
              {m.load_error ? " — load error" : ""}
            </option>
          ))}
        </select>
      </label>
      {error && <p className="wizard-error">{error}</p>}
      <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
        {saving ? "Switching…" : "Save"}
      </button>

      <hr className="wizard-divider" />

      <p className="wizard-step-help">
        Pre-roll confirmation: when to interrupt a turn and ask the player to accept, modify, or
        decline the proposed dice rolls before resolving them.
      </p>
      <label className="wizard-field">
        <span>Confirm before executing</span>
        <select
          value={confirmPolicy}
          onChange={(e) => setConfirmPolicy(e.target.value as PreRollPolicy)}
        >
          {PRE_ROLL_POLICIES.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      {policyError && <p className="wizard-error">{policyError}</p>}
      <button
        type="button"
        className="primary"
        disabled={policySaving}
        onClick={() => void savePolicy()}
      >
        {policySaving ? "Saving…" : "Save policy"}
      </button>

      {bulk && (
        <BulkSheetCreation
          campaignId={campaign.id}
          moduleId={bulk.moduleId}
          themeCss={bulk.themeCss}
          missing={bulk.missing}
          onClose={() => setBulk(null)}
        />
      )}
    </div>
  );
}
