#!/usr/bin/env bash
# Smoke check for the login gate of a running instance: health, anonymous API refusal, login, authenticated API.
# Usage: scripts/auth_smoke.sh <base_url> <password>
# The instance must have been started with WATERMETER_ADMIN_PASSWORD set to <password>.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <base_url> <password>" >&2
  exit 2
fi

BASE="${1%/}"
PW="$2"
JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

# Wait up to 120 s (60 tries, 2 s apart) for the container to answer on /health.
ready=0
for i in $(seq 1 60); do
  if curl -sf -o /dev/null "$BASE/health"; then
    ready=1
    echo "PASS health: /health answered after ${i} tries"
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "FAIL health: $BASE/health never answered within 120 s" >&2
  exit 1
fi

# check <name> <expected status> <curl args...>
check() {
  local name="$1" expected="$2" code
  shift 2
  code=$(curl -s -o /dev/null -w '%{http_code}' "$@")
  if [ "$code" != "$expected" ]; then
    echo "FAIL $name: expected HTTP $expected, got $code" >&2
    exit 1
  fi
  echo "PASS $name: HTTP $code"
}

check "anonymous /api/status is refused" 401 "$BASE/api/status"
check "login with the seeded password" 303 -c "$JAR" -X POST --data-urlencode "password=$PW" "$BASE/login"
check "authenticated /api/status" 200 -b "$JAR" "$BASE/api/status"
# Last, so the failed-login throttle cannot affect the checks above.
check "wrong password is refused" 401 -X POST --data-urlencode "password=wrong-$PW-wrong" "$BASE/login"
echo "All auth smoke checks passed"
