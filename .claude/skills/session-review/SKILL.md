---
name: session-review
description: "Honest coach's review of a finished workout in Fit-OS, in the chat: what went well and what didn't, with numbers, against the plan, the last sessions and the last 7 days, plus whether they should train more, less or differently. Use when they ask how their workout went or want feedback (\"¿cómo estuvo mi entreno?\", \"sé honesto\", \"qué hice mal\", \"debería entrenar más?\", \"review my session\"). Reads `fitos context`; never changes data. For a quick mid-workout tally use session-status; to write the next day, plan-day."
---

# Review a workout like an honest coach

They asked for the truth, not encouragement. No flattery, no filler, no "¡gran trabajo!" unless a number backs
it. Every claim cites their data (kg × reps, dates, set counts). Direct but not harsh: name the problem, show
why it matters, say what to do instead. Answer in the chat, in their language. Workouts are health data: temp
files in /tmp or `deploy/local/` (gitignored), deleted after; never commit or paste the JSON.

## 1. Read
Account and server as in the plan-day skill. The day under review is today unless they say otherwise:
```bash
make -s context-remote U=<u> DATE=<YYYY-MM-DD> > /tmp/fitos-review.json   # read it, don't paste it
```
- `live`: that day's workout item by item: `planned` (sets, reps, kg) vs `sets` done, `status` (done |
  skipped | pending), `notes` (sleep, energy, illness), `rpe`, `minutes`, `totals.sets_by_muscle`.
  A workout still in progress → review what's there and say it isn't finished.
- `sessions`: the previous weeks set by set, to compare each exercise with its last time.
- `week.hard_sets_by_muscle` vs `week.target_hard_sets`: the last 7 days (moving window).
- `recovery`: muscles in red (`fatigued`) after this session.
- `profile`: `goal`, `experience`, `days_per_week`, `session_minutes`, and `body_composition` (latest smart-scale
  values with their change: body fat, muscle, lean mass…) when a scale is linked: for recomposition, is fat
  going down and lean mass holding?
- RIR = reps left in reserve: lower is harder (0 failure, 1 very hard, 2 hard, 3 challenging, 4 moderate).

## 2. Check, with numbers
1. **Plan vs done.** Exercises planned, done, skipped, added. Skipping what's behind for the week (abs,
   back, legs) while adding more of what's already over is the classic mistake: call it out.
2. **Dose per session.** More than ~10 hard sets for one muscle in a single session is mostly junk volume
   (diminishing returns, more fatigue). Session length vs `session_minutes`.
3. **Effort and load management.** Sets at RIR 0 on heavy compounds, above all when the notes say ill, tired
   or slept badly; load jumping back and forth (alternating weights set to set); a planned load or RIR
   ignored. Testing a heavier weight is fine; grinding to failure on a sick day isn't.
4. **Progress per exercise.** Against its last time in `sessions`: up (more kg or reps at the same effort),
   flat (same kg × reps for 2+ sessions = stalled: say what to change), down. Reps collapsing within the
   session (e.g. 10 → 4) = fatigue or the exercise came too late.
5. **The week.** Hard sets per muscle vs the target: over, on, under. Balance push (chest, delts, triceps)
   vs pull (lats, upper back) and upper vs lower body; abs, calves, lower back are the usual forgotten ones.
6. **Frequency.** Workouts in the last 7 days vs `days_per_week`, counting sick or rest days fairly.
7. **Data quality.** Missing RIR or loads that make progression guesswork; values that look mistyped.

## 3. Write it
1. **Lo bueno**: 2–3 bullets, only what the numbers support (a PR, more reps at the same load, showing up
   when ill). If there's little, keep it short; don't invent.
2. **Lo que no estuvo bien**: numbered, most important first, each with the numbers and a one-line "why it
   matters".
3. **The week**: a table of hard sets per muscle vs the target, flagging over and under.
4. **¿Entrenar más?**: a straight answer: more, less, or the same amount distributed differently, and why.
   For fat-loss or recomposition goals, say plainly that food decides most of it and there's no data on it
   (unless there is).
5. **One rule for next time**: a single concrete instruction ("haz el plan tal cual; si sobra energía, sube
   peso, no agregues ejercicios").

If they also want the next day, use the plan-day skill and let this review steer it (rest the muscles that
are over, favor the ones behind).

## Don'ts
- Don't soften a real problem or pad with praise; don't be cruel either.
- Don't judge without data: a missing RIR is "no lo anotaste", not a guess.
- Don't change sessions or plans here, and don't paste the context JSON or anything from `deploy/local/`.
