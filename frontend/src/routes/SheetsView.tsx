import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api, type ModuleDetail, type SheetBulkResult, type SheetRoster, type SheetRosterRow,
} from "../api/client";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePaletteSource, type PaletteItem } from "../components/palette";
import { typeKind } from "../components/SheetEditor";
import SheetPanel from "../components/SheetPanel";
import { usePublishShellContext } from "../components/ShellStatus";
import { sheetKindLabel } from "../sheetKinds";

/** What one cast member's row says on the rail, in one word.
 *
 *  Ordered worst-first on purpose: a sheet can be invalid AND owe creation
 *  points at once, and "invalid" is the one that stops it being usable, so it
 *  wins the badge. A row never shows two.
 */
type Mark = { text: string; tone: "" | "missing" | "alert" };

function markFor(row: SheetRosterRow): Mark {
  if (!row.sheeted) return { text: "Missing", tone: "missing" };
  if (row.errors.length) return { text: "Invalid", tone: "alert" };
  const owed = Object.values(row.unspent);
  const over = owed.filter((n) => n < 0).reduce((a, b) => a - b, 0);
  if (over) return { text: `${over} over`, tone: "alert" };
  const left = owed.reduce((a, b) => a + b, 0);
  if (left) return { text: `${left} left`, tone: "missing" };
  return { text: "Sheet", tone: "" };
}

const isMissing = (row: SheetRosterRow) => !row.sheeted;

/** The sheet types a module offers for one FILE kind — `pcs` share the
 *  `characters` types, which is what `typeKind` encodes. */
function typesFor(module: ModuleDetail | null, kind: string): [string, string][] {
  if (!module) return [];
  return Object.entries(module.sheets.sheet_types)
    .filter(([, st]) => st.kind === typeKind(kind))
    .map(([tid, st]) => [tid, st.label] as [string, string])
    .sort((a, b) => a[0].localeCompare(b[0]));
}

/** Sheet coverage across a campaign's whole cast, and the one action that
 *  closes the gap.
 *
 *  A room rather than a drawer, for the reason the ledger is one: the mechanics
 *  panel answers "which module is this campaign playing" in six lines and can
 *  stay a panel, but "who among forty characters has a sheet" is a list read
 *  top to bottom, and the play view has nowhere to put one.
 *
 *  The list/detail split `CLAUDE.md` describes, with the rail in the shell's
 *  context column rather than beside it: the column IS this page's rail, and a
 *  second navigation surface next to it would be the misread `PageShell`
 *  warns about. Picking a member opens their sheet read-only; `SheetPanel`'s
 *  own Open sheet button is the explicit edit step, so this screen reuses the
 *  single-sheet view whole instead of growing a second one that would drift.
 */
