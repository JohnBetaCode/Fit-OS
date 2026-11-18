#!/usr/bin/env bash
# Fit-OS session webhook on a WAHA instance shared with other apps. Runs ON THE SERVER, from the repo root.
# (Not needed with the bundled WAHA — compose profile `waha` — which sets the webhook through its env.)
#
#   bash ops/waha-webhook.sh show [--raw]        session config (keys masked; --raw unmasked, for a snapshot)
#   CONFIRM=1 bash ops/waha-webhook.sh add       add/update ONLY the Fit-OS webhook (the PUT restarts the session)
#   CONFIRM=1 bash ops/waha-webhook.sh remove    remove the Fit-OS webhook (leaves the rest as it was)
#   CONFIRM=1 bash ops/waha-webhook.sh restore F restore the config saved in F (output of `show --raw`)
#
# A global webhook of another app (WAHA env WHATSAPP_HOOK_URL) is untouched: WAHA sends to both.
# Reads WAHA_API_KEY, WAHA_SESSION and WAHA_WEBHOOK_HMAC_KEY from the Fit-OS .env without printing them.
# WAHA_HOST (default http://127.0.0.1:3000) is where WAHA's API is published on the server.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

env_get() { [ -f .env ] && sed -n "s/^$1=//p" .env | tail -n1 | tr -d "\"'" || true; }
export WAHA_API_KEY WAHA_SESSION WAHA_WEBHOOK_HMAC_KEY WAHA_HOST FITOS_URL
WAHA_API_KEY=$(env_get WAHA_API_KEY)
WAHA_SESSION=$(env_get WAHA_SESSION)
WAHA_SESSION=${WAHA_SESSION:-default}
WAHA_WEBHOOK_HMAC_KEY=$(env_get WAHA_WEBHOOK_HMAC_KEY)
WAHA_HOST=${WAHA_HOST:-http://127.0.0.1:3000}
FITOS_URL=${FITOS_URL:-http://fitos:8000/webhooks/waha}

cmd=${1:-show}
arg=${2:-}
case "$cmd" in
show) ;;
add | remove | restore)
	[ "${CONFIRM:-}" = 1 ] || { echo "this changes the shared WhatsApp session (restarts it for a few seconds): repeat with CONFIRM=1" >&2; exit 2; }
	;;
*) echo "usage: show [--raw] | add | remove | restore <file>" >&2; exit 2 ;;
esac
[ -n "$WAHA_API_KEY" ] || { echo "WAHA_API_KEY missing in .env" >&2; exit 1; }
[ "$cmd" != add ] || [ -n "$WAHA_WEBHOOK_HMAC_KEY" ] || { echo "WAHA_WEBHOOK_HMAC_KEY missing in .env" >&2; exit 1; }

python3 - "$cmd" "$arg" <<'PY'
import json, os, sys, time, urllib.request

cmd, arg = sys.argv[1], sys.argv[2]
host, sess, key = os.environ["WAHA_HOST"], os.environ["WAHA_SESSION"], os.environ["WAHA_API_KEY"]
fitos = os.environ["FITOS_URL"]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(host + path, data=data, method=method,
                                 headers={"X-Api-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def masked(cfg):
    cfg = json.loads(json.dumps(cfg))
    for h in cfg.get("webhooks") or []:
        if isinstance(h.get("hmac"), dict) and h["hmac"].get("key"):
            h["hmac"]["key"] = "***"
    return cfg


def clean(d):
    if isinstance(d, dict):
        return {k: clean(v) for k, v in d.items() if v is not None}
    if isinstance(d, list):
        return [clean(x) for x in d]
    return d


def wait_working():
    for _ in range(45):
        time.sleep(2)
        st = call("GET", f"/api/sessions/{sess}").get("status")
        if st == "WORKING":
            return st
    return st


info = call("GET", f"/api/sessions/{sess}")
cfg = clean(info.get("config") or {})

if cmd == "show":
    out = {"name": info.get("name"), "status": info.get("status"), "config": cfg if arg == "--raw" else masked(cfg)}
    print(json.dumps(out, indent=1))
    sys.exit(0)

if info.get("status") != "WORKING":
    sys.exit(f"the session is {info.get('status')}, not WORKING: not touching anything")

if cmd == "restore":
    new_cfg = clean(json.load(open(arg)).get("config") or {})
else:
    hooks = [h for h in cfg.get("webhooks") or [] if str(h.get("url", "")).rstrip("/") != fitos]
    if cmd == "add":
        hooks.append({"url": fitos, "events": ["message.any", "session.status"],
                      "hmac": {"key": os.environ["WAHA_WEBHOOK_HMAC_KEY"]}})
    new_cfg = dict(cfg, webhooks=hooks)

call("PUT", f"/api/sessions/{sess}", {"name": sess, "config": new_cfg})
st = wait_working()
after = call("GET", f"/api/sessions/{sess}")
print(json.dumps({"status": st, "config": masked(clean(after.get("config") or {}))}, indent=1))
if st != "WORKING":
    sys.exit("the session did not return to WORKING within 90 s: check WAHA's dashboard or run restore")
PY
