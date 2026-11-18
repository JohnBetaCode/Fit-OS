#!/usr/bin/env bash
# Fit-OS operations ON THE SERVER: encrypted backups, deploy and rollback (SQLite).
#
#   bash ops/server.sh backup [nightly|manual]   online copy of every SQLite database (accounts + one per
#                                                 person) in one tar, encrypted with age (Sundays also photos)
#   bash ops/server.sh deploy [ref]              backup, checkout ref (origin/main), build, migrate, start, health
#   bash ops/server.sh rollback                  back to the commit before the last deploy (and its DB if the schema changed)
#   bash ops/server.sh status                    commit, revision, containers, memory, last deploys and backups
#   bash ops/server.sh install-cron              nightly backup (BACKUP_CRON in .env, default 03:20 server time)
#
# From your laptop: make status | backup | deploy | rollback (they run the origin/main version of this file
# over ssh; see deploy/target.env.example). On the server itself you can run it directly.
# Backups are encrypted to the age recipients in BACKUP_RECIPIENTS_FILE (.env); the private key never lives
# on the server.
# Layout: $DATA_DIR/accounts.sqlite + $DATA_DIR/users/<name>/fitos.sqlite. The first deploy of that layout on a
# single-person install adopts $DATA_DIR/db/fitos.sqlite (app stopped, copy checked row by row, old file
# renamed): ADOPT_AS=<username> picks the admin's username (default: the one in caddy/users.caddy). Everything runs inside main(): bash reads the whole script before `git checkout` changes it.

set -euo pipefail

main() {
	cd "$(git rev-parse --show-toplevel)"
	DATA_DIR=$(env_get DATA_DIR)
	DATA_DIR=${DATA_DIR:-./data}
	LEGACY_DB="$DATA_DIR/db/fitos.sqlite"  # single-person layout, before accounts
	ACCOUNTS="$DATA_DIR/accounts.sqlite"
	BACKUPS="$DATA_DIR/backups"
	STATE="$DATA_DIR/deploy"
	APP_PORT=$(env_get APP_PORT)
	APP_PORT=${APP_PORT:-8090}

	local cmd=${1:-status}
	shift || true
	if [ "$cmd" != status ]; then
		mkdir -p "$STATE"
		exec 9>"$STATE/.lock"
		flock -n 9 || die "another operation (deploy, rollback or backup) is running"
	fi
	case "$cmd" in
	backup) backup "${1:-manual}" ;;
	deploy) deploy "${1:-origin/main}" ;;
	rollback) rollback ;;
	status) status ;;
	install-cron) install_cron ;;
	*) die "usage: bash ops/server.sh backup [nightly|manual] | deploy [ref] | rollback | status | install-cron" ;;
	esac
}

say() { printf '[fit-os %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() {
	say "ERROR: $*" >&2
	exit 1
}

env_get() { # value of a .env variable, without evaluating it
	[ -f .env ] || return 0
	sed -n "s/^$1=//p" .env | tail -n1 | tr -d "\"'"
}

age_bin() {
	command -v age 2>/dev/null && return 0
	[ -x "$HOME/.local/bin/age" ] && echo "$HOME/.local/bin/age" && return 0
	die "age not found (https://github.com/FiloSottile/age/releases) in PATH or ~/.local/bin"
}

