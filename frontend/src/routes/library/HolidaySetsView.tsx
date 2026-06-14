import { useCallback, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { ApiError, calendarsApi } from "../../api/library";
import type {
  CalendarSystem,
  CreateHolidaySetPayload,
  Holiday,
  HolidayRule,
  UpdateHolidaySetPayload,
} from "../../api/library";
import { useResource } from "../../api/useResource";
import { CardIconBar } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { useDestructiveConfirm } from "../../hooks/useDestructiveConfirm";

const SYSTEM_LABELS: Record<CalendarSystem, string> = {
  gregorian: "Gregorian",
  julian: "Julian",
  hebrew: "Hebrew",
  islamic: "Islamic",
  persian: "Persian",
  chinese: "Chinese",
  japanese_era: "Japanese Era",
  indian_saka: "Indian Saka",
  ethiopian: "Ethiopian",
  coptic: "Coptic",
  bahai: "Bahá'í",
  buddhist: "Thai Buddhist",
  iso_week: "ISO Week",
  stardate: "Stardate",
  custom: "Custom",
};

const RULE_LABELS: Record<HolidayRule, string> = {
  fixed: "Fixed (month + day)",
  nth_weekday: "Nth weekday of month",
  last_weekday: "Last weekday of month",
  easter_western: "Offset from Western Easter",
  easter_orthodox: "Offset from Orthodox Easter",
  lunar_new_year: "Offset from Lunar New Year",
};

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function emptyHoliday(): Holiday {
  return {
    id: "",
    name: "",
    description: "",
    tags: [],
    rule: "fixed",
    month: 1,
    day: 1,
    weekday: 0,
    nth: 1,
    weekday_month: 1,
    offset_days: 0,
    duration_days: 1,
  };
}

export function HolidaySetsView() {
  return (
    <Routes>
      <Route index element={<List />} />
      <Route path="new" element={<Create />} />
      <Route path=":setId" element={<Detail />} />
      <Route path=":setId/edit" element={<Edit />} />
    </Routes>
  );
}

function List() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useResource(
    useCallback(() => calendarsApi.listHolidaySets(), []),
  );

  const builtins = data?.filter((s) => s.builtin) ?? [];
  const customs = data?.filter((s) => !s.builtin) ?? [];

  const del = useDestructiveConfirm<{ id: string; name: string }>(async ({ id }) => {
    await calendarsApi.deleteHolidaySet(id);
    reload();
  });

  return (
    <section className="library-section">
      <header className="library-section-header">
        <h3>Holiday sets</h3>
        <button onClick={() => navigate("/library/holiday-sets/new")}>+ New holiday set</button>
      </header>
      {del.target && (
        <ConfirmDestructiveDialog
          open
          title={`Delete holiday set "${del.target.name}"?`}
          body={<p>This cannot be undone.</p>}
          busy={del.busy}
          error={del.error}
          onConfirm={del.confirm}
          onCancel={del.cancel}
        />
      )}
      <p className="library-section-intro">
        Each holiday set binds to a specific calendar system. Attach sets to a world to overlay
        multiple traditions onto its calendars.
      </p>
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No holiday sets."
        onRetry={reload}
      >
        <h4>Built-in</h4>
        <ul className="grid-cards">
          {builtins.map((s) => (
            <li key={s.id} className="library-card">
              <Link to={`/library/holiday-sets/${encodeURIComponent(s.id)}`}>
                <h4>{s.name}</h4>
                <small>{SYSTEM_LABELS[s.calendar_system]}</small>
                <p className="library-card-meta">{s.holidays.length} holidays</p>
                {s.description && <p className="library-card-meta">{s.description}</p>}
              </Link>
              <CardIconBar actions={[]} />
            </li>
          ))}
        </ul>

        {customs.length > 0 && (
          <>
            <h4>Custom</h4>
            <ul className="grid-cards">
              {customs.map((s) => (
                <li key={s.id} className="library-card">
                  <Link to={`/library/holiday-sets/${encodeURIComponent(s.id)}`}>
                    <h4>{s.name || s.id}</h4>
                    <small>{s.id}</small>
                    <p className="library-card-meta">{s.holidays.length} holidays</p>
                  </Link>
                  <div className="library-card-actions">
                    <Link to={`/library/holiday-sets/${encodeURIComponent(s.id)}/edit`}>Edit</Link>
                  </div>
                  <CardIconBar
                    actions={[
                      deleteAction({
                        onClick: () => del.request({ id: s.id, name: s.name || s.id }),
                        label: `Delete holiday set ${s.name || s.id}`,
                      }),
                    ]}
                  />
                </li>
              ))}
            </ul>
          </>
        )}
      </AsyncBoundary>
    </section>
  );
}

