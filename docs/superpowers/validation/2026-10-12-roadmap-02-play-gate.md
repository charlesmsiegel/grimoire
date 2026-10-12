# Roadmap 02 play gate: first live report (2026-10-12)

The first live run of `evals/run.py --live --play` (roadmap 02 §4), made on
the day the user ratified the play-decision switches for Settings (02-S6).
The fixtures are `evals/play.py`'s four synthetic ones, with placeholder names
only:
- `addressed`: Seraphine asks Mara;
- `open-remark`: an open remark to Mara and Winifred;
- `pending-decision`: the player's call is pending;
- `director-note`: a note names Winifred.

No store content is reported here.

## Setup

- **Scene (Primary):** `openrouter/deepseek/deepseek-chat-v3.1`.
- **Decision:** `openrouter/openai/gpt-6-luna-decisions`. This is GPT-6 Luna
  served through OpenAI's Decisions API on OpenRouter. It is decide-only, so
  its model facts were stated `generate: no`, `decide_native: yes`, and every
  pick and turn decision ran native. Native answers carry the reported
  distribution the draws sample from.
- **Repeats:** 3 per fixture per configuration, so 12 runs per
  configuration. Spec §4.2's default is 5. This run was cut to fit a
  short-lived, spend-capped key.
- **Concurrency:** the four features ran at the same time. Chat latencies are
  therefore inflated and noisy, and do not compare across configurations. The
  decision latencies are the decision calls' own.
- **Spend:** the whole session, the stopped Jev runs included, cost $0.12 of
  the $1 cap.

## Hard criteria (§4.3)

| Feature | legal | no sampled abstention | no turn regression | off is free | on is on | played |
|---|---|---|---|---|---|---|
| speaker | ok | ok | FAIL¹ | ok | FAIL² | FAIL² |
| intent | ok | ok | ok | ok | ok | FAIL² |
| plan | ok | ok | ok | ok | ok | ok |
| tool | ok | ok | ok | ok | ok | FAIL² |

¹ `director-note [c3]: turn.handoff`. This is the `cutoff: 0` arm (open
question 9), not the shipped `1/(2n)` arm. Three repeats that all
failed the handoff on that arm, against three that passed off, is within
the scene model's own handoff noise; see the next section.

² Every failed run is `empty_response` ("The model returned no response
prose") from the scene model. Some of them are in **off** configurations,
and it reproduces with every switch off. `gate.on_is_on` fails for the
speaker's c3 only because an errored run is counted as not on.

No criterion failed because of a play decision. Every value drawn was one the
item offered, and no abstained, refused or unread answer was ever sampled.

## Findings about the models, not the features

These show in **off** and **on** configurations alike:
- **DeepSeek V3.1 often omits a valid handoff fence.** `turn.handoff` failed
  on 7 to 14 runs per feature, out of 24 (36 for the speaker). It also
  sometimes returns no prose at all (`empty_response`). Both are
  reply-format failures of the scene model.
- **Luna Decisions does not hand the pending-decision fixture back to the
  player.** It picks Mara, so `speaker.hand_back` fails, off and on: sampling
  never turns a null into a speaker (§5.2 rule 1), and this pick was not
  null. It does hand the open remark back (an abstained pick, `basis: none`).
  It reports a spread such as Mara 0.39, Winifred 0.26, narrator 0.19, none
  0.16 on the addressed fixture. The addressed narrowing then makes Mara
  certain, which is why mass on intended reads 1.000.

## Reported figures (for you to judge)

**Added pre-generation time**, the median of each decision call:
- the speaker pick: about 700 ms, as before;
- `turn-intent`: about 285 ms;
- `turn-plan`: about 282 ms;
- `turn-tool-decision`: about 707 ms, when called.

**Cost per call.**
- A native pick or turn decision costs about $0.00003.
- A DeepSeek contribution costs about $0.0005 to $0.0008.
- A turn decision is therefore about 5% of a contribution's cost.

**Stance diversity.**
- intent: `engage` only, over 6 asks;
- plan: `act` and `engage`.

A turn decision is asked only when an NPC replies, and in half the runs the
pick handed back. So 6 asks is too few to call the vocabulary narrow. It is
the first thing a longer run should measure.

**The decide tool.** It was offered on 9 contributions and called on 2. None
was declined after text.

**Speaker draw.** There were no departures from the answer and no addressed
overrides in 12 picks per "on" configuration. The fixtures either addressed
someone (the draw narrowed to them) or drew an abstention (nothing to draw).
The open-remark spread the draw exists for needs a fixture whose pick names a
speaker without an addressee.

## Not measured yet

- §6.6's model-judged adherence grader (02-S5's plan, "Owed before 02-S6").
- The four ensemble turn graders.
- Repeat 5, and a structured Decision model beside the native one (§5.7).
- A second scene model. Runs with `typesafe/jev-router` as the scene model
  were started and stopped unfinished:
  - Jev routed the play prompt to a reasoning model
    (`deepseek/deepseek-v4.1-flash`), which took 35 to 95 s per
    contribution, about 130 s per two-reply turn;
  - at that rate 36 runs outlast the key's window;
  - one probe turn completed cleanly, with a native pick and two valid
    contributions.
