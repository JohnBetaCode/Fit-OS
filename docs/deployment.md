# Deployment and operations

Fit-OS deploys from git: the server keeps a clone of the repo and its own `.env`, and `ops/server.sh` does
the work there (backup → checkout → build → migrate → start → health check). You can run that script on the
server or drive it from your laptop over ssh with `make`.

## Your target: `deploy/target.env`

```bash
cp deploy/target.env.example deploy/target.env
```

```ini
REMOTE_HOST=me@192.168.1.20      # anything ssh accepts
REMOTE_PATH=~/Fit-OS             # the clone on the server
REMOTE_DATA=~/fit-os-data        # DATA_DIR on the server
AGE_KEY=~/.config/fit-os/backup-age.key
PROFILE_YAML=data/import/profile.yaml
LABS_CSV=data/import/labs.csv
```

`deploy/target.env` and the folder `deploy/local/` are gitignored. Keep your private deployment notes there
(host quirks, which WAHA you share, the group id, pending items); the repo stays host-agnostic.

## Everyday commands (from the laptop)

| Command | What it does |
|---|---|
| `make status` | Deployed commit, DB schema revision, containers, memory, last deploys and backups |
| `make deploy` | Deploys **origin/main**: push first. Pre-deploy backup, build, `fitos migrate` (every account's database), start, waits for `/healthz` |
| `make rollback` | Back to the commit before the last deploy; if the schema changed, restores the pre-deploy DB copy |
| `make backup` | Encrypted DB backup now |
| `make install-cron` | Nightly backup at `BACKUP_CRON` (server time) |
| `make backup-check` | Fetches the newest backup, decrypts it locally with `AGE_KEY`, runs `PRAGMA integrity_check` |
| `make context-remote U=me [DATE=…]` | What a coach needs to plan a day, as JSON (profile, program, recovery, week volume, history) |
| `make catalog-search-remote U=me Q="…" [MUSCLE=…] [PLACE=gym]` | Exercise ids for a plan |
| `make plan-remote U=me FILE=plan.yaml [DRY=1]` | Put a day's plan from a YAML kept out of git (`DRY=1` only checks it). The `plan-day` skill writes these |
| `make remote-logs` | Last 200 log lines |
| `make catalog-remote` | Downloads the dataset and builds the media on the server |
| `make seed-remote` | `scp` your profile and labs files and imports them (never through git) |
| `make demo-remote` | Synthetic demo data (only into an empty DB) |
| `make wa-groups MATCH=Gym` | Lists WhatsApp groups and their ids |
| `make waha-hook ACTION=show\|add\|remove CONFIRM=1` | The Fit-OS session webhook on a shared WAHA |

The remote targets run the `origin/main` version of `ops/server.sh`, so a deploy always uses the script of
the code it deploys. On the server itself: `bash ops/server.sh status|deploy|rollback|backup|install-cron`.

## What a deploy does

1. Takes a lock (one operation at a time), refuses if the checkout has uncommitted changes.
2. Creates the `wa-shared` docker network if it's missing.
3. Backs up the DB (`predeploy`, encrypted) and keeps a plain copy for a fast rollback.
4. Checks out the target commit (detached), builds the image, runs Alembic migrations, `docker compose up -d`
   (the app plus whatever `COMPOSE_PROFILES` enables).
5. Waits up to 2 minutes for `/healthz` and records the result in `$DATA_DIR/deploy/history.tsv`.

`.env` never travels with git: when `.env.example` gains a key, add it to the server's `.env` by hand.

## Dashboard access

The app listens on `127.0.0.1:8090` only. People reach it through the `edge` profile: Caddy on port 8444
with HTTPS (Caddy's internal CA, issued on demand for whatever IP or name you use); the app then asks for a user and
password (`/entrar`, with a 90-day "remember me" cookie).

- Accounts: `$DATA_DIR/accounts.sqlite` (username, display name, bcrypt hash, role, WhatsApp group). Each person
  has their own database, `$DATA_DIR/users/<username>/fitos.sqlite`, and sees only their own data. An **admin**
  manages accounts on the **Users** page (`/admin`) and can look at anyone's dashboard, read-only ("view as").
  From the server: `docker compose exec fitos fitos users add <name> --name "Name" [--role admin]` (asks for the
  password), `fitos users list|password|disable|enable|set-chat`. Disabling keeps the data and signs them out.
- WhatsApp is per account: a message counts only in a group assigned to an account (`fitos users set-chat <name>
  <group id>`); `WA_GYM_CHAT_ID` is the first admin's group. Accounts without a group use the web only.
- A new person's first visit to Today opens the welcome flow (`/bienvenida`: about you, goal and injuries, training
  week, program); people who already trained skip it. The footer shows the version: the short commit for
  everyone, plus the deployed branch for admins (`GIT_BRANCH`, set by `ops/server.sh`).
- CLI commands that touch someone's data take `--user <name>` (or `FITOS_USER`; with a single account it is
  implied). Remote helpers take `U=<name>`: `make seed-remote U=me`.
