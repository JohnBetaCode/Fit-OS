---
name: deploy
description: "Deploy Fit-OS changes to the server in deploy/target.env. Use when the user wants to deploy, ship, release or push Fit-OS to their server. Covers test locally -> commit+push -> make deploy -> verify, plus the WAHA webhook and gitignored-data caveats."
---

# Deploying Fit-OS

## Where
- Target: `deploy/target.env` (gitignored; template in `deploy/target.env.example`). If it is missing, ask the
  user for the host instead of guessing.
- Host specifics (paths, portal URL, WhatsApp group, which WAHA, pending items): read the private manifest in
  `deploy/local/*manifest*.md` if it exists. It is gitignored: never copy its contents into tracked files.

## Flow
1. `make test lint` green. If UI or WhatsApp changed, try it with `make dev` and `/dev/wa`; then `make down`.
2. Commit + push to `main` (`make deploy` deploys **origin/main**).
3. `make deploy` (pre-deploy backup, build, `fitos migrate`, `up`, waits for `/healthz`). On ARM boards the
   build takes several minutes.
4. Verify: `make status` (healthy, memory well under the 384 MB limit) and `ping` in the group →
   `pong · Fit-OS <sha>`.
5. If it fails: `make rollback` (restores the pre-deploy DB if the schema changed). Deciding a rollback is
   the user's call.

## Caveats
- `.env` and `data/` don't travel with git. New keys in `.env.example` must be added by hand to the server's
  `.env` (set them with sed, never print secret values).
- The `wa-shared` network must exist (`ops/server.sh deploy` creates it); the service is `fitos`, never `app`.
- `make waha-hook ACTION=add|remove CONFIRM=1` restarts a WhatsApp session that other apps may share: only
  with the user's explicit go-ahead, after a snapshot
  (`bash ops/waha-webhook.sh show --raw > <data dir>/deploy/waha-session-before.json`).
- Never print keys (`WAHA_API_KEY`, HMAC keys, `PROXY_SECRET`, Anthropic).