recipients() { # age recipients file (public keys), kept outside the repo
	local f
	f=$(env_get BACKUP_RECIPIENTS_FILE)
	f=${f:-$DATA_DIR/deploy/backup-recipients.txt}
	f=${f/#\~/$HOME}
	[ -s "$f" ] || die "no age recipients at $f (set BACKUP_RECIPIENTS_FILE in .env; see docs/deployment.md#backups)"
	echo "$f"
}

databases() { # every SQLite database, relative to $DATA_DIR
	local f
	[ -f "$LEGACY_DB" ] && echo "db/fitos.sqlite"
	[ -f "$ACCOUNTS" ] && echo "accounts.sqlite"
	for f in "$DATA_DIR"/users/*/fitos.sqlite; do
		[ -f "$f" ] && echo "${f#"$DATA_DIR"/}"
	done
	return 0
}

db_rev() { # Alembic revision of the person databases (they are migrated together; empty if none)
	local f
	f=$(databases | grep -v '^accounts.sqlite$' | head -n1)
	[ -n "$f" ] || return 0
	python3 -c "import sqlite3,sys
try:
    print(sqlite3.connect('file:$DATA_DIR/$f?mode=ro', uri=True).execute('select version_num from alembic_version').fetchone()[0])
except Exception:
    pass" 2>/dev/null || true
}

running() { [ -n "$(docker compose ps -q --status running fitos 2>/dev/null)" ]; }

snapshot() { # consistent copy of every database into directory $1, same relative paths (app running or not)
	local out=$1 f
	rm -rf "$out"
	mkdir -p "$out"
	chmod 700 "$out"
	for f in $(databases); do
		mkdir -p "$out/$(dirname "$f")"
		python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" \
			"$DATA_DIR/$f" "$out/$f"
		chmod 600 "$out/$f"
	done
}

prune() { # keep in $1 the $3 newest files matching pattern $2
	# shellcheck disable=SC2086
	ls -1t "$1"/$2 2>/dev/null | tail -n +$(($3 + 1)) | xargs -r rm -f --
}

backup() { # leaves the path in BACKUP_FILE (empty if there is no DB yet)
	local kind=$1 age rcp ts out tmp
	case "$kind" in nightly | predeploy | prerollback | manual) ;; *) die "unknown backup kind: $kind" ;; esac
	BACKUP_FILE=""
	if [ -z "$(databases)" ]; then
		say "backup $kind: no database yet; nothing to back up"
		return 0
	fi
	age=$(age_bin)
	rcp=$(recipients)
	umask 077
	mkdir -p "$BACKUPS/$kind"
	ts=$(date -u +%Y%m%dT%H%M%SZ)
	tmp="$STATE/backup-$ts"
	snapshot "$tmp"
	out="$BACKUPS/$kind/db-$ts-$(git rev-parse --short HEAD)-$(db_rev).tar.age"
	tar -C "$tmp" -cf - . | "$age" -R "$rcp" -o "$out"
	if [ "$kind" = predeploy ]; then
		rm -rf "$STATE/predeploy" "$STATE/predeploy.sqlite"
		mv "$tmp" "$STATE/predeploy"
	else
		rm -rf "$tmp"
	fi
	if [ "$kind" = nightly ]; then
		if [ "$(date -u +%u)" = 7 ]; then
			mkdir -p "$BACKUPS/weekly"
			cp "$out" "$BACKUPS/weekly/"
			prune "$BACKUPS/weekly" 'db-*' 4
			local photos
			photos=$(cd "$DATA_DIR" && ls -d users/*/photos 2>/dev/null || true)
			if [ -n "$photos" ]; then  # progress photos: weekly, they also live in the chat
				# shellcheck disable=SC2086
				tar -C "$DATA_DIR" -cf - $photos | "$age" -R "$rcp" -o "$BACKUPS/weekly/photos-$ts.tar.age"
				prune "$BACKUPS/weekly" 'photos-*' 4
			fi
		fi
		prune "$BACKUPS/nightly" 'db-*' 7
	else
		prune "$BACKUPS/$kind" 'db-*' 5
	fi
	BACKUP_FILE=$out
	say "backup $kind: $out ($(du -h "$out" | cut -f1); $(databases | wc -l) databases)"
}

healthy() { # /healthz answers (up to 2 min: startup on small ARM boards is slow)
	local _
	for _ in $(seq 1 24); do
		curl -fsS -o /dev/null "http://127.0.0.1:$APP_PORT/healthz" && return 0
		sleep 5
	done
	return 1
}

record() {
	printf '%s\t%s\t%s→%s\t%s→%s\t%s\n' "$(date -u +%FT%TZ)" "$1" "${2:0:7}" "${3:0:7}" "$4" "$5" "$6" >>"$STATE/history.tsv"
}

