# Using Fit-OS

Fit-OS has two faces: the **WhatsApp group**, where you train, and the **dashboard**, where you plan and look
back. Both speak English or Spanish (Settings → Language, or `DEFAULT_LANGUAGE`), and the group understands
commands in **both languages whatever the setting**: `busy` and `ocupada` do the same thing.

## A training day in the group

1. **Morning.** At your morning hour (Settings) on a training day, Fit-OS posts today's session: focus,
   place, duration, and each exercise with sets × reps and a target weight from your history. Ask for it any
   time with `today` / `hoy`.
2. **Before you start** reply with a number:
   `1` start · `2` change place (e.g. gym → home equipment) · `3` another focus · `4` rest day.
3. **During the session** Fit-OS sends one exercise card at a time (target, last time, rest timer, the
   animation as a short video). Log sets as you go:

   | English | Español | Means |
   |---|---|---|
   | `10x40`, `10 at 40`, `10 reps with 40` | `10x40`, `10 de 40`, `10 reps con 40` | 10 reps with 40 kg |
   | `8x72.5 r2`, `@8`, `rpe 8`, `to failure` | `8x72,5 rir2`, `@8`, `al fallo` | with reps in reserve, RPE, or to failure |
   | `3 sets of 10 at 40`, `10x40, 9x40` | `3x10x40`, `3 series de 10` | several sets in one message |
   | `10x50lb`, `10 x 100 lbs` | `10x45 libras` | pounds are converted to kg |
   | `10` | `10` | 10 reps at the target weight (asks for the weight if there is none yet) |
   | `12 bw`, `12 +10` | `12 pc`, `12 +10` | bodyweight / bodyweight plus 10 kg |
   | `done`, `next` | `listo`, `siguiente` | next exercise |
   | `busy`, `taken` | `ocupada` | the machine or bench is taken → best substitute you can do now |
   | `another`, `swap` | `otra` | another substitute |
   | `no barbell`, `don't have a bench` | `no tengo barra`, `sin banco` | missing equipment → substitute without it |
   | `skip` | `salta` | skip this exercise |
   | `video`, `how`, `tips` | `video`, `como`, `tips` | the animation again / technique steps |
   | `one more`, `+1` | `otro set`, `+1` | one extra set |
   | `undo` | `deshacer` | delete the last set |
   | `fix 10x42.5` | `corrige 10x42,5` | fix the last set |
   | `status` | `estado` | where you are in the session |
   | `finish`, `done for today` | `fin`, `terminar` | finish: summary with tonnage, PRs and what goes up next time |

   `done` ends the current *exercise*; only `finish` / `fin` (or `gotta go` / `debo terminar` when you run out
   of time) ends the session; the summary lists what was left. `skip` skips an exercise;
   `skip today` / `rest` takes the day off before you start.

   **Warm-ups and rest.** The first heavy compound lift of each muscle comes with a warm-up ramp (empty bar,
   ~45 %, ~65 % and, when heavy, ~85 % of the working weight, rounded to your equipment). Log warm-up sets
   with `w 10x20` / `c 10x20` if you want them on record; they never count for PRs, volume or progression.
   After each set Fit-OS posts "⏱️ Rest's over" when the rest time (Settings) is up, unless you logged
   something first. Both can be switched off in Settings. Because Fit-OS writes from your own number,
   WhatsApp shows these messages without a notification sound.

   **Swipe to reply.** Replying to an exercise's card (swipe it right in WhatsApp) logs to *that* exercise,
   in any order; so does replying to a set confirmation, and `other`/`skip` as a reply act on that exercise.
   Replying to a card of today's plan before `1` starts the session. With Settings → *Exercise cards in
   WhatsApp* → *All at the start*, every card arrives when you start, ready to be swiped. Timed exercises
   (cardio, planks) take a duration: `25 min`, `45 s`.

