# One set of coach tools for WhatsApp, the web chat and MCP

Date: 2026-11-27 · Status: draft for approval (builder-checked)

## Intent

Everything the owner has been asking an agent to do by hand over SSH (write or move a day's plan, add or swap
an exercise mid-workout, log sets and cardio, mark a rest day, honest reviews that read training, WHOOP,
activities, the scale and labs) should be askable from:

1. the WhatsApp group (the coach that already answers free text),
2. the embedded chat on the **Coach** tab,
3. Claude Code on the owner's computer, through an MCP server, so the agent stops writing one-off scripts.

The why: the person trains with the phone in hand and wants to say "move today's plan to tomorrow" or "add a
tuck crunch" to the coach, and get the same answers and changes the agent gives today. One implementation
keeps the three surfaces from drifting apart.

## Goals

- One tool registry (schemas + handlers) used by the LLM coach and by the MCP server.
- The tools cover what has been done by hand: day plans (write, move, rest day), live-session edits (add,
  swap, remove, reorder, finish an item), logging (sets, cardio minutes, body weight), and read-only data
  for reviews (context, plans, WHOOP days, activities, rest summary, body composition, labs).
- In WhatsApp and the web chat, **every change asks first**: the coach proposes, the person answers yes or no.
  Reading and logging are immediate (logging is what the person just said they did).
- Claude Code reaches the tools over the SSH it already uses: no new port, no new credential.
- An admin can turn the coach on and enter the Anthropic API key from the Integrations page (encrypted
  server vault; `.env` still wins), like the WHOOP app credentials.

## Non-goals

- The Claude mobile app / claude.ai as a client (needs a public HTTPS endpoint with OAuth: a later, separate
  decision).
- New analysis features. The tools expose data and actions that already exist.
- Replacing the deterministic WhatsApp commands. They keep working, LLM or not.

## Anti-goals (would be a failure even if it works)

- The coach changes a plan without the person saying yes.
- A "sí" meant to close the current exercise is swallowed as a confirmation (or the other way round).
- The MCP server can reach another person's data, or picks a user by default.
- The API key or tokens end up in git, logs, chat or the database.

## Constraints

- Tenancy: every tool runs inside `tenancy.as_user(name)`. The MCP process is started per person
  (`fitos mcp --user <name>`) and refuses to start without one; no fallback that guesses a user.
- Logic stays in services; tools only validate, call and shape output (CLAUDE.md "Architecture").
- Bilingual: tool descriptions for the LLM stay in Spanish as today; user-visible confirmation and error texts
  go through `_()` with English entries.
- Budget: the coach already stops at `llm_daily_budget_usd` (default US$1). MCP use costs no API money.
- `LLM_MODE` stays `off` by default for every install; the web toggle is per server.

## Approaches considered

- **A. MCP over SSH (chosen).** A `fitos mcp --user <name>` CLI command speaks MCP over stdio. Claude Code
  launches `ssh <host> docker compose exec -T fitos fitos mcp --user <name>`. Security is the SSH key; nothing
  new is exposed. Works from any machine with SSH to the server; the phone uses WhatsApp.
- **B. `/mcp` HTTP endpoint behind Caddy with per-person tokens.** Any MCP client on the tailnet, and the path
  to claude.ai later. Costs token issue/revoke/storage and more attack surface over health data. Deferred.
- **C. An MCP that calls the web app.** Rejected: the web has no API for this and it would duplicate logic.

The owner picked A.

## Design

### 1. Tool registry (`fitos/coach/tools.py`)

Each tool keeps its JSON schema and handler, plus a `kind`: `read`, `log` or `change`.

- Existing, kept: `get_profile`, `get_today`, `get_recent_sessions`, `get_exercise_history`,
  `search_exercises`, `find_alternatives`, `get_metrics`, `get_labs`, `get_body_metrics` (read);
  `log_set`, `delete_last_set`, `log_body_weight` (log); `swap_exercise` (change).
- New read: `get_context` (`coach/context.build`, the JSON the plan-day skill reads), `get_plans` (a date
  range), `get_wearable_days` (WHOOP daily values for a range and kinds), `get_activities` (range, sport),
  `get_rest_summary` (`body/rest.build` tiles, flags, last night), `get_composition` (`body/composition`).
- New log: `log_cardio` (minutes + optional details, `assist.log_cardio`). `log_set` and `log_cardio` take an
  optional `exercise_id` so a set can go to any exercise of today's session, not only the current one (people
  log the shrug they just did while the app shows the row). Editing past sessions stays on the web.
- New change: `put_day_plan` (`put_custom_plan`), `move_plan`, `rest_day`, `add_exercise` (`assist.add`),
  `remove_exercise` (`assist.remove`), `move_exercise` (`assist.move`), `finish_exercise` (`assist.finish_item`).
