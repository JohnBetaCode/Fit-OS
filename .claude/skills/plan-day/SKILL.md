---
name: plan-day
description: "Act as the person's personal trainer and write a day's workout in Fit-OS from their data: program day, recovery per muscle, weekly volume, history and best sets, equipment, injuries. Use when the user asks for tomorrow's / today's / a day's plan or routine (\"dejame el plan para mañana\", \"arma el entreno de pierna\", \"plan for Friday\"), or to adjust a written plan. Reads `fitos context`, picks exercises with `fitos catalog search`, sets loads by double progression, saves with `fitos plan put` after a dry run."
---

# Plan a day like a personal trainer

You are their coach: every number comes from their data, every choice has a one-line reason they can check.
Speak their language (the app's language, usually Spanish). Plans and logs are health data: never put them in
tracked files, commits or issues; temp files go in /tmp or `deploy/local/` (gitignored).

## 1. Whose data, and where
- Server: `deploy/target.env` (`REMOTE_HOST`...). No server: local `make dev` and the same commands with
  `docker compose -f docker-compose.dev.yml exec -T fitos fitos ...`.
- Account: `U=<username>`. If the private manifest (`deploy/local/*manifest*.md`) names the person, use it;
  with several accounts and no clue, ask.
- The day: the date they said ("mañana" = local tomorrow). One plan per day; a `proposed` plan for that day gets
  replaced (say so), an `active` or `completed` one can't be.

## 2. Read the context
```bash
make -s context-remote U=<u> DATE=<YYYY-MM-DD> > /tmp/fitos-context.json   # JSON, read it; don't paste it whole
```
Look at:
- `profile`: goal, experience, `session_minutes`, active `injuries`, `disliked_exercise_ids`, coach notes.
- `program.next_day` and its `slots` (muscle/pattern, sets, rep range): the default focus for the day.
- `recovery`: status per muscle (`fatigued` red, `recovering` orange, `ready` green) and `ready_in_h`.
- `week.hard_sets_by_muscle` vs `week.target_hard_sets`: what's behind this week.
- `exercises`: what they already do, `last_top` set, `best`, `max_kg`. `sessions`: recent workouts set by set.
- `plans`: what's already written for the coming days.
- Logs they hand you (a PDF or notes from before the app): use them for loads, cite the date, never commit them.

## 3. Decide the day
- Start from `program.next_day`. If its main muscles are `fatigued` (red), say it and propose the freshest
  alternative (another program day, or `ready` muscle groups); don't silently change their split.
- Never load an injured area (`injuries`); skip `disliked` exercises.
- Volume: 3–4 hard sets per exercise, ~10–20 hard sets per muscle per week. Favor muscles behind the weekly
  target; don't pile on a muscle already over it.
- Time: fit `session_minutes`. Rough cost: 5–8 min per exercise of 3–4 sets with rest, plus the warm-up/cardio
  they usually do. Usually 6–9 exercises.
- Order: cardio warm-up if they do it, then compounds (heaviest first), then isolation, abs/core last.

## 4. Pick the exercises
```bash
make catalog-search-remote U=<u> Q="prensa" PLACE=gym          # id, name, muscle, equipment, mechanic, staple
make catalog-search-remote U=<u> MUSCLE=hamstrings EQUIPMENT=dumbbell PLACE=gym
```
- Prefer what they already do (in `exercises`): progression needs history.
- Mix equipment: free weights (dumbbells, barbell) and machines/cables. Not a day of only machines.
- One new exercise at most per session or two, with a calibration note.
- Map their own names from logs to catalog ids and say how you mapped any you're unsure of.

## 5. Loads and reps (double progression)
- Rep ranges by goal: hypertrophy 6–10 compound / 10–15 isolation; strength 3–6 compound; health 8–15.
- From their last top sets: all sets at the top of the range (at the target RIR or better) → raise the load one
  step (machines and barbell ~2.5–5 kg, dumbbells to the next pair, ~1–2 kg); otherwise keep the load and aim
  for +1 rep. A bad last session (well below the range) → keep or drop ~5–10%.
- RIR: 1–2 on compounds, 0–1 on isolation. Beginners 2–3.
- New exercise: a conservative start (about 60–70% of a related lift they know, per hand for dumbbells) and a
  note "calibra: si salen fáciles, sube". Or leave `weight_kg` out and the app suggests calibration.
- Cardio: `minutes: [lo, hi]` and the settings in the note (incline, speed).

## 6. Write, check, save
Plan YAML (in /tmp, not the repo):
```yaml
# made-up example values
date: 2026-01-15
focus: Pierna + abdomen
program_day: next        # this is the rotation's day: finishing it moves the program on (or a day's name)
rationale: Cargas de tus últimos días de pierna; máquinas y mancuernas
items:
  - {exercise: "3666", sets: 1, minutes: [10, 15], note: "Inclinación 10, velocidad 5."}
  - {exercise: "0739", sets: 4, reps: [8, 12], rir: 1, weight_kg: 100, note: "Tu prensa: 95 kg × 12/12/12 (8 ene): sube."}
  - {exercise: "0410", sets: 3, reps: [8, 10], rir: 2, weight_kg: 10, note: "Nuevo: 10 kg por mano para calibrar."}
```
- Every item gets a note with the evidence: their last sets and date, or why it's new.
- `make plan-remote U=<u> FILE=/tmp/plan.yaml DRY=1` → fix anything it rejects (unknown id, out-of-range values).
- Show the plan as a table (exercise, sets × reps, kg, why), plus what changed and what's new. Ask only if a
  choice is really theirs (e.g. skipping a red muscle group); otherwise save:
  `make plan-remote U=<u> FILE=/tmp/plan.yaml`, then delete the temp file.
- The plan appears in Today/Train and in the WhatsApp morning message. They can still swap anything on the spot.

## Don'ts
- Don't invent loads without data; say "calibrar" instead.
- Don't overwrite an active or completed day, and don't move the program's rotation by hand.
- Don't print or commit secrets, personal logs or the context JSON.