function Detail() {
  const { setId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => calendarsApi.getHolidaySet(setId), [setId]),
  );

  const navigate = useNavigate();
  const del = useDestructiveConfirm<{ name: string }>(async () => {
    await calendarsApi.deleteHolidaySet(setId);
    navigate("/library/holiday-sets");
  });

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/holiday-sets">Holiday sets</Link> / {setId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && (
          <div>
            <header className="library-section-header">
              <h3>{data.name}</h3>
              <div>
                {!data.builtin && (
                  <>
                    <Link
                      to={`/library/holiday-sets/${encodeURIComponent(data.id)}/edit`}
                      className="button-link"
                    >
                      Edit
                    </Link>{" "}
                    <button onClick={() => del.request({ name: data.name })}>Delete</button>
                  </>
                )}
                {del.target && (
                  <ConfirmDestructiveDialog
                    open
                    title={`Delete holiday set "${del.target.name}"?`}
                    body={<p>This cannot be undone.</p>}
                    busy={del.busy}
                    error={del.error}
                    onConfirm={del.confirm}
                    onCancel={del.cancel}
                  />
                )}
              </div>
            </header>
            <p>
              <strong>Calendar system:</strong> {SYSTEM_LABELS[data.calendar_system]}
            </p>
            {data.description && <p>{data.description}</p>}
            <h4>Holidays</h4>
            <ul className="holiday-list">
              {data.holidays.map((h) => (
                <li key={h.id || h.name}>
                  <strong>{h.name}</strong> <small>({RULE_LABELS[h.rule]})</small>
                  <br />
                  <small>{formatHolidayRule(h)}</small>
                </li>
              ))}
            </ul>
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}

function formatHolidayRule(h: Holiday): string {
  switch (h.rule) {
    case "fixed":
      return `Month ${h.month}, Day ${h.day}${h.duration_days > 1 ? ` (${h.duration_days} days)` : ""}`;
    case "nth_weekday":
      return `${ordinal(h.nth)} ${WEEKDAY_LABELS[h.weekday]} of month ${h.weekday_month}`;
    case "last_weekday":
      return `Last ${WEEKDAY_LABELS[h.weekday]} of month ${h.weekday_month}`;
    case "easter_western":
    case "easter_orthodox":
      return `${h.offset_days >= 0 ? "+" : ""}${h.offset_days} days from Easter`;
    case "lunar_new_year":
      return `${h.offset_days >= 0 ? "+" : ""}${h.offset_days} days from Lunar New Year`;
  }
}

function ordinal(n: number): string {
  if (n === 1) return "1st";
  if (n === 2) return "2nd";
  if (n === 3) return "3rd";
  return `${n}th`;
}

function Create() {
  const navigate = useNavigate();
  return (
    <Form
      mode="create"
      initial={{
        id: "",
        name: "",
        description: "",
        tags: [],
        calendar_system: "gregorian",
        holidays: [],
      }}
      onSubmit={async (payload) => {
        const created = await calendarsApi.createHolidaySet(payload as CreateHolidaySetPayload);
        navigate(`/library/holiday-sets/${encodeURIComponent(created.id)}`);
      }}
    />
  );
}

function Edit() {
  const { setId = "" } = useParams();
  const navigate = useNavigate();
  const { data, error } = useResource(
    useCallback(() => calendarsApi.getHolidaySet(setId), [setId]),
  );

  if (error) return <p className="library-error">{error.message}</p>;
  if (!data) return <p>Loading…</p>;
  if (data.builtin) return <p className="library-error">Built-in holiday sets cannot be edited.</p>;

  return (
    <Form
      mode="edit"
      initial={data}
      onSubmit={async (payload) => {
        const patch: UpdateHolidaySetPayload = {
          name: payload.name,
          description: payload.description,
          tags: payload.tags,
          calendar_system: payload.calendar_system,
          holidays: payload.holidays,
        };
        await calendarsApi.updateHolidaySet(setId, patch);
        navigate(`/library/holiday-sets/${encodeURIComponent(setId)}`);
      }}
    />
  );
}

interface FormPayload {
  id: string;
  name: string;
  description: string;
  tags: string[];
  calendar_system: CalendarSystem;
  holidays: Holiday[];
}

