# Setup

Fit-OS runs anywhere Docker Compose runs: a laptop for development, or any always-on amd64/arm64 box (a mini
PC, a NAS, a Raspberry Pi 4/5, a Jetson) for real use. It needs about 150 MB of RAM for the app, plus ~500 MB
if you also run the bundled WhatsApp gateway.

- [1. Try it locally (no WhatsApp, no API keys)](#1-try-it-locally)
- [2. Run it for real on a server](#2-run-it-for-real-on-a-server)
- [3. Connect WhatsApp](#3-connect-whatsapp)
- [4. Your own data](#4-your-own-data)
- [5. Optional: the Claude coach](#5-optional-the-claude-coach)
- [Configuration reference](#configuration-reference)
- [Troubleshooting](#troubleshooting)

## 1. Try it locally

```bash
git clone <repo-url> Fit-OS && cd Fit-OS
make dev        # builds, migrates and starts http://localhost:8090 (creates .env from .env.example)
make catalog    # downloads the exercise dataset and converts 1,300+ animations to MP4 (~10 min, ~220 MB)
make demo       # fills the empty DB with ~10 weeks of synthetic training, body weight and lab results
```

Open http://localhost:8090. In development WhatsApp and Claude are fakes and you are logged in as
`dev@localhost`. The WhatsApp group is simulated at **http://localhost:8090/dev/wa**: type `today`, `1`,
`10x40`, `busy`, `finish` (or the Spanish `hoy`, `ocupada`, `fin`), exactly as you would in the real group.

`make down` stops it. Tests and lint don't need Docker: `make venv` once, then `make test lint`.

## 2. Run it for real on a server

On the server (any Linux host with git and Docker Compose v2):

```bash
git clone <repo-url> ~/Fit-OS && cd ~/Fit-OS
cp .env.example .env
```

Edit `.env` (see the [reference](#configuration-reference)). The minimum for a working install:

```ini
APP_ENV=production
TZ=Europe/Madrid                 # your timezone
DEFAULT_LANGUAGE=en              # en | es, switchable later in Settings
DATA_DIR=/home/me/fit-os-data    # outside the repo
COMPOSE_PROFILES=edge,waha       # Caddy in front; drop `waha` if you already run WAHA
PROXY_SECRET=<openssl rand -hex 32>
PORTAL_BASE_URL=https://192.168.1.20:8444
WAHA_API_KEY=<openssl rand -hex 24>
WAHA_WEBHOOK_HMAC_KEY=<openssl rand -hex 32>
WAHA_HOOK_MODE=global            # `session` if you share an existing WAHA, see below
WAHA_DASHBOARD_PASSWORD=<something>
```

Start everything, then create your account (the first admin; it gets the `WA_GYM_CHAT_ID` group):

```bash
bash ops/server.sh deploy        # creates the docker network, builds, migrates, starts, waits for /healthz
docker compose exec fitos fitos users add me --name "Me" --role admin   # asks for a password
```

More people: the **Users** page in the dashboard (admins only). Each person gets their own database and sees only
their own data; WhatsApp stays connected to your group.

Open `https://<server-ip>:8444`, accept the certificate warning once (Caddy's internal CA), and log in.
Then load the exercise bank inside the container:

```bash
docker compose exec fitos fitos catalog fetch
docker compose exec fitos fitos catalog build-media     # slow on ARM boards: ~30–60 min
docker compose exec fitos fitos catalog import
```

From then on you can operate the server from your laptop: see [deployment](deployment.md).

### Reaching it from your phone

The dashboard is HTTPS + password on port 8444 of the server. Over your home Wi-Fi that's
`https://<lan-ip>:8444`. From anywhere, put the server on a private network such as Tailscale or WireGuard
and use its private IP. Don't open the port to the internet. Typing `http://` is fine: Caddy redirects it to `https://` on the same
port. With Tailscale, use the IP or the short
MagicDNS name: a `*.ts.net` name makes Caddy look for a Tailscale certificate it can't reach.

## 3. Connect WhatsApp

Fit-OS talks to WhatsApp through [WAHA](https://waha.devlike.pro/), a self-hosted WhatsApp HTTP API. You
write from **your own WhatsApp number** in a group that only you (and optionally a friend) are in, and
Fit-OS answers in the same group from the same number. Every other chat of that number is ignored before it
is logged or stored.

### Option A: bundled WAHA (simplest)

1. `COMPOSE_PROFILES=edge,waha` and `WAHA_HOOK_MODE=global` in `.env`, then `bash ops/server.sh deploy`.
2. Open WAHA's dashboard through an ssh tunnel (`ssh -L 3000:127.0.0.1:3000 me@server`, then
   http://localhost:3000, user `admin` and `WAHA_DASHBOARD_PASSWORD`). Start the `default` session and scan
   the QR code with WhatsApp → Linked devices.
3. In WhatsApp create a group, e.g. "Gym" (WhatsApp needs one more member to create it; you can remove them
   afterwards).
4. Find its id and set it:
   ```bash
   docker compose exec fitos fitos wa groups --match Gym     # or `make wa-groups MATCH=Gym` from your laptop
   ```
   Put the id (`1203…@g.us`) in `WA_GYM_CHAT_ID` and restart: `docker compose up -d fitos`.
5. Write `ping` in the group. Fit-OS answers `pong · Fit-OS <sha>`.

On small ARM boards set `WAHA_IMAGE=devlikeapro/waha:noweb-arm`.

### Option B: share a WAHA you already run

If another app already runs WAHA with your number (with a global webhook of its own), Fit-OS can share it
without touching that app:

1. Attach that WAHA to the `wa-shared` network (`docker network connect wa-shared <waha-container>`, or add
   the network to its compose file) and set `WAHA_URL=http://<its service name>:3000` and its `WAHA_API_KEY`.
2. `WAHA_HOOK_MODE=session`. Fit-OS adds **its own session webhook** next to the existing one; WAHA delivers
   to both.
   ```bash
   bash ops/waha-webhook.sh show --raw > $DATA_DIR/deploy/waha-session-before.json   # snapshot first
   CONFIRM=1 bash ops/waha-webhook.sh add      # restarts the session for a few seconds
   ```
   `remove` takes it out again and `restore <file>` puts the snapshot back.
3. Set `PEER_NET_CIDR` to the `wa-shared` subnet: other containers on that network can then reach only
   `/healthz` and the webhook.

## 4. Your own data

- **Profile:** fill it in the **Profile** tab (goal, days, times, equipment per place, injuries), or import a
  YAML file with `fitos profile import <file>`.
- **Equipment profiles:** "Gym" (everything) and "Casa" (dumbbells 2–24 kg) are created on first start; edit
  them in Profile. The planner only picks exercises your equipment allows.
- **Lab results:** import a CSV with `fitos labs import-seed <file>` (or `make seed` / `make seed-remote`
  with `LABS_CSV` in `deploy/target.env`). Ranges come from `catalog/biomarkers.yaml` and depend on sex and
  age; values that look ambiguous are flagged for review instead of guessed.

Keep these files under `data/` (gitignored). They reach a server only by `scp` (`make seed-remote`), never
through git.

## 5. Optional: the Claude coach

Everything above is deterministic. With an Anthropic API key, free-text messages in the group and the
**Coach** tab go to Claude, which can read your history and labs and use validated tools (log a set, swap an
exercise, log body weight). It also writes a short rationale for the morning plan.

```ini
LLM_MODE=live
ANTHROPIC_API_KEY=sk-ant-...
```

A daily budget (`llm_daily_budget_usd`, default US$1, in Settings) caps spending; past it the coach switches
off until the next day. Every call is logged with tokens and cost.

## Configuration reference

All settings live in `.env` (see `.env.example`, which documents every key). The important ones:

| Key | What it does |
|---|---|
| `TZ` | Your timezone: calendar days, morning plan, quiet hours |
| `DEFAULT_LANGUAGE` | `en` or `es` for the dashboard and the bot (Settings overrides it); commands work in both |
| `DATA_DIR` | Host folder for the DB, media, labs, backups, Caddy users (outside the repo) |
| `COMPOSE_PROFILES` | `edge` (Caddy), `waha` (bundled WhatsApp gateway) |
| `PROXY_SECRET` | Shared secret Caddy → app. Without it the app trusts no proxy |
| `PORTAL_BASE_URL` | Base for links sent to WhatsApp |
| `TRUST_TAILSCALE_HEADERS`, `OWNER_LOGINS` | Alternative to Caddy when `tailscale serve` is the only way in |
| `WAHA_URL`, `WAHA_API_KEY`, `WAHA_SESSION` | Where WAHA is |
| `WAHA_WEBHOOK_HMAC_KEY` | WAHA signs webhooks with it; unsigned or badly signed events are rejected |
| `WAHA_HOOK_MODE` | `session` (shared WAHA) or `global` (bundled) |
| `WA_GYM_CHAT_ID` | The only chat Fit-OS listens to |
| `PEER_NET_CIDR` | `wa-shared` subnet: peers there only see `/healthz` and the webhook |
| `LLM_MODE` | `off` (default), `live`, or `fake` (development) |
| `BACKUP_RECIPIENTS_FILE`, `BACKUP_CRON` | Encrypted nightly backups, see [deployment](deployment.md#backups) |

In-app preferences (morning plan hour, quiet hours, rest timers, clip policy, weekly set targets, LLM budget)
are edited in the **Settings** tab and stored in the DB.

## Troubleshooting

| Symptom | Check |
|---|---|
| `ping` gets no answer | `docker compose logs fitos \| grep webhook`. Is `WA_GYM_CHAT_ID` right? In session mode, does `ops/waha-webhook.sh show` list `http://fitos:8000/webhooks/waha`? Is the HMAC key the same on both sides? |
| Dashboard says "Sin acceso" | You reached port 8090 directly or `PROXY_SECRET` differs between Caddy and the app (`docker compose up -d` after changing it) |
| TLS error with a `*.ts.net` name | Use the IP or the short hostname |
| Empty Exercises tab | Run the three `fitos catalog` commands |
| Videos don't play in WhatsApp | `fitos catalog build-media` must finish (it adds a silent audio track WhatsApp needs) |
| The morning plan never arrives | Settings → morning hour, quiet hours, and that the day is a training day in Profile |
| Clock in 1970/2000 after a power cut | Boards without an RTC: nothing is scheduled until NTP sets a 2026+ date |
