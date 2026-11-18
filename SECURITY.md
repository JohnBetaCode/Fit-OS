# Security policy

Fit-OS is a self-hosted personal project: there is no hosted service and no support SLA.

## Reporting a vulnerability

Please report it privately through GitHub: **Security → Report a vulnerability** on this repository.
Do not open a public issue for security problems.

Include what is affected (WhatsApp webhook, dashboard sign-in, Caddy edge, backups…), how to reproduce it,
and the commit you tested. You should get an answer within a couple of weeks.

## Deployment notes

- Keep the dashboard behind the Caddy edge; the app port (`fitos:8090`) stays on 127.0.0.1.
- Secrets (`WAHA_API_KEY`, `ANTHROPIC_API_KEY`, `PROXY_SECRET`, HMAC keys) live only in `.env`, never in git.
- Leave `TRUST_TAILSCALE_HEADERS=false` unless `tailscale serve` is the only way in.
