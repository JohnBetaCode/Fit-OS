#!/usr/bin/env bash
# Proves a server backup is usable: fetches it, decrypts it with the private key (only on your laptop), runs
# PRAGMA integrity_check and counts rows per table of every database inside (accounts + one per person; older
# backups are a single .sqlite). The decrypted copy lives in a temp dir and is deleted.
#
#   make backup-check                                   newest DB backup
#   make backup-check FILE=<data dir>/backups/manual/db-....tar.age
set -euo pipefail

REMOTE_HOST=${REMOTE_HOST:?set REMOTE_HOST (deploy/target.env)}
REMOTE_DATA=${REMOTE_DATA:-fit-os-data}
KEY=${AGE_KEY:?set AGE_KEY to your age private key (deploy/target.env)}
KEY=${KEY/#\~/$HOME}
FILE=${FILE:-}

[ -r "$KEY" ] || { echo "cannot read the backup private key at $KEY (AGE_KEY=...)" >&2; exit 1; }
command -v age >/dev/null || { echo "age is not installed" >&2; exit 1; }
[ -n "$FILE" ] || FILE=$(ssh -n "$REMOTE_HOST" "ls -1t $REMOTE_DATA/backups/*/db-*.age | head -n 1")
echo "backup: $FILE"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/x"
case "$FILE" in
*.tar.age) ssh -n "$REMOTE_HOST" "cat '$FILE'" | age -d -i "$KEY" | tar -C "$tmp/x" -xf - ;;
*) ssh -n "$REMOTE_HOST" "cat '$FILE'" | age -d -i "$KEY" -o "$tmp/x/check.sqlite" ;;
esac
python3 - "$tmp/x" <<'PY'
import pathlib, sqlite3, sys
root = pathlib.Path(sys.argv[1])
dbs = sorted(root.rglob("*.sqlite"))
assert dbs, "no databases in the backup"
for db in dbs:
    c = sqlite3.connect(db)
    print(f"── {db.relative_to(root)}: integrity {c.execute('PRAGMA integrity_check').fetchone()[0]}")
    tables = [t for (t,) in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    if "alembic_version" in tables:
        print("   alembic:", c.execute("select version_num from alembic_version").fetchone()[0])
    for t in tables:
        print(f"   {t}: {c.execute(f'select count(*) from \"{t}\"').fetchone()[0]}")
PY
echo "ok: the backup decrypts and reads"