- `move_plan(from, to)` (new service, `training/service.py`): copies the source plan's items, focus, equipment
  profile and `program_day_id` to the target date, so the rotation advances when it is done on the new day.
  The source becomes `skipped` (a rest day), which is what "move today's plan to tomorrow" has meant every time.
  A `proposed`/`accepted` plan on the target is replaced (the confirmation summary says so). Refused: a
  target or source that is `active`/`completed`, a source with a live session, a target in the past.
- `rest_day(date)`: a not-started plan becomes `skipped`; a day with no plan yet gets a `DayLog` of a new kind
  `rest` (`body/days.py`), which the morning job already treats as "marked day" and does not send a plan for.
  Refused on a day with a live or completed session.

Handlers raise `ToolError` with a person-facing message; every change writes an `AuditLog` row as today.

### 2. Confirmation in the chats (`coach/agent.py`)

- When the LLM calls a `change` tool from WhatsApp or the web, the handler is **not** run. The call is
  validated (schema + a dry run inside a rolled-back transaction that raises on bad ids or dates) and added to
  the person's pending proposal (pref `coach_pending`: a list of calls with a human summary each, plus
  channel, thread and created_at). The tool result tells the model "pending confirmation: <summary>"; the
  model asks in its reply.
- One proposal per turn, all-or-nothing: every change call of the same coach turn joins the list ("rest today
  and put the plan on Friday" is two calls, one "sí"). A new turn that proposes changes replaces an older
  proposal. "Sí" runs the calls in order in one transaction; if one fails nothing is kept and the reply says
  which one failed.
- A proposal is answered in the channel it was made in; it lives 30 minutes.
- WhatsApp routing, checked first: a yes/no word while a fresh proposal exists → confirm/cancel. A yes/no
  within 2 hours of a proposal that expired → "that proposal expired, ask me again" (so a late "sí" never
  falls through to `_idle`, where "sí" starts today's plan). Anything else, or with no proposal: the router
  as today ("sí" before a session starts the plan; during one it reaches the coach).
- Web chat: the reply shows "Confirm" / "Cancel" buttons under the message (POST routes); typing yes/no works
  the same way.
- `read` and `log` tools run immediately, as today.

### 3. MCP server (`fitos/mcp_server.py` + CLI `fitos mcp --user <name>`)

- Official Python SDK (`mcp` package, version pinned in `requirements.txt`), stdio transport.
- `--user` is required for `mcp`: the `FITOS_USER` / single-account defaults of `cli._resolve_user` are refused.
  SSH to the server means server admin; other people use WhatsApp and the web. Per-person MCP for non-admins
  would need a pinned forced command per SSH key (out of scope, noted in the docs).
- Lists the same registry. Annotations: `read` → `readOnlyHint`; `change` → `destructiveHint`. All tools
  run immediately: Claude Code's own permission prompt is the confirmation on this side.
- Each call opens its own `session_scope()` inside `tenancy.as_user(name)`.
- Repo docs: a `.mcp.json` example and a line in `docs/development.md`; the real host stays in the
  gitignored `deploy/target.env` (a `make mcp-config` target prints the command for Claude Code).

### 4. Turning the coach on from the web

- Integrations → Claude card (admin only, server-wide): Anthropic API key (password field, server vault
  `anthropic`; a key in `.env` wins, as with WHOOP), the mode (off / live) and the coach model (Opus / Sonnet /
  Haiku from the existing price table). Mode and model saved on the web override the environment; when never
  saved, the environment applies (so `LLM_MODE=fake` for development keeps working). `llm.available()` and
  `get_llm()` read these.
- The daily budget stays per person in Settings (`llm_daily_budget_usd`, each person's own `LlmCall` sum).
- Non-admins see the status, not the form.

### 5. Prompts

- The system prompt gains: the tool kinds, "ask before changing anything; never claim a change that is only
  pending", and the plan-day rules the skill uses (double progression, evidence in each note).

## Testing

- Registry: every tool has a schema, a kind and a handler; read tools run against the demo data.
- Confirmation: change calls from WhatsApp store one proposal and change nothing; two calls in a turn are
  confirmed together; "sí" runs them, "no" drops them; a failure rolls all back; a late "sí" after expiry
  gets the "expired" reply; with no proposal "sí" behaves as today (starts the plan before a session, reaches
  the coach during one).
- `move_plan` and `rest_day`: the target/source rules above, the rotation moving with the plan, the morning
  job skipping a `rest` day.
- MCP: an in-process client lists the tools and calls one read and one change against a temp database;
  starting without `--user` fails.
- Web: confirm/cancel routes, admin-only key form (403 for others and in read-only view), forms accept empty
  strings (`test_forms.py`).
- `test_i18n.py` covers the new texts.

## Left to the builder

- Exact schemas and Spanish tool descriptions.
- How `move_plan` refreshes notes that say "yesterday" (rewrite with dates, or leave the notes and add one
  line on the move).
- Output size limits for big reads (`get_context` can be large; the coach truncates at 20 000 characters).