build_up() {
	export GIT_SHA GIT_BRANCH
	GIT_SHA=$(git rev-parse HEAD)
	# the server checks out a detached commit: name the remote branch whose tip it is (empty if none)
	GIT_BRANCH=$(git for-each-ref --points-at HEAD --format='%(refname:lstrip=3)' refs/remotes | grep -vx HEAD | head -n1 || true)
	docker compose build fitos
	if [ -f "$LEGACY_DB" ] && [ ! -f "$ACCOUNTS" ]; then
		say "single-person database found: adopting it as the first admin's (app stopped while copying)"
		docker compose stop fitos
		docker compose run --rm fitos fitos users adopt-legacy ${ADOPT_AS:+--as "$ADOPT_AS"} </dev/null
	fi
	docker compose run --rm fitos fitos migrate </dev/null
	docker compose up -d   # fitos + whatever COMPOSE_PROFILES enables in .env (edge, waha)
	reload_caddy
	export_caddy_root
}

reload_caddy() { # the Caddyfile is a single-file bind mount: a git checkout replaces the file (new inode) and
	# the container keeps seeing the old one, so neither `up -d` nor `caddy reload` picks it up. Restart if it differs.
	docker compose ps --status running caddy 2>/dev/null | grep -q caddy || return 0
	docker compose exec -T caddy cat /etc/caddy/Caddyfile </dev/null 2>/dev/null | cmp -s - ops/caddy/Caddyfile \
		|| docker compose restart caddy
}

export_caddy_root() { # Caddy's internal CA root, so phones can trust the dashboard (Settings → On your phone)
	docker compose ps --status running caddy 2>/dev/null | grep -q caddy || return 0
	mkdir -p "$DATA_DIR/caddy"
	for _ in 1 2 3 4 5; do
		docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$DATA_DIR/caddy/root.crt" \
			>/dev/null 2>&1 && return 0
		sleep 2
	done
	echo "note: Caddy's root certificate isn't there yet (it's created on the first HTTPS request)"
}

deploy() {
	local ref=$1 from to from_rev to_rev
	git fetch -q origin
	from=$(git rev-parse HEAD)
	to=$(git rev-parse "$ref^{commit}")
	if [ "$from" = "$to" ] && [ -z "${FORCE:-}" ]; then
		say "already at ${to:0:7}; nothing to deploy (FORCE=1 rebuilds anyway)"
		return 0
	fi
	git diff --quiet HEAD -- || die "the server checkout has uncommitted changes (git status)"
	local net
	net=$(env_get WA_NETWORK)
	net=${net:-wa-shared}
	docker network inspect "$net" >/dev/null 2>&1 || { say "creating docker network $net"; docker network create "$net" >/dev/null; }
	from_rev=$(db_rev)
	backup predeploy
	printf 'PREV_SHA=%s\nPREV_REV=%s\nTARGET_SHA=%s\nBACKUP=%s\nAT=%s\n' \
		"$from" "$from_rev" "$to" "$BACKUP_FILE" "$(date -u +%FT%TZ)" >"$STATE/last-deploy.env"
	record deploy-start "$from" "$to" "$from_rev" "?" "-"
	say "deploying ${from:0:7} → ${to:0:7} (if anything fails: make rollback)"
	git checkout -q --detach "$to"
	build_up
	to_rev=$(db_rev)
	if healthy; then
		record deploy "$from" "$to" "$from_rev" "$to_rev" ok
		say "ok: ${to:0:7} $(git log -1 --format=%s), Alembic ${from_rev:-∅} → $to_rev"
	else
		record deploy "$from" "$to" "$from_rev" "$to_rev" unhealthy
		die "/healthz does not answer: check docker logs fit-os and decide on make rollback"
	fi
}

