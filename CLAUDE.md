# Fit-OS

Self-hosted personal gym coach: a WhatsApp group (through WAHA) plus a web dashboard, in Docker Compose on any
amd64/arm64 host. Several people can share a server (one database each). Design: `docs/design.md` (long: read
only the section you need); architecture: `docs/architecture.md`; research: `docs/research.md`.

Your own deployment (host, paths, group id, pending items) lives in the gitignored `deploy/target.env` and
`deploy/local/` (manifest). Read it when deploying; never copy anything from it into tracked files.

## Commands
- `make venv` (once), `make test` and `make lint` before calling anything done; paste the output.
- `make dev` → http://localhost:8090 with fake WAHA and Claude; `/dev/wa` simulates the group. `make catalog`
  then `make demo` for a full synthetic dataset. Stop with `make down` when done.
- `make revision m="..."` generates a migration (SQLite, batch mode).
- Server: `make status` is safe. `make deploy` deploys **origin/main** (push first) and `make rollback`
  reverts: a person decides those.
- `make waha-hook ACTION=add|remove CONFIRM=1` changes a WhatsApp session other apps may share (restarts it):
  a person decides.

## Architecture
- One container, one process: FastAPI + scheduler (`scheduler.py`) + outbox drainer. One uvicorn worker.
- SQLite WAL + SQLAlchemy 2 + Alembic (`render_as_batch`). Models in `fitos/core/models.py`.
- One database per person (`$DATA_DIR/users/<name>/fitos.sqlite`) plus `accounts.sqlite` (`core/accounts.py`).
  Everything runs "as" a user (`core/tenancy.py`): `session_scope()`/`get_db` open that user's database and raise
  without one. Web: the signed-in account (or the one an admin views, read-only). WhatsApp: the group's owner.
  Scheduler/outbox: a loop over accounts. CLI: `fitos --user <name>`. Never add a fallback that guesses a user;
  new background work must run inside `tenancy.as_user(...)`.
- Logic lives in `service.py` and pure modules (`training/progression.py`, `substitution.py`, `wa/parser.py`,
  `labs/ranges.py`); routes and `wa/router.py` only validate, call and render.
- External things (WAHA, Claude) go through a class with a double: `FakeWaha`, `FakeLLM`.
- Numbers come from code (progression, e1RM, PRs, metrics, substitute ranking, lab status); the LLM picks and
  explains, and only mutates data through validated tools. The LLM is optional (`LLM_MODE=off` by default).

## Security and privacy
- Never read `.env` or `data/` (real health data). Never real data in fixtures, logs, commits or commit
  messages. Demo data is synthetic (`fitos/demo.py`).
- Never print `WAHA_API_KEY`, `ANTHROPIC_API_KEY`, `PROXY_SECRET` or HMAC keys.
- The dashboard is reached only through Caddy (profile `edge`: internal HTTPS; the app's own sign-in, users in
  `$DATA_DIR/accounts.sqlite`). The app trusts Caddy through `PROXY_SECRET` (tests in `test_auth.py`);
  `fitos:8090` stays on 127.0.0.1. Keep `TRUST_TAILSCALE_HEADERS=false` unless `tailscale serve` is the only
  way in: otherwise anyone can forge that header.
- Source IPs can't authorize anything: docker-proxy replaces them with the bridge gateway.
- The webhook sees EVERY chat of the WhatsApp number: anything that isn't an account's group is dropped before
  it is logged or stored (there is a test). One person's data never reaches another's database or page
  (`test_multiuser.py`); an admin's "view as" is read-only.

## Conventions
- The app is bilingual (es/en). Every user-visible string goes through `_("Spanish text", **kw)` from
  `fitos.i18n` (Spanish is the key) and gets its English entry in the area's `i18n/en_*.py`;
  `test_i18n.py` fails on a missing one. Data with two fields uses `pick(es, en)` (`display_name`,
  `instructions`, `description`). Dynamic labels (muscles, focus names) are wrapped with `_()` where shown.
  WhatsApp commands must keep working in BOTH languages whatever the preference.
- Docs, comments in new code and commits in English (`feat(wa): …`); identifiers in English. Ruff at 110.
  Commits straight to `main`.
- Outside services that bring a person's data in (smart scales, wearables, health exports) live in
  `fitos/integrations/<name>.py` with a double for tests, and their link / status / sync-now UI on the
  **Integrations** page (`integrations.html`, routes under `/integraciones/<name>`), never in Profile or
  Settings. Credentials: encrypted per person in their data dir (see `integrations/renpho.py`), never in the
  database. The "coming soon" cards there are the backlog.

## Mistakes we already made
- The compose service must NOT be called `app` (other apps' WAHA webhooks often point at `http://app:8000`).
- `PUT /api/sessions/{s}` replaces the whole session config and restarts it; the global (env) webhook adds
  to the session ones.
- No trailing comments in `.env` lines.
- Over ssh with a heredoc, `docker compose run/exec` swallows stdin: use `</dev/null`.
- Hosts without an RTC boot in the past: nothing scheduled runs with a clock earlier than 2026.
- There is no curl inside the WAHA container: `node -e "fetch(...)"`.
- WAHA on small ARM boards: `devlikeapro/waha:noweb-arm`.
- Caddy + a `*.ts.net` hostname: Caddy asks tailscaled for the cert and TLS fails. Use the IP or short name.
- Opus 5.5: thinking can't be disabled, forced `tool_choice` returns 400, check `stop_reason`; the history
  you resend is never edited.
- WhatsApp message ids come in three shapes: use `bare_id`.
- A single-file bind mount (the Caddyfile) keeps pointing at the old file after a git checkout replaces it:
  `caddy reload` reads the stale copy. `ops/server.sh` restarts Caddy when the files differ.
- Tailwind v4: CSS outside `@layer` beats utilities. Component classes (`.input`, `.btn`…) live in
  `@layer components`, or `w-16`/`py-1` on an element are silently ignored.
- Browsers send an empty optional field as `""`: a `int | None = Form(None)` / `float | None` parameter answers
  422 (it broke logging bodyweight sets and finishing a workout). Take `str = Form("")` and parse with
  `form_num` / `form_int` (`web/ui.py`); `test_forms.py` checks every route.

If Claude makes the same mistake twice, the fix goes here.
