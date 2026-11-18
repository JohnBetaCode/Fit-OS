# Research (2026-11-15)

Checked against primary sources (official docs, live wger API, WAHA OpenAPI 2026.10.1, GitHub/Docker Hub/PyPI) unless marked otherwise.

## 1. Exercise database with media

| Source | Exercises | Media | License | Spanish | Bulk |
|---|---|---|---|---|---|
| yuhonas/free-exercise-db | 876 | 2 JPGs (start/end) | Unlicense | No | `dist/exercises.json` 1 MB, repo ~97 MB |
| hasaneyldrm/exercises-dataset | 1,324 | 180x180 GIF each (129 MB) + JPG thumbs | data MIT; media © Gym visual (redistributed with permission, attribution required; personal use is a grey area) | Yes, 10 languages | clone; JSON 17.4 MB |
| wger | 973 | 394 images, 78 videos (raw, need transcoding) | per-entry CC-BY-SA/CC-BY/CC0 | 644/973 | `/api/v2/exerciseinfo/` |
| ExerciseDB | v1 1,500 / v2 11k | GIF / MP4 | hosted API only | ? | none |
| MuscleWiki | 1,700+ | MP4 | proprietary, no re-hosting | — | forbidden |

- free-exercise-db: primary/secondary muscles (17), equipment (12 + null, a single "machine" bucket), category, instructions, force, mechanic, level.
- exercises-dataset: target (19), muscle_group, secondary_muscles, body_part, instructions+steps in 10 languages, equipment 28 values (leverage machine 81, smith 48, sled 15, cable 157, dumbbell 294, body weight 325). No force/mechanic.
- Substitution ("machine busy", "only dumbbells"): exercises-dataset is the only one with fine-grained equipment: same `target`, equipment ⊂ available set minus the busy class → Claude picks among top candidates.
- **Rec:** exercises-dataset as catalog (convert GIF→MP4 once, keep Gym visual attribution); free-exercise-db as clean-license fallback / enrichment (force, mechanic). Video fallback: cached YouTube URL or search link, sent as text.

## 2. WhatsApp media via WAHA (NOWEB, noweb-arm)

- `POST /api/sendImage` `{session, chatId, file:{mimetype, filename, url|data}, caption}` (JPEG); `/api/sendFile` (documents/PDF); `/api/sendVideo` `{file, caption, asNote, convert}`; `sendVoice`, `sendPoll`, `sendList`, `/api/{session}/media/convert/video`.
- WhatsApp video must be MP4/H.264 → pre-convert with ffmpeg `-movflags +faststart`. No gifPlayback flag; GIF auto-loop not guaranteed.
- Since **2026.6.1 all Plus features are in Core** (media, sessions, storage). The reference host ran **2026.7.2 CORE** → OK.
- Session `config.webhooks` is an array `{url, events, hmac, retries, customHeaders}`; `PUT /api/sessions/{session}` replaces the whole config **and restarts the session**. `WHATSAPP_HOOK_URL` is a single global URL. Whether the global env hook and session webhooks both fire was unverified here; later confirmed in WAHA's source that they add up (see `design.md` §4.1).
- Polls: `sendPoll` + `poll.vote` / `poll.vote.failed` work on NOWEB (needs store to decrypt votes); save poll msg id; fall back to text when a vote fails to decrypt. `sendList` only in direct chats and flaky; buttons deprecated.
- `file.url` must be reachable from the WAHA container (shared docker network) or send base64.

## 3. Integrations

- **Whoop** (developer.whoop.com): OAuth2 code flow; scopes read:recovery/cycles/workout/sleep/profile/body_measurement + offline. Tokens 1 h, refresh rotates (single refresher). v2: cycles (strain), recovery (score, HRV, RHR, SpO2, skin temp), sleep, workouts. Webhooks need public HTTPS → poll every 30–60 min instead. Free for personal use (≤10 members unapproved, secondary source).
- **Garmin:** official **Personal API** (personal key from Garmin Connect web → token, pull-only: activities JSON/FIT + health summaries). `garth` deprecated 2026-03; `garminconnect` 0.3.20 fallback (logins break often).
- **Apple Health:** no server API → Health Auto Export (iOS, Premium ~$6.99/yr) POSTs JSON to our endpoint with shared secret; `export.xml` for one-off backfill.
- **Google Fit** REST shut down 2026; Health Connect on-device only → companion webhook apps (HC Webhook, HCGateway) or skip.
- **Strava:** subscription required since 2026-06, AI-tool restrictions → deprioritise.
- **Withings:** OAuth2, scopes user.info/metrics/activity/sleepevents; access 3 h, refresh 1 yr rotating; `getmeas` weight(1), fat ratio(6), muscle mass(76), hydration(77), bone(88); ≤1 poll / 10 min; use raw HTTP.
- **Rec:** one Connector interface, polling by default. Order: Whoop → Withings / Health Auto Export → Garmin Personal API.

## 4. Whoop-style metrics from training logs

- Tonnage = Σ weight×reps; per muscle primary 1.0, secondary 0.5.
- Hard sets/muscle/week (RIR ≤ 4; direct 1, indirect 0.5): maintenance 4–6, productive 10–20, start 10–12, cap 20, ≥2×/week.
- e1RM: Epley `w(1+r/30)`, Brzycki `w·36/(37−r)`; only r ≤ 10–12; with RIR use r+RIR. Best per exercise per session.
- sRPE load = session RPE × minutes. Monotony = mean daily / SD (7 d); strain = weekly load × monotony; flag monotony > 2 or weekly jump > 15–20%.
- ACWR = 7 d load / (28 d load / 4), EWMA λ = 2/(N+1); 0.8–1.3 normal, > 1.5 spike; needs ≥ 4 weeks; soft flag only.
- Double progression: 6–10 (compound) / 10–15 (isolation); all sets at top → +2.5 kg upper / +5 kg lower / next DB pair; else add reps.
- RIR autoregulation: target RIR 1–3; actual ≥ target+2 → +2.5–5%; miss bottom of range 2 sessions → −5–10%.
- Deload: planned every 4–8 wk, or reactive (e1RM −5% on 2 key lifts × 2 sessions, RPE +1 at same load ×2, ACWR > 1.5, low readiness 3+ days) → ~½ sets at RIR 4+ for a week.
- Readiness without wearable (heuristic, unvalidated): 40% morning check-in (sleep, soreness, energy, stress) + 30% load balance + 30% e1RM trend vs 4-wk baseline; replace with Whoop recovery once connected.

## 5. Existing open-source apps

- wger (AGPL, arm64 image, 7-service stack): pre-planned routines fight a day-by-day coach; weak machine tags. Borrow its target-vs-performed log design.
- Liftosaur: not self-hostable; borrow declarative progression rules.
- workout.cool (UI reference), Ryot (uses free-exercise-db), SparkyFitness (integration reference), open-wearables (wearable aggregator, heavy).
- **Rec:** build our own thin tracker.

## 6. Lab PDF ingestion

- Claude PDF input + structured output (`output_config.format` json_schema / `messages.parse()`; no numeric min/max in schema; `additionalProperties:false`; citations incompatible → store page + raw text). Fields: lab, collection date; per result raw name, canonical key, LOINC, value, text value, comparator, unit, ref low/high/text, flag, page.
- pdfplumber only as a check (every extracted number must appear in the text layer, else review queue).
- Curated dictionary of 60–100 biomarkers (key, LOINC, canonical unit, conversions, Spanish aliases). Reference: tsilva/parselabs (MIT).
- Cost ≈ $0.05–0.15 per 4-page report on Opus. PDFs leave the host (Anthropic API).
