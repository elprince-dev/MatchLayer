#!/usr/bin/env bash
# E2E smoke against a fresh local stack — phase-1-auth task 15.1.
# Uses curl as a stand-in for the browser to walk every documented flow.

set -uo pipefail

API="http://127.0.0.1:8000"
COOKIES=/tmp/smoke_cookies.txt
COOKIES2=/tmp/smoke_cookies2.txt
LOG=/tmp/smoke.log
: > "$COOKIES"
: > "$COOKIES2"
: > "$LOG"

EMAIL="smoke+$(date +%s)@example.com"
PASS_ORIG="OriginalSmokePassword!2024"
PASS_NEW="ResetSmokePassword!2024"
DISPLAY="Smoke Tester"

step() { echo; echo "==================== $* ===================="; }

extract_csrf() {
  awk '$6=="matchlayer_csrf"{print $7}' "$1"
}
extract_refresh_present() {
  awk '$6=="matchlayer_refresh"{print "yes"; exit}' "$1"
}

# -------- 1) /register --------
step "1) POST /api/v1/auth/register"
HTTP=$(curl -sS -o /tmp/reg.json -w "%{http_code}" \
  -c "$COOKIES" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/register" \
  --data "{\"email\":\"$EMAIL\",\"password\":\"$PASS_ORIG\",\"display_name\":\"$DISPLAY\"}")
echo "register HTTP=$HTTP"; python3 -m json.tool < /tmp/reg.json 2>/dev/null || cat /tmp/reg.json
[ "$HTTP" = "201" ] || { echo "FAIL: register expected 201"; exit 1; }
ACCESS=$(python3 -c "import json; print(json.load(open('/tmp/reg.json'))['access_token'])")
[ -n "$ACCESS" ] || { echo "FAIL: empty access_token"; exit 1; }
[ -n "$(extract_refresh_present "$COOKIES")" ] || { echo "FAIL: matchlayer_refresh cookie missing"; exit 1; }
CSRF=$(extract_csrf "$COOKIES")
[ -n "$CSRF" ] || { echo "FAIL: matchlayer_csrf cookie missing"; exit 1; }
echo "Got access_token (length=${#ACCESS}); web app would redirect to /."

# -------- 2) GET /me (assert user name appears) --------
step "2) GET /api/v1/auth/me after register"
HTTP=$(curl -sS -o /tmp/me1.json -w "%{http_code}" \
  -H "Authorization: Bearer $ACCESS" \
  "$API/api/v1/auth/me")
echo "me HTTP=$HTTP"; python3 -m json.tool < /tmp/me1.json
[ "$HTTP" = "200" ] || { echo "FAIL: /me expected 200"; exit 1; }
NAME=$(python3 -c "import json; print(json.load(open('/tmp/me1.json')).get('display_name',''))")
[ "$NAME" = "$DISPLAY" ] || { echo "FAIL: display_name mismatch"; exit 1; }

# -------- 3) Sign out --------
step "3) POST /api/v1/auth/logout"
HTTP=$(curl -sS -o /tmp/logout.json -w "%{http_code}" \
  -b "$COOKIES" -c "$COOKIES" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST "$API/api/v1/auth/logout")
echo "logout HTTP=$HTTP"
[ "$HTTP" = "204" ] || { echo "FAIL: logout expected 204"; exit 1; }

# -------- 4) /login --------
step "4) POST /api/v1/auth/login"
: > "$COOKIES"
HTTP=$(curl -sS -o /tmp/login.json -w "%{http_code}" \
  -c "$COOKIES" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/login" \
  --data "{\"email\":\"$EMAIL\",\"password\":\"$PASS_ORIG\"}")
echo "login HTTP=$HTTP"; python3 -m json.tool < /tmp/login.json
[ "$HTTP" = "200" ] || { echo "FAIL: login expected 200"; exit 1; }
ACCESS=$(python3 -c "import json; print(json.load(open('/tmp/login.json'))['access_token'])")
[ -n "$ACCESS" ] || { echo "FAIL: empty access_token after login"; exit 1; }
CSRF=$(extract_csrf "$COOKIES")
[ -n "$CSRF" ] || { echo "FAIL: post-login csrf cookie missing"; exit 1; }
echo "(web app would redirect to /)"

# -------- 5) Silent refresh path --------
step "5) Silent refresh-and-retry (clearing access token, hitting /me, calling /refresh)"
HTTP=$(curl -sS -o /tmp/me_unauth.json -w "%{http_code}" "$API/api/v1/auth/me")
echo "me (no bearer) HTTP=$HTTP"
[ "$HTTP" = "401" ] || { echo "FAIL: /me without bearer expected 401"; exit 1; }

HTTP=$(curl -sS -o /tmp/refresh.json -w "%{http_code}" \
  -b "$COOKIES" -c "$COOKIES" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST "$API/api/v1/auth/refresh")
echo "refresh HTTP=$HTTP"; python3 -m json.tool < /tmp/refresh.json
[ "$HTTP" = "200" ] || { echo "FAIL: refresh expected 200"; exit 1; }
NEW_ACCESS=$(python3 -c "import json; print(json.load(open('/tmp/refresh.json'))['access_token'])")
[ -n "$NEW_ACCESS" ] || { echo "FAIL: refresh did not return access_token"; exit 1; }
[ "$NEW_ACCESS" != "$ACCESS" ] || { echo "FAIL: refreshed token equals previous"; exit 1; }
NEW_CSRF=$(extract_csrf "$COOKIES")

HTTP=$(curl -sS -o /tmp/me_retry.json -w "%{http_code}" \
  -H "Authorization: Bearer $NEW_ACCESS" \
  "$API/api/v1/auth/me")
echo "me retry HTTP=$HTTP"
[ "$HTTP" = "200" ] || { echo "FAIL: retried /me expected 200"; exit 1; }
echo "Silent refresh-and-retry succeeded; user remained signed in."
ACCESS="$NEW_ACCESS"; CSRF="$NEW_CSRF"

# -------- 6) /forgot-password --------
step "6) POST /api/v1/auth/password-reset/request"
HTTP=$(curl -sS -o /tmp/reset_req.json -w "%{http_code}" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/password-reset/request" \
  --data "{\"email\":\"$EMAIL\"}")
echo "reset request HTTP=$HTTP"; cat /tmp/reset_req.json
[ "$HTTP" = "202" ] || { echo "FAIL: reset request expected 202"; exit 1; }

# -------- 7) Dev reset-link surface --------
step "7) GET /api/v1/dev/last-reset-link"
HTTP=$(curl -sS -o /tmp/link.json -w "%{http_code}" "$API/api/v1/dev/last-reset-link")
echo "link HTTP=$HTTP"; python3 -m json.tool < /tmp/link.json
[ "$HTTP" = "200" ] || { echo "FAIL: dev last-reset-link expected 200"; exit 1; }
RESET_LINK=$(python3 -c "import json; print(json.load(open('/tmp/link.json')).get('link') or '')")
[ -n "$RESET_LINK" ] || { echo "FAIL: empty reset link"; exit 1; }
TOKEN=$(python3 -c "from urllib.parse import urlparse, parse_qs; print(parse_qs(urlparse('$RESET_LINK').query).get('token',[''])[0])")
[ -n "$TOKEN" ] || { echo "FAIL: reset link has no token query param"; exit 1; }
case "$RESET_LINK" in
  *"/reset-password?token="*) echo "link path matches /reset-password?token=...";;
  *) echo "FAIL: link path doesn't look like /reset-password?token=…"; exit 1;;
esac

# -------- 8) Submit new password --------
step "8) POST /api/v1/auth/password-reset/confirm"
HTTP=$(curl -sS -o /tmp/reset_conf.json -w "%{http_code}" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/password-reset/confirm" \
  --data "{\"token\":\"$TOKEN\",\"new_password\":\"$PASS_NEW\"}")
echo "reset confirm HTTP=$HTTP"; cat /tmp/reset_conf.json
[ "$HTTP" = "204" ] || { echo "FAIL: reset confirm expected 204"; exit 1; }
echo "(web app would redirect to /login?just-reset=1)"

# -------- 9) Sign in with new password --------
step "9) POST /api/v1/auth/login (new password)"
: > "$COOKIES2"
HTTP=$(curl -sS -o /tmp/login2.json -w "%{http_code}" \
  -c "$COOKIES2" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/login" \
  --data "{\"email\":\"$EMAIL\",\"password\":\"$PASS_NEW\"}")
echo "login (new pw) HTTP=$HTTP"; python3 -m json.tool < /tmp/login2.json
[ "$HTTP" = "200" ] || { echo "FAIL: login with new password expected 200"; exit 1; }
ACCESS2=$(python3 -c "import json; print(json.load(open('/tmp/login2.json'))['access_token'])")

# -------- 10) /me works again --------
step "10) GET /api/v1/auth/me after the reset"
HTTP=$(curl -sS -o /tmp/me_final.json -w "%{http_code}" \
  -H "Authorization: Bearer $ACCESS2" \
  "$API/api/v1/auth/me")
echo "me HTTP=$HTTP"; python3 -m json.tool < /tmp/me_final.json
[ "$HTTP" = "200" ] || { echo "FAIL: post-reset /me expected 200"; exit 1; }

# -------- 11) PATCH /me (drive display_name_changed audit row) --------
step "11) PATCH /api/v1/auth/me to drive a display_name_changed audit row"
HTTP=$(curl -sS -o /tmp/patch_me.json -w "%{http_code}" \
  -H "Authorization: Bearer $ACCESS2" \
  -H "Content-Type: application/json" \
  -X PATCH "$API/api/v1/auth/me" \
  --data "{\"display_name\":\"Smoke Tester Renamed\"}")
echo "patch me HTTP=$HTTP"; python3 -m json.tool < /tmp/patch_me.json
[ "$HTTP" = "200" ] || { echo "FAIL: patch /me expected 200"; exit 1; }

echo
echo "ALL SMOKE STEPS PASSED."