export default function SheetsView() {
  const { cid = "" } = useParams();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  // Every piece of loaded state is held WITH the campaign it came from: this
  // route is not keyed on `cid`, so a campaign switch keeps the component
  // mounted, and a bare value would show one game's cast under the other's
  // name until the new request settled (LedgerView holds its ledger the same
  // way, for the same reason).
  const [loaded, setLoaded] = useState<{ cid: string; roster: SheetRoster } | null>(null);
  const [bound, setBound] = useState<{ cid: string; module: ModuleDetail | null } | null>(null);
  const [selected, setSelected] = useState<{ kind: string; id: string } | null>(null);
  const [types, setTypes] = useState<Record<string, string>>({});
  const [result, setResult] = useState<SheetBulkResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  usePublishShellContext(name ? { campaign: name, scene: "" } : null);

  useEffect(() => {
    api.getCampaign(cid).then((c) => setName(c.meta.name)).catch(() => setName(cid));
  }, [cid]);

  // The module's full detail, not just the bound id: `SheetPanel` renders
  // against the schema, and this page needs the per-kind sheet-type lists to
  // offer a choice for a kind that has more than one.
  useEffect(() => {
    let live = true;
    setBound(null);
    api.getCampaignModule(cid)
      .then((m) => (m.resolved ? api.readModule(m.resolved) : null))
      .then((detail) => { if (live) setBound({ cid, module: detail }); })
      .catch(() => { if (live) setBound({ cid, module: null }); });
    return () => { live = false; };
  }, [cid]);

  const reload = useCallback(() => {
    let live = true;
    api.getCampaignSheetRoster(cid)
      .then((r) => { if (live) setLoaded({ cid, roster: r.roster }); })
      .catch(() => { if (live) setLoaded({ cid, roster: {} }); });
    return () => { live = false; };
  }, [cid]);

  // The campaign changed: drop the selection and the last bulk report with it,
  // or the report would be read as this campaign's.
  useEffect(() => {
    setSelected(null);
    setResult(null);
    setError(null);
    setTypes({});
    setLoaded(null);
    return reload();
  }, [cid, reload]);

  const roster = loaded && loaded.cid === cid ? loaded.roster : null;
  const module = bound && bound.cid === cid ? bound.module : null;
  const kinds = useMemo(() => Object.keys(roster ?? {}), [roster]);

  /** A kind this campaign can bulk-create for, and whether a choice is owed:
   *  a module with two sheet types for a kind has no rule that picks between
   *  them, so the server skips it until one is named. */
  const plan = useMemo(() => kinds.map((kind) => {
    const rows = roster?.[kind] ?? [];
    const options = typesFor(module, kind);
    const missing = rows.filter(isMissing).length;
    return {
      kind, rows, options, missing,
      sheeted: rows.filter((r) => r.sheeted).length,
      // One option needs no choosing; several with none chosen is what the
      // report will call a skip.
      needsChoice: missing > 0 && options.length > 1 && !types[kind],
    };
  }), [kinds, roster, module, types]);

  const missingTotal = plan.reduce((n, p) => n + p.missing, 0);

  const paletteSource = useCallback((): PaletteItem[] => ([{
    id: `sheets:${cid}`, group: "IN THIS CAMPAIGN", label: "Sheets",
    meta: roster ? `${missingTotal} without a sheet` : "sheet coverage",
    run: () => navigate(`/campaigns/${cid}/sheets`),
  }]), [cid, navigate, roster, missingTotal]);
  usePaletteSource(paletteSource);

  async function createMissing() {
    setBusy(true);
    setError(null);
    // Back to the overview first: the report is the answer to this press, and
    // it renders there, not under whichever member happened to be open.
    setSelected(null);
    try {
      setResult(await api.createMissingSheets(cid, types));
    } catch (e) {
      // `ApiError` extends Error with the server's `detail` as its message, so
      // this reads the same sentence the route wrote without importing it.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      reload();
    }
  }

  const column = (
    <>
      <Link className="column-back" to={`/campaigns/${cid}`}>‹ {name || "The campaign"}</Link>
      <div className="ledger-ident">
        <div className="eyebrow">Who has a sheet</div>
        <h2 className="ledger-ident-name">{name || cid}</h2>
      </div>
      {roster === null && <p className="column-empty">Reading the cast…</p>}
      {roster !== null && kinds.length === 0 && (
        <p className="column-empty">No mechanics bound.</p>
      )}
      {plan.map(({ kind, rows, sheeted }) => (
        <ColumnSection key={kind} label={sheetKindLabel(kind)}
                       count={`${sheeted}/${rows.length}`}>
          {rows.length === 0 && <p className="column-empty">None yet.</p>}
          {rows.map((row) => {
            const mark = markFor(row);
            const active = selected?.kind === kind && selected.id === row.id;
            return (
              <button key={row.id} className={"column-row" + (active ? " active" : "")}
                      onClick={() => setSelected({ kind, id: row.id })}>
                <span className="column-row-label">{row.name}</span>
                <span className={"column-row-count" + (mark.tone ? " " + mark.tone : "")}>
                  {mark.text}
                </span>
              </button>
            );
          })}
        </ColumnSection>
      ))}
    </>
  );

  const footer = (
    <button className="primary" onClick={() => { void createMissing(); }}
            disabled={busy || missingTotal === 0 || !module}>
      {busy ? "Creating…" : `+ Create missing sheets${missingTotal ? ` (${missingTotal})` : ""}`}
    </button>
  );

  const selectedRow = selected
    ? (roster?.[selected.kind] ?? []).find((r) => r.id === selected.id) ?? null
    : null;

  return (
    <PageShell column={column} footer={footer} columnLabel="The cast">
      <div className="page-wide view-anim">
        <div className="shelf-head">
          <div>
            <div className="eyebrow">
              {module ? `SHEETS · ${module.manifest.name.toUpperCase()}` : "SHEETS"}
            </div>
            <h1 className="screen-title">
              {selectedRow ? selectedRow.name : "Sheet coverage"}
            </h1>
          </div>
        </div>

        {error && <div className="banner">{error}</div>}

        {roster !== null && !module && (
          <p className="empty-state">
            <span className="empty-what">
              This campaign has no mechanics bound, so there are no sheets to keep.
            </span>{" "}
            <Link to={`/campaigns/${cid}`}>Bind a module from the play view →</Link>
          </p>
        )}

        {selected && selectedRow && module && (
          <div className="detail-view">
            <div className="detail-main">
              <button className="subtle" onClick={() => setSelected(null)}>‹ All sheets</button>
              {selectedRow.errors.length > 0 && (
                <div className="banner">{selectedRow.errors.join("; ")}</div>
              )}
              {Object.entries(selectedRow.unspent).map(([pool, left]) => (
                <div className="field-hint" key={pool}>
                  {left > 0
                    ? `${left} unspent in the ${pool} creation pool.`
                    : `${-left} over budget in the ${pool} creation pool.`}
                </div>
              ))}
            </div>
            <aside className="detail-sidebar">
              {/* The single-sheet view, whole: read-only chips here, and its own
                  Open sheet button for the edit step. Keyed so moving between
                  two members refetches instead of showing the previous one's
                  sheet while the new read is in flight. */}
              <SheetPanel key={`${selected.kind}/${selected.id}`}
                          scope={{ kind: "campaign", id: cid }} module={module}
                          kind={selected.kind} eid={selected.id}
                          onOpenRef={(kind, id) => setSelected({ kind, id })} />
            </aside>
          </div>
        )}

        {!selectedRow && module && roster !== null && (
          <>
            <table className="ledger-table">
              <thead>
                <tr>
                  <th scope="col">KIND</th>
                  <th scope="col">SHEETED</th>
                  <th scope="col">MISSING</th>
                  <th scope="col">CREATE AS</th>
                </tr>
              </thead>
              <tbody>
                {plan.map(({ kind, rows, options, missing, sheeted, needsChoice }) => (
                  <tr key={kind}>
                    <td>{sheetKindLabel(kind)}</td>
                    <td>{sheeted}/{rows.length}</td>
                    <td className={missing ? "ledger-mark alert" : undefined}>{missing}</td>
                    <td>
                      {options.length <= 1
                        ? <span className="field-hint">{options[0]?.[1] ?? "—"}</span>
                        : (
                          <select aria-label={`Sheet type for ${sheetKindLabel(kind)}`}
                                  value={types[kind] ?? ""}
                                  onChange={(e) => setTypes(
                                    { ...types, [kind]: e.target.value })}>
                            <option value="">Choose…</option>
                            {options.map(([tid, label]) => (
                              <option key={tid} value={tid}>{label}</option>
                            ))}
                          </select>
                        )}
                      {needsChoice && (
                        <div className="field-hint">
                          {options.length} types — choose one or this kind is skipped.
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {missingTotal === 0 && plan.length > 0 && (
              <p className="field-hint">Every cast member has a sheet.</p>
            )}

            {result && (
              <div className="side-section">
                <h4>Last create</h4>
                <p className="field-hint">
                  {result.created.length} sheet{result.created.length === 1 ? "" : "s"} created.
                </p>
                {/* Named, not counted: a sheet written from schema defaults owes
                    its creation pools, and a bulk create that reported only a
                    total would be the silent skip #201 rules out wearing a
                    different hat. */}
                {result.created.filter((c) => Object.keys(c.unspent).length > 0).map((c) => (
                  <p className="field-hint" key={`${c.kind}/${c.id}`}>
                    {c.name}: {Object.entries(c.unspent)
                      .map(([pool, left]) => (left > 0
                        ? `${left} unspent in ${pool}`
                        : `${-left} over budget in ${pool}`)).join(", ")}
                  </p>
                ))}
                {result.skipped.map((s) => (
                  <p className="field-hint" key={s.kind}>
                    {sheetKindLabel(s.kind)} skipped — {s.reason}
                  </p>
                ))}
                {result.failed.map((f) => (
                  <p className="field-hint" key={`${f.kind}/${f.id}`}>
                    {sheetKindLabel(f.kind)} {f.id} failed — {f.detail}
                  </p>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </PageShell>
  );
}