function Form({
  mode,
  initial,
  onSubmit,
}: {
  mode: "create" | "edit";
  initial: FormPayload;
  onSubmit: (payload: FormPayload) => Promise<void>;
}) {
  const [id, setId] = useState(initial.id);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [tagsStr, setTagsStr] = useState(initial.tags.join(", "));
  const [system, setSystem] = useState<CalendarSystem>(initial.calendar_system);
  const [holidays, setHolidays] = useState<Holiday[]>(initial.holidays);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function updateHoliday(i: number, patch: Partial<Holiday>) {
    setHolidays((hs) => hs.map((h, idx) => (idx === i ? { ...h, ...patch } : h)));
  }

  function removeHoliday(i: number) {
    setHolidays((hs) => hs.filter((_, idx) => idx !== i));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const tags = tagsStr
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      await onSubmit({
        id: id.trim(),
        name: name.trim(),
        description: description.trim(),
        tags,
        calendar_system: system,
        holidays,
      });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/holiday-sets">Holiday sets</Link> /{" "}
        {mode === "create" ? "new" : `${id} / edit`}
      </p>
      <header className="library-section-header">
        <h3>{mode === "create" ? "New holiday set" : `Edit ${name}`}</h3>
      </header>
      <form onSubmit={submit} className="library-form">
        {mode === "create" && (
          <label>
            <span>ID</span>
            <input
              required
              value={id}
              pattern="[a-zA-Z0-9][a-zA-Z0-9._-]*"
              onChange={(e) => setId(e.target.value)}
            />
          </label>
        )}
        <label>
          <span>Name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          <span>Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>
          <span>Tags</span>
          <input
            value={tagsStr}
            onChange={(e) => setTagsStr(e.target.value)}
            placeholder="comma, separated"
          />
        </label>
        <label>
          <span>Calendar system</span>
          <select value={system} onChange={(e) => setSystem(e.target.value as CalendarSystem)}>
            {(Object.keys(SYSTEM_LABELS) as CalendarSystem[]).map((s) => (
              <option key={s} value={s}>
                {SYSTEM_LABELS[s]}
              </option>
            ))}
          </select>
        </label>

        <fieldset>
          <legend>Holidays</legend>
          {holidays.map((h, i) => (
            <div key={i} className="holiday-row">
              <label>
                <span>Name</span>
                <input
                  value={h.name}
                  onChange={(e) => updateHoliday(i, { name: e.target.value })}
                />
              </label>
              <label>
                <span>Rule</span>
                <select
                  value={h.rule}
                  onChange={(e) => updateHoliday(i, { rule: e.target.value as HolidayRule })}
                >
                  {(Object.keys(RULE_LABELS) as HolidayRule[]).map((r) => (
                    <option key={r} value={r}>
                      {RULE_LABELS[r]}
                    </option>
                  ))}
                </select>
              </label>
              {h.rule === "fixed" && (
                <>
                  <label>
                    <span>Month</span>
                    <input
                      type="number"
                      min={1}
                      max={31}
                      value={h.month}
                      onChange={(e) => updateHoliday(i, { month: parseInt(e.target.value) || 1 })}
                    />
                  </label>
                  <label>
                    <span>Day</span>
                    <input
                      type="number"
                      min={1}
                      max={31}
                      value={h.day}
                      onChange={(e) => updateHoliday(i, { day: parseInt(e.target.value) || 1 })}
                    />
                  </label>
                </>
              )}
              {(h.rule === "nth_weekday" || h.rule === "last_weekday") && (
                <>
                  <label>
                    <span>Month</span>
                    <input
                      type="number"
                      min={1}
                      max={12}
                      value={h.weekday_month}
                      onChange={(e) =>
                        updateHoliday(i, { weekday_month: parseInt(e.target.value) || 1 })
                      }
                    />
                  </label>
                  <label>
                    <span>Weekday</span>
                    <select
                      value={h.weekday}
                      onChange={(e) => updateHoliday(i, { weekday: parseInt(e.target.value) || 0 })}
                    >
                      {WEEKDAY_LABELS.map((label, w) => (
                        <option key={w} value={w}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {h.rule === "nth_weekday" && (
                    <label>
                      <span>Nth</span>
                      <input
                        type="number"
                        min={1}
                        max={5}
                        value={h.nth}
                        onChange={(e) => updateHoliday(i, { nth: parseInt(e.target.value) || 1 })}
                      />
                    </label>
                  )}
                </>
              )}
              {(h.rule === "easter_western" ||
                h.rule === "easter_orthodox" ||
                h.rule === "lunar_new_year") && (
                <label>
                  <span>Offset days</span>
                  <input
                    type="number"
                    value={h.offset_days}
                    onChange={(e) =>
                      updateHoliday(i, { offset_days: parseInt(e.target.value) || 0 })
                    }
                  />
                </label>
              )}
              <label>
                <span>Duration</span>
                <input
                  type="number"
                  min={1}
                  value={h.duration_days}
                  onChange={(e) =>
                    updateHoliday(i, { duration_days: parseInt(e.target.value) || 1 })
                  }
                />
              </label>
              <button type="button" onClick={() => removeHoliday(i)}>
                Remove
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() =>
              setHolidays((hs) => [
                ...hs,
                {
                  ...emptyHoliday(),
                  id: `h-${hs.length + 1}`,
                  name: `New holiday ${hs.length + 1}`,
                },
              ])
            }
          >
            + Add holiday
          </button>
        </fieldset>

        {err && (
          <p className="library-error" role="alert">
            {err}
          </p>
        )}
        <div className="library-form-actions">
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : mode === "create" ? "Create" : "Save"}
          </button>
        </div>
      </form>
    </section>
  );
}
