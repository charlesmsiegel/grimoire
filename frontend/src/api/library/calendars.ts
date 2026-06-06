import { request } from "./request";

export type CalendarSystem =
  | "gregorian"
  | "julian"
  | "hebrew"
  | "islamic"
  | "persian"
  | "chinese"
  | "japanese_era"
  | "indian_saka"
  | "ethiopian"
  | "coptic"
  | "bahai"
  | "buddhist"
  | "iso_week"
  | "stardate"
  | "custom";

export type LeapRuleKind = "none" | "gregorian_like" | "custom_cycle" | "leap_month";

export type HolidayRule =
  | "fixed"
  | "nth_weekday"
  | "last_weekday"
  | "easter_western"
  | "easter_orthodox"
  | "lunar_new_year";

export interface CalendarMonth {
  name: string;
  days: number;
  short_name?: string;
}

export interface CalendarSeason {
  name: string;
  start_month: number;
  start_day: number;
  palette?: string;
}

export interface LeapRule {
  kind: LeapRuleKind;
  cycle_short: number;
  cycle_skip: number;
  cycle_keep: number;
  leap_days: number;
  leap_day_month: number;
  cycle_years: number;
  leap_years_in_cycle: number[];
  leap_month_name: string;
  leap_month_days: number;
  leap_month_position: number;
}

export interface CustomCalendarConfig {
  months: CalendarMonth[];
  days_per_week: number;
  week_day_names: string[];
  seasons: CalendarSeason[];
  leap_rule: LeapRule;
  epoch_jdn: number;
  era_name: string;
}

export interface Calendar {
  id: string;
  name: string;
  description: string;
  tags: string[];
  system: CalendarSystem;
  builtin: boolean;
  custom: CustomCalendarConfig | null;
  date_format: string;
  version: number;
}

export interface Holiday {
  id: string;
  name: string;
  description: string;
  tags: string[];
  rule: HolidayRule;
  month: number;
  day: number;
  weekday: number;
  nth: number;
  weekday_month: number;
  offset_days: number;
  duration_days: number;
}

export interface HolidaySet {
  id: string;
  name: string;
  description: string;
  tags: string[];
  calendar_system: CalendarSystem;
  holidays: Holiday[];
  builtin: boolean;
  version: number;
}

export interface CalendarDate {
  calendar_id: string;
  jdn: number;
  year: number;
  month: number;
  day: number;
  formatted: string;
  era: string;
  weekday: string;
}

export interface HolidayOccurrence {
  set_id: string;
  holiday_id: string;
  name: string;
  description: string;
  tags: string[];
  jdn_start: number;
  jdn_end: number;
}

export interface CreateCalendarPayload {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
  system?: CalendarSystem;
  custom?: Partial<CustomCalendarConfig>;
  date_format?: string;
}

export interface UpdateCalendarPayload {
  name?: string;
  description?: string;
  tags?: string[];
  custom?: Partial<CustomCalendarConfig>;
  date_format?: string;
}

export interface CreateHolidaySetPayload {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
  calendar_system: CalendarSystem;
  holidays: Holiday[];
}

export interface UpdateHolidaySetPayload {
  name?: string;
  description?: string;
  tags?: string[];
  calendar_system?: CalendarSystem;
  holidays?: Holiday[];
}

export const calendarsApi = {
  listCalendars: () => request<Calendar[]>("GET", `/library/calendars`),
  getCalendar: (id: string) =>
    request<Calendar>("GET", `/library/calendars/${encodeURIComponent(id)}`),
  createCalendar: (payload: CreateCalendarPayload) =>
    request<Calendar>("POST", `/library/calendars`, payload),
  updateCalendar: (id: string, patch: UpdateCalendarPayload) =>
    request<Calendar>("PATCH", `/library/calendars/${encodeURIComponent(id)}`, patch),
  deleteCalendar: (id: string) =>
    request<void>("DELETE", `/library/calendars/${encodeURIComponent(id)}`),
  convertDate: (body: {
    from_calendar_id: string;
    to_calendar_ids: string[];
    year: number;
    month: number;
    day: number;
  }) => request<Record<string, CalendarDate>>("POST", `/library/calendars/convert`, body),
  holidaysInYear: (calendarId: string, year: number, setIds: string[]) =>
    request<HolidayOccurrence[]>(
      "GET",
      `/library/calendars/${encodeURIComponent(calendarId)}/holidays?year=${year}&sets=${encodeURIComponent(setIds.join(","))}`,
    ),

  listHolidaySets: () => request<HolidaySet[]>("GET", `/library/holiday-sets`),
  getHolidaySet: (id: string) =>
    request<HolidaySet>("GET", `/library/holiday-sets/${encodeURIComponent(id)}`),
  createHolidaySet: (payload: CreateHolidaySetPayload) =>
    request<HolidaySet>("POST", `/library/holiday-sets`, payload),
  updateHolidaySet: (id: string, patch: UpdateHolidaySetPayload) =>
    request<HolidaySet>("PATCH", `/library/holiday-sets/${encodeURIComponent(id)}`, patch),
  deleteHolidaySet: (id: string) =>
    request<void>("DELETE", `/library/holiday-sets/${encodeURIComponent(id)}`),
};