rollback() {
	[ -f "$STATE/last-deploy.env" ] || die "no deploy to roll back ($STATE/last-deploy.env)"
	local PREV_SHA PREV_REV TARGET_SHA now cur_rev ts
	PREV_SHA=$(sed -n 's/^PREV_SHA=//p' "$STATE/last-deploy.env")
	PREV_REV=$(sed -n 's/^PREV_REV=//p' "$STATE/last-deploy.env")
	TARGET_SHA=$(sed -n 's/^TARGET_SHA=//p' "$STATE/last-deploy.env")
	now=$(git rev-parse HEAD)
	if [ "$now" != "$TARGET_SHA" ] && [ -z "${FORCE:-}" ]; then
		die "the server is at ${now:0:7}, not ${TARGET_SHA:0:7} (the last deploy); FORCE=1 to continue"
	fi
	cur_rev=$(db_rev)
	backup prerollback
	record rollback-start "$now" "$PREV_SHA" "$cur_rev" "$PREV_REV" "-"
	docker compose stop fitos
	if [ -d "$STATE/predeploy" ] && { [ "$cur_rev" != "$PREV_REV" ] || [ "$(layout_of "$STATE/predeploy")" != "$(layout_of "$DATA_DIR")" ]; }; then
		# SQLite downgrades are fragile: restore the pre-deploy copies (the minutes since the deploy are lost).
		ts=$(date -u +%Y%m%dT%H%M%SZ)
		restore_predeploy "$STATE/rolledback-$ts"
	fi
	say "rolling code back ${now:0:7} → ${PREV_SHA:0:7}"
	git checkout -q --detach "$PREV_SHA"
	build_up
	if healthy; then
		record rollback "$now" "$PREV_SHA" "$cur_rev" "$(db_rev)" ok
		mv "$STATE/last-deploy.env" "$STATE/last-deploy.rolled-back.env"
		say "ok: rollback a ${PREV_SHA:0:7} $(git log -1 --format=%s)"
	else
		record rollback "$now" "$PREV_SHA" "$cur_rev" "$(db_rev)" unhealthy
		die "after the rollback /healthz does not answer: check docker logs fit-os"
	fi
}

layout_of() { # "legacy" (one database) or "accounts" (one per person), for a data dir or a snapshot
	if [ -f "$1/db/fitos.sqlite" ]; then echo legacy; else echo accounts; fi
}

restore_predeploy() { # put the pre-deploy databases back; the current ones go to $1 (kept, never deleted)
	local keep=$1 f
	mkdir -p "$keep"
	chmod 700 "$keep"
	say "restoring the pre-deploy databases (the current ones are kept in ${keep#"$DATA_DIR"/})"
	for f in $(databases); do
		mkdir -p "$keep/$(dirname "$f")"
		mv "$DATA_DIR/$f" "$keep/$f"
		rm -f "$DATA_DIR/$f-wal" "$DATA_DIR/$f-shm"
	done
	(cd "$STATE/predeploy" && find . -name '*.sqlite' -type f) | while read -r f; do
		f=${f#./}
		mkdir -p "$DATA_DIR/$(dirname "$f")"
		cp -p "$STATE/predeploy/$f" "$DATA_DIR/$f"
	done
}

status() {
	say "code: $(git log -1 --format='%h %s (%cr)')"
	say "Alembic: $(db_rev) · databases: $(databases | tr '\n' ' ')"
	docker compose ps --format 'table {{.Service}}\t{{.Status}}'
	docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' fit-os 2>/dev/null || true
	echo "── last deploys"
	tail -n 6 "$STATE/history.tsv" 2>/dev/null || echo "(none)"
	echo "── last backups"
	# shellcheck disable=SC2012
	ls -1t "$BACKUPS"/*/db-*.age 2>/dev/null | head -n 5 || true
}

install_cron() {
	local line when
	when=$(env_get BACKUP_CRON)
	when=${when:-20 3 * * *}
	line="$when cd $(pwd) && bash ops/server.sh backup nightly >> $BACKUPS/backup.log 2>&1 # fit-os-backup"
	{
		crontab -l 2>/dev/null | grep -v '# fit-os-backup$' || true
		echo "$line"
	} | crontab -
	say "cron: $line"
}

main "$@"
exit
