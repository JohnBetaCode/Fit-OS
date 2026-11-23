---
name: session-status
description: "Give the person a short text balance of today's workout in Fit-OS, right in the chat: what's done set by set, what's still pending, totals per muscle, how it sits against the last 7 days and what's sensible next. Use when they ask how it's going mid-workout or after it (\"¿cómo voy?\", \"dame el balance\", \"resumen de hoy\", \"qué me falta\", \"how am I doing\", \"session summary\"). Reads `live` from `fitos context`; it never changes data."
---

# Today's balance, as text

A coach glancing at the logbook between sets: short, readable on a phone, every number from their data.
Answer in the chat, in their language (the app's, usually Spanish). No files, no artifacts. Workouts are health
data: temp files go in /tmp or `deploy/local/` (gitignored) and are deleted afterwards. Never commit or paste
the JSON.

## 1. Read
Account and server as in the plan-day skill (`deploy/target.env`, `U=<username>`).
```bash
make -s context-remote U=<u> | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k: d.get(k) for k in ("today","live","week","recovery","plans","profile")}, ensure_ascii=False))'
```
- `live`: the workout in progress, else today's latest one. `null` = nothing started today: say it and show
  today's plan from `plans` instead (or offer the plan-day skill).
- `live.items[]`: `status` (pending | active | done | skipped), `current` (what the app shows now),
  `planned` (sets, reps, kg) and `sets` done (`reps`, `kg`, `rir`; cardio as `min`; `pr` when it was a record).
- RIR = reps left in reserve: lower is harder (0 failure, 1 very hard, 2 hard, 3 challenging, 4 moderate).
- `live.totals`: `work_sets`, `sets_by_muscle`, `volume_kg`, `cardio_min`. `live.minutes`: time since start.
- `week.hard_sets_by_muscle` vs `week.target_hard_sets`: the last 7 days (moving window), today included.
- `recovery`: `fatigued` (red) / `recovering` (orange) / `ready` (green) per muscle.
- `profile.session_minutes`: their usual session length.

## 2. Write the balance
Keep it to one screen:
1. **One line**: focus, minutes so far, work sets, cardio minutes.
2. **Done**: a table, one row per exercise with sets (`12×40 · 10×50 · 7×50`, kg and RIR when logged).
   Mark records (`pr`) and sets well above or below the plan.
3. **Pending**: items still `pending` or `active` with their planned sets × reps × kg. An item with sets but
   still `pending` was probably finished without the ✓: say so (they mark it done, or remove leftovers with ⋯).
4. **Per muscle**: today's sets by muscle, plus the 7-day total vs the target (e.g. "pecho 14 hoy, 22 en 7 días:
   por encima de la meta"). Use the app's muscle names in their language.
5. **What next**, 1–3 short lines from the numbers: what's left to hit the plan, a muscle that's already over
   the 7-day target (stop there), time left vs `session_minutes`, a load to try on the next set (double
   progression, as in plan-day). If notes mention being ill or tired, lean toward finishing early.

## Don'ts
- Don't invent sets or loads; if something they told you isn't logged, say it's missing (or log it only if
  they ask).
- Don't change plans or sessions here: this skill only reads.
- Don't paste the context JSON or anything from `deploy/local/`.
