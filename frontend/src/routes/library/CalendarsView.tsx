import { useCallback, useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { ApiError, calendarsApi } from "../../api/library";
import type {
  Calendar,
  CalendarMonth,
  CalendarSystem,
  CreateCalendarPayload,
  CustomCalendarConfig,
  LeapRule,
  LeapRuleKind,
  UpdateCalendarPayload,
} from "../../api/library";
import { useResource } from "../../api/useResource";
import { CardIconBar } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { AsyncBoundary } from "./AsyncBoundary";

const SYSTEM_LABELS: Record<CalendarSystem, string> = {
  gregorian: "Gregorian",
  julian: "Julian",
  hebrew: "Hebrew",
  islamic: "Islamic (Hijri)",
  persian: "Persian (Solar Hijri)",
  chinese: "Chinese Lunisolar",
  japanese_era: "Japanese Era",
  indian_saka: "Indian Saka",
  ethiopian: "Ethiopian",
  coptic: "Coptic",
  bahai: "Bahá'í",
  buddhist: "Thai Buddhist",
  iso_week: "ISO Week Date",
  stardate: "Stardate (Star Trek)",
  custom: "Custom",
};

const LEAP_KIND_LABELS: Record<LeapRuleKind, string> = {
  none: "No leap rule (fixed year length)",
  gregorian_like: "Gregorian-like (every N years, with skip/keep exceptions)",
  custom_cycle: "Custom cycle (declarative leap years within a cycle)",
  leap_month: "Leap month inserted in leap years",
};

const DEFAULT_LEAP_RULE: LeapRule = {
  kind: "none",
  cycle_short: 4,
  cycle_skip: 100,
  cycle_keep: 400,
  leap_days: 1,
  leap_day_month: 2,
  cycle_years: 0,
  leap_years_in_cycle: [],
  leap_month_name: "",
  leap_month_days: 30,
  leap_month_position: 1,
};

const DEFAULT_CUSTOM: CustomCalendarConfig = {
  months: [
    { name: "First", days: 30 },
    { name: "Second", days: 30 },
    { name: "Third", days: 30 },
  ],
  days_per_week: 7,
  week_day_names: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  seasons: [],
  leap_rule: DEFAULT_LEAP_RULE,
  epoch_jdn: 1721426,
  era_name: "",
};

export function CalendarsView() {
  return (
    <Routes>
      <Route index element={<CalendarsList />} />
      <Route path="new" element={<CalendarCreate />} />
      <Route path=":calendarId" element={<CalendarDetail />} />
      <Route path=":calendarId/edit" element={<CalendarEdit />} />
    </Routes>
  );
}

function CalendarsList() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useResource(
    useCallback(() => calendarsApi.listCalendars(), []),
  );

  const builtins = data?.filter((c) => c.builtin) ?? [];
  const customs = data?.filter((c) => !c.builtin) ?? [];

  async function handleDelete(id: string, name: string) {
    if (!window.confirm(`Delete calendar "${name}"? This cannot be undone.`)) return;
    await calendarsApi.deleteCalendar(id);
    reload();
  }

  return (
    <section className="library-section">
      <header className="library-section-header">
        <h3>Calendars</h3>
        <button onClick={() => navigate("/library/calendars/new")}>+ New calendar</button>
      </header>
      <p className="library-section-intro">
        Worlds and campaigns can attach multiple calendars at once; dates in one calendar reconcile
        to any other via a shared Julian Day Number. Pick one as the "display" calendar for scene
        tracking.
      </p>
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No calendars."
        onRetry={reload}
      >
        <h4>Built-in</h4>
        <ul className="library-card-grid">
          {builtins.map((c) => (
            <li key={c.id} className="library-card">
              <Link to={`/library/calendars/${encodeURIComponent(c.id)}`}>
                <h4>{c.name}</h4>
                <small>{SYSTEM_LABELS[c.system]}</small>
                {c.description && <p className="library-card-meta">{c.description}</p>}
                {c.tags.length > 0 && <p className="library-card-meta">{c.tags.join(" · ")}</p>}
              </Link>
              <CardIconBar actions={[]} />
            </li>
          ))}
        </ul>

        {customs.length > 0 && (
          <>
            <h4>Custom</h4>
            <ul className="library-card-grid">
              {customs.map((c) => (
                <li key={c.id} className="library-card">
                  <Link to={`/library/calendars/${encodeURIComponent(c.id)}`}>
                    <h4>{c.name || c.id}</h4>
                    <small>{c.id}</small>
                    {c.tags.length > 0 && <p className="library-card-meta">{c.tags.join(" · ")}</p>}
                  </Link>
                  <div className="library-card-actions">
                    <Link to={`/library/calendars/${encodeURIComponent(c.id)}/edit`}>Edit</Link>
                  </div>
                  <CardIconBar
                    actions={[
                      deleteAction({
                        onClick: () => void handleDelete(c.id, c.name || c.id),
                        label: `Delete calendar ${c.name || c.id}`,
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

function CalendarDetail() {
  const { calendarId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => calendarsApi.getCalendar(calendarId), [calendarId]),
  );

  async function handleDelete() {
    if (!data || data.builtin) return;
    if (!confirm(`Delete calendar "${data.name}"?`)) return;
    try {
      await calendarsApi.deleteCalendar(calendarId);
      window.location.href = "/library/calendars";
    } catch (err) {
      alert(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/calendars">Calendars</Link> / {calendarId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && (
          <div className="calendar-detail">
            <header className="library-section-header">
              <h3>{data.name}</h3>
              <div>
                {!data.builtin && (
                  <>
                    <Link
                      to={`/library/calendars/${encodeURIComponent(data.id)}/edit`}
                      className="button-link"
                    >
                      Edit
                    </Link>{" "}
                    <button onClick={handleDelete}>Delete</button>
                  </>
                )}
              </div>
            </header>
            <dl className="calendar-meta">
              <dt>System</dt>
              <dd>{SYSTEM_LABELS[data.system]}</dd>
              <dt>Type</dt>
              <dd>{data.builtin ? "Built-in (read-only)" : "Custom"}</dd>
              {data.description && (
                <>
                  <dt>Description</dt>
                  <dd>{data.description}</dd>
                </>
              )}
              {data.tags.length > 0 && (
                <>
                  <dt>Tags</dt>
                  <dd>{data.tags.join(" · ")}</dd>
                </>
              )}
            </dl>
            {data.custom && (
              <>
                <h4>Months</h4>
                <table className="calendar-months">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Name</th>
                      <th>Days</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.custom.months.map((m, i) => (
                      <tr key={i}>
                        <td>{i + 1}</td>
                        <td>{m.name}</td>
                        <td>{m.days}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <h4>Week</h4>
                <p>
                  {data.custom.days_per_week} days per week:{" "}
                  {data.custom.week_day_names.join(", ") || "(unnamed)"}
                </p>
                <h4>Leap rule</h4>
                <p>{LEAP_KIND_LABELS[data.custom.leap_rule.kind]}</p>
                <p>
                  <small>Epoch JDN: {data.custom.epoch_jdn}</small>
                </p>
              </>
            )}
          </div>
        )}
      </AsyncBoundary>
    </section>
  );
}

function CalendarCreate() {
  const navigate = useNavigate();
  return (
    <CalendarForm
      mode="create"
      initial={{
        id: "",
        name: "",
        description: "",
        tags: [],
        system: "custom",
        custom: DEFAULT_CUSTOM,
        date_format: "",
      }}
      onSubmit={async (id, payload) => {
        const created = await calendarsApi.createCalendar({
          ...payload,
          id,
        } as CreateCalendarPayload);
        navigate(`/library/calendars/${encodeURIComponent(created.id)}`);
      }}
    />
  );
}

function CalendarEdit() {
  const { calendarId = "" } = useParams();
  const navigate = useNavigate();
  const [initial, setInitial] = useState<Calendar | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    calendarsApi
      .getCalendar(calendarId)
      .then((cal) => {
        if (cancelled) return;
        if (cal.builtin) {
          setErr("Built-in calendars cannot be edited.");
        } else {
          setInitial(cal);
        }
      })
      .catch((e) => setErr(e instanceof ApiError ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [calendarId]);

  if (err) return <p className="library-error">{err}</p>;
  if (!initial) return <p>Loading…</p>;

  return (
    <CalendarForm
      mode="edit"
      initial={{
        id: initial.id,
        name: initial.name,
        description: initial.description,
        tags: initial.tags,
        system: initial.system,
        custom: initial.custom ?? DEFAULT_CUSTOM,
        date_format: initial.date_format,
      }}
      onSubmit={async (_id, payload) => {
        const patch: UpdateCalendarPayload = {
          name: payload.name,
          description: payload.description,
          tags: payload.tags,
          custom: payload.custom,
          date_format: payload.date_format,
        };
        await calendarsApi.updateCalendar(calendarId, patch);
        navigate(`/library/calendars/${encodeURIComponent(calendarId)}`);
      }}
    />
  );
}

interface FormPayload {
  id: string;
  name: string;
  description: string;
  tags: string[];
  system: CalendarSystem;
  custom: CustomCalendarConfig;
  date_format: string;
}

function CalendarForm({
  mode,
  initial,
  onSubmit,
}: {
  mode: "create" | "edit";
  initial: FormPayload;
  onSubmit: (id: string, payload: FormPayload) => Promise<void>;
}) {
  const [id, setId] = useState(initial.id);
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [tagsStr, setTagsStr] = useState(initial.tags.join(", "));
  const [custom, setCustom] = useState<CustomCalendarConfig>(initial.custom);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const tags = tagsStr
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      await onSubmit(id.trim(), {
        id: id.trim(),
        name: name.trim(),
        description: description.trim(),
        tags,
        system: "custom",
        custom,
        date_format: initial.date_format,
      });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function updateMonth(idx: number, patch: Partial<CalendarMonth>) {
    setCustom((c) => ({
      ...c,
      months: c.months.map((m, i) => (i === idx ? { ...m, ...patch } : m)),
    }));
  }

  function removeMonth(idx: number) {
    setCustom((c) => ({ ...c, months: c.months.filter((_, i) => i !== idx) }));
  }

  function addMonth() {
    setCustom((c) => ({
      ...c,
      months: [...c.months, { name: `Month ${c.months.length + 1}`, days: 30 }],
    }));
  }

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/calendars">Calendars</Link> /{" "}
        {mode === "create" ? "new" : `${id} / edit`}
      </p>
      <header className="library-section-header">
        <h3>{mode === "create" ? "New custom calendar" : `Edit ${name}`}</h3>
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
            <small>e.g. "harvest-realm-cal"</small>
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

        <fieldset className="calendar-months-fieldset">
          <legend>Months</legend>
          {custom.months.map((m, i) => (
            <div key={i} className="calendar-month-row">
              <input
                value={m.name}
                onChange={(e) => updateMonth(i, { name: e.target.value })}
                placeholder="Name"
              />
              <input
                type="number"
                min={1}
                value={m.days}
                onChange={(e) => updateMonth(i, { days: parseInt(e.target.value) || 1 })}
              />
              <button type="button" onClick={() => removeMonth(i)}>
                ×
              </button>
            </div>
          ))}
          <button type="button" onClick={addMonth}>
            + Add month
          </button>
        </fieldset>

        <fieldset>
          <legend>Week</legend>
          <label>
            <span>Days per week</span>
            <input
              type="number"
              min={1}
              max={20}
              value={custom.days_per_week}
              onChange={(e) =>
                setCustom((c) => ({ ...c, days_per_week: parseInt(e.target.value) || 7 }))
              }
            />
          </label>
          <label>
            <span>Day names (comma-separated)</span>
            <input
              value={custom.week_day_names.join(", ")}
              onChange={(e) =>
                setCustom((c) => ({
                  ...c,
                  week_day_names: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                }))
              }
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Leap rule</legend>
          <label>
            <span>Kind</span>
            <select
              value={custom.leap_rule.kind}
              onChange={(e) =>
                setCustom((c) => ({
                  ...c,
                  leap_rule: { ...c.leap_rule, kind: e.target.value as LeapRuleKind },
                }))
              }
            >
              {(Object.keys(LEAP_KIND_LABELS) as LeapRuleKind[]).map((k) => (
                <option key={k} value={k}>
                  {LEAP_KIND_LABELS[k]}
                </option>
              ))}
            </select>
          </label>
          {custom.leap_rule.kind === "gregorian_like" && (
            <>
              <label>
                <span>Leap every N years (cycle_short)</span>
                <input
                  type="number"
                  value={custom.leap_rule.cycle_short}
                  onChange={(e) =>
                    setCustom((c) => ({
                      ...c,
                      leap_rule: { ...c.leap_rule, cycle_short: parseInt(e.target.value) || 4 },
                    }))
                  }
                />
              </label>
              <label>
                <span>Skip if year is multiple of (cycle_skip)</span>
                <input
                  type="number"
                  value={custom.leap_rule.cycle_skip}
                  onChange={(e) =>
                    setCustom((c) => ({
                      ...c,
                      leap_rule: { ...c.leap_rule, cycle_skip: parseInt(e.target.value) || 100 },
                    }))
                  }
                />
              </label>
              <label>
                <span>Keep if year is multiple of (cycle_keep)</span>
                <input
                  type="number"
                  value={custom.leap_rule.cycle_keep}
                  onChange={(e) =>
                    setCustom((c) => ({
                      ...c,
                      leap_rule: { ...c.leap_rule, cycle_keep: parseInt(e.target.value) || 400 },
                    }))
                  }
                />
              </label>
              <label>
                <span>Add extra day to month #</span>
                <input
                  type="number"
                  min={1}
                  max={custom.months.length || 12}
                  value={custom.leap_rule.leap_day_month}
                  onChange={(e) =>
                    setCustom((c) => ({
                      ...c,
                      leap_rule: { ...c.leap_rule, leap_day_month: parseInt(e.target.value) || 1 },
                    }))
                  }
                />
              </label>
            </>
          )}
          {(custom.leap_rule.kind === "custom_cycle" || custom.leap_rule.kind === "leap_month") && (
            <>
              <label>
                <span>Cycle length (years)</span>
                <input
                  type="number"
                  value={custom.leap_rule.cycle_years}
                  onChange={(e) =>
                    setCustom((c) => ({
                      ...c,
                      leap_rule: { ...c.leap_rule, cycle_years: parseInt(e.target.value) || 0 },
                    }))
                  }
                />
              </label>
              <label>
                <span>Leap years within cycle (comma-separated offsets, 1..cycle)</span>
                <input
                  value={custom.leap_rule.leap_years_in_cycle.join(", ")}
                  onChange={(e) =>
                    setCustom((c) => ({
                      ...c,
                      leap_rule: {
                        ...c.leap_rule,
                        leap_years_in_cycle: e.target.value
                          .split(",")
                          .map((s) => parseInt(s.trim()))
                          .filter((n) => Number.isFinite(n)),
                      },
                    }))
                  }
                />
              </label>
              {custom.leap_rule.kind === "leap_month" && (
                <>
                  <label>
                    <span>Leap month name</span>
                    <input
                      value={custom.leap_rule.leap_month_name}
                      onChange={(e) =>
                        setCustom((c) => ({
                          ...c,
                          leap_rule: { ...c.leap_rule, leap_month_name: e.target.value },
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>Leap month days</span>
                    <input
                      type="number"
                      min={1}
                      value={custom.leap_rule.leap_month_days}
                      onChange={(e) =>
                        setCustom((c) => ({
                          ...c,
                          leap_rule: {
                            ...c.leap_rule,
                            leap_month_days: parseInt(e.target.value) || 30,
                          },
                        }))
                      }
                    />
                  </label>
                  <label>
                    <span>Inserted at position (1-indexed)</span>
                    <input
                      type="number"
                      min={1}
                      value={custom.leap_rule.leap_month_position}
                      onChange={(e) =>
                        setCustom((c) => ({
                          ...c,
                          leap_rule: {
                            ...c.leap_rule,
                            leap_month_position: parseInt(e.target.value) || 1,
                          },
                        }))
                      }
                    />
                  </label>
                </>
              )}
            </>
          )}
        </fieldset>

        <fieldset>
          <legend>Anchor</legend>
          <label>
            <span>Epoch JDN (year 1 day 1 = this Julian Day Number)</span>
            <input
              type="number"
              value={custom.epoch_jdn}
              onChange={(e) =>
                setCustom((c) => ({ ...c, epoch_jdn: parseInt(e.target.value) || 1721426 }))
              }
            />
            <small>
              1721426 = 1 Jan 1 CE (proleptic Gregorian). Pick any JDN to anchor your fantasy
              calendar to a real-world date.
            </small>
          </label>
          <label>
            <span>Era name (e.g. "AC", "FA")</span>
            <input
              value={custom.era_name}
              onChange={(e) => setCustom((c) => ({ ...c, era_name: e.target.value }))}
            />
          </label>
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