4. **Anywhere:** `sick` / `enfermo`, `travel` / `de viaje`, `injured` / `lesionado` mark today (its plan
   becomes a rest day, the rotation doesn't move; the Week tab can mark past days too), `i'm better` /
   `ya estoy bien` ends it; the morning after a sick day asks how you are, and the Sunday summary counts them.
   `weight 80.5` / `peso 80.5` logs body weight, `waist 84, chest 102` / `cintura 84, pecho 102`
   logs tape measurements (waist, chest, arm, hips, thigh, neck, body fat), a photo with no caption (or with
   `progress` / `progreso`) is saved as a progress photo, `ping` checks the connection, `help` /
   `ayuda` lists commands. Anything Fit-OS doesn't understand is answered with what you can write at that moment. With the coach enabled, anything else is a question for Claude ("how is my bench
   going?", "my shoulder hurts, what should I change today?"), answered in your language.

If you go quiet in the middle of a session Fit-OS nudges you after 45 minutes and closes the session after 90.
Nothing is sent during quiet hours.

## How the numbers are decided

- **Double progression.** Each exercise has a rep range. Hit the top of the range on every set with the
  target RIR and the next session goes up one step (+2.5 kg upper body / +5 kg lower body on a barbell, the
  next dumbbell pair you own, the next stack plate); otherwise you add reps at the same weight. Falling
  short of the bottom of the range again triggers a step back.
- **RIR autoregulation.** Sets logged as too easy or too hard nudge the next target.
- **e1RM and PRs.** Estimated one-rep max per set (Epley, counting reps in reserve, up to 12 reps); you get 🏆 for a new best e1RM, best weight, or
  best reps at a weight.
- **Substitutes** are ranked by code: same movement pattern and primary muscle, equipment you have at that
  place, no conflict with your injuries, similar loading, and a preference for staples over oddities.
- **Weekly volume.** Hard sets per muscle (secondary muscles count half) against a 10–20 band (Settings).

## The dashboard

| Tab | What's there |
|---|---|
| **Today** (Hoy) | Today's plan with buttons to switch place, focus or rest; the week strip; sets per muscle; warnings from your labs; latest records |
| **Week** (Semana) | The seven days: what's done, today, and the program rotation projected forward; build a day with the focus and place you want, rest a day, and edit a plan before it starts (sets, reps, loads, RIR, order, swap for a ranked alternative, add or remove exercises). Planned sets per muscle for the week against your band |
| **Workouts** (Entrenos) | Every session and set: edit a workout's date, RPE, duration and notes, add a missed set, edit or delete sets, delete a whole workout (PRs are recomputed) |
| **Progress** (Progreso) | Overview: e1RM per lift over time, body weight, measurements, progress photos (first vs latest), weekly volume per muscle against the target band, consistency and streak. **My exercises**: every exercise you've done in a sortable table (search, muscle, period): sessions, first and latest top set, best, change and a trend line |
| **Exercises** (Ejercicios) | The 1,300+ exercise bank with animations, filters by muscle and equipment, your history on each |
| **Labs** (Exámenes) | Every biomarker with your values over time against reference ranges for your sex and age, and your own goals; nutrition view |
| **Coach** | Chat with Claude (when `LLM_MODE=live`) |
| **Profile** (Perfil) | Goal, experience, days and times, injuries, equipment per place: what the planner uses |
| **Integrations** (Integraciones) | Placeholders for Whoop, Apple Health and Garmin (not implemented yet) |
| **Settings** (Ajustes) | Language, morning plan and quiet hours, rest timers and alerts, warm-ups, exercise cards, Sunday summary, RIR target, weekly set band, program template, coach budget, **export everything** (ZIP with CSV, JSON and a printable report), system health |

## Sunday summary and export

Every Sunday (Settings → hour) the group gets the week: workouts against your target, sets and tonnage
against the previous week, cardio minutes, records, muscles below or above the volume band, body-weight
change, and which loads go up next week.

Settings → *Your data* downloads everything you logged as a ZIP: `workouts_sets.csv` (one row per set),
body metrics, labs, your lab targets, favourites, every table as CSV and as one JSON, and `report.html`, a
printable summary (open it and print to PDF). On the server: `docker compose exec fitos fitos export
/data/export.zip`. The file contains health data.

## Programs

Settings → Program switches the template. The defaults follow the evidence-based "each muscle twice a week"
rule (full body, upper/lower, push/pull/legs). `combo_3` is the classic gym pairing instead: chest + shoulders
+ biceps, back + triceps, legs + abs, each day opening with 15–20 min of incline walking and with the main
lifts first, so finishing early drops accessories.

## A hand-made plan for a day

The planner builds each day from your program. To decide a day yourself (or with an assistant), write a YAML
file and load it; it replaces that day's plan if it hasn't started, doesn't move the program rotation, and
gets the usual target weights from your history unless you set them:

```yaml
date: 2026-11-16
focus: Chest + cardio
items:
  - {exercise: "0025", sets: 4, reps: [6, 8], rir: 2}           # ids from the Exercises tab
  - {exercise: "0314", sets: 3, reps: [8, 10], rir: 2, weight_kg: 22.5}
  - {exercise: "3666", sets: 1, minutes: [20, 25], note: "zone 2"}
```

```bash
docker compose exec fitos fitos plan put /data/plan.yaml     # the file must be visible inside the container
docker compose exec fitos fitos plan show 2026-11-16
```

## On your phone

The dashboard is built for phones and installs like an app: open it in the phone's browser (the phone on your
private network, e.g. Tailscale), then Share → Add to Home Screen (iPhone) or ⋮ → Install app (Android).
Settings → *On your phone* offers Caddy's root certificate (copied by `ops/server.sh` on deploy) to remove the
self-signed warning; install it only on your own devices for your own server.

## Lab results

`catalog/biomarkers.yaml` has 29 markers (lipids, glucose and insulin, liver, kidney, hormones, thyroid,
vitamins and minerals, inflammation) with reference ranges by sex and age. Each value gets a status:
in range, borderline, out of range, or **needs review** when the source value was ambiguous. Your own goals
(stricter than the lab's range, e.g. LDL < 100) show as a second band on the chart.

These are reference ranges, not medical advice; the range printed by your lab and your doctor win.