- Caddy forwards every request with `X-Fitos-Proxy: $PROXY_SECRET` and the user in `X-Fitos-User`, overwriting
  whatever the client sent. The app accepts nothing else, so the port being reachable on the LAN is not a way in.
- Why not trust the source IP (e.g. "only my Tailscale network")? docker-proxy replaces it with the bridge
  gateway, so the app can't see it.
- A certificate every device trusts: with Tailscale, enable *HTTPS Certificates* in the admin console (DNS),
  allow your user once (`sudo tailscale set --operator=$USER`), then
  `tailscale serve --bg --https=443 http://127.0.0.1:8445`. Caddy also listens on plain HTTP at
  `127.0.0.1:${EDGE_LOCAL_PORT:-8445}` for exactly this (same sign-in); open the machine's `https://<name>.<tailnet>.ts.net`
  and set `PORTAL_BASE_URL` to it in `.env`. The name ends up in public certificate-transparency logs.
- Alternative: if you can run `sudo tailscale serve`, it can be the only way in and the app can use its
  identity header (`TRUST_TAILSCALE_HEADERS=true`, `OWNER_LOGINS=you@github`). Never enable that header
  while the port is reachable another way.

## WhatsApp on a shared WAHA

Several apps can share one WAHA (one WhatsApp number) over the external `wa-shared` network. Fit-OS adds a
**session webhook** for itself; WAHA keeps delivering the global webhook to the other app too. Changing
the session config restarts the session for a few seconds, so:

```bash
ssh server 'cd ~/Fit-OS && bash ops/waha-webhook.sh show --raw > ~/fit-os-data/deploy/waha-session-before.json'
make waha-hook ACTION=add CONFIRM=1
```

A health job checks every 5 minutes that WAHA is `WORKING` and (in session mode) that the Fit-OS webhook is
still there; if it disappears, Fit-OS posts a warning in the group.

Two rules learned the hard way: the compose service is called `fitos`, never `app` (many WAHA setups deliver
to `http://app:8000`), and `PUT /api/sessions/<name>` replaces the whole session config, so the script reads
it, changes only the Fit-OS entry, and writes it back.

## Backups

- Format: online SQLite copies of every database (`accounts.sqlite` and `users/<name>/fitos.sqlite`, consistent
  while the app runs) in one tar, encrypted with [age](https://github.com/FiloSottile/age) to the public key(s)
  in `BACKUP_RECIPIENTS_FILE`: `db-<ts>-<sha>-<revision>.tar.age`. Restore with
  `age -d -i key db-….tar.age | tar -xf - -C <data dir>` (app stopped). Backups from before accounts are a
  single `.sqlite.age`. The private key
  stays on your laptop: a stolen server disk gives nothing away.
- Kinds: `nightly` (7 kept), `weekly` (Sundays, 4 kept, plus everyone's `photos/`), `predeploy`, `prerollback`,
  `manual` (5 kept each), under `$DATA_DIR/backups/`.
- Set up once:
  ```bash
  age-keygen -o ~/.config/fit-os/backup-age.key          # on your laptop; note the public key it prints
  ssh server 'echo age1... > ~/fit-os-data/deploy/backup-recipients.txt'
  # .env on the server: BACKUP_RECIPIENTS_FILE=/home/me/fit-os-data/deploy/backup-recipients.txt
  make install-cron && make backup && make backup-check
  ```
- Copy `$DATA_DIR/backups` off the server from time to time; the media can always be rebuilt from the dataset.

## Rollback

`make rollback` goes back to the commit recorded before the last deploy. If that deploy changed the schema (or
the data layout, like the move to one database per person), the current databases are moved aside
(`deploy/rolledback-<ts>/`) and the pre-deploy copies (`deploy/predeploy/`) are restored: anything logged since
the deploy is in the moved-aside files. SQLite downgrades are not attempted.

### From one person to accounts

An install from before accounts has a single `$DATA_DIR/db/fitos.sqlite`. The first deploy that knows about
accounts stops the app and adopts it: the database is copied to `users/<name>/fitos.sqlite` with SQLite's backup
API, every table's row count is compared, the account is created as admin with the password from the old
`caddy/users.caddy` and the `WA_GYM_CHAT_ID` group, and the old file is renamed `fitos.sqlite.adopted-<ts>` (never
deleted). `make deploy ADOPT_AS=<name>` picks the username; by default it is the one in the old users file.

## Resource notes

- `fitos` has `mem_limit: 384m` and normally uses 80–150 MB; Caddy ~15 MB; WAHA (NOWEB) 300–500 MB.
- Building the media (GIF → MP4 for 1,300+ exercises) is the only heavy job: minutes on a laptop, up to an
  hour on a small ARM board. It runs once.
- Boards without a real-time clock boot in the past; the scheduler refuses to run anything until the clock
  reads 2026 or later.
