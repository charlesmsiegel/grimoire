# Campaign Calendars, Time and Event Clocks

**Outcome:** Keep exact and approximate passage of time, custom calendars and deadlines consistent across scenes.

## Scope
- Retain Gregorian/Hebrew/custom calendar support and existing fixed-day arithmetic.
- Represent ordered dates, uncertain durations, clocks, reminders and scheduled triggers as readable campaign authority.
- Prevent skipped/retried operations from advancing time twice.

## Milestones
1. Expose reliable current campaign date and time advancement.
2. Record one deadline clock and detect its triggering point.
3. Support complex fictional calendars and parallel event schedules.

## Proving scenario
Moving ahead 20 days advances the calendar once and flags a deadline while leaving older scene dates unchanged.

## Independence and interfaces
Can work with existing time records, without modeling offscreen NPC life or long activities.

## Open questions
- How do approximate durations interact with exact chronology?
- Which clock triggers require explicit confirmation?
- What is a portable custom-calendar representation?
