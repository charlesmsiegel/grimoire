-- Time Engine remaining-design (§6 + §9 follow-up storage).
--
-- §6 ``scheduled_event_pre_notice``: when the engine emits a
-- ``scheduled_event_imminent`` event during an advance we stamp the row so a
-- subsequent advance does not re-warn about the same event.

ALTER TABLE scheduled_events ADD COLUMN pre_notice_emitted_at TEXT;
