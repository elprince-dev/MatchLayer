#!/usr/bin/env bash
# 15.1 smoke harness — runs the documented flow against API + web app.
# API port and web port are configurable via env so we can side-step a
# Windows-host process that holds 127.0.0.1:8000 under WSL2 mirrored
# networking.

set -uo pipefail

API="${API:-http://127.0.0.1:8765}"
WEB="${WEB:-http://127.0.0.1:3000}"
COOKIES=/tmp/smoke_cookies.txt
COOKIES2=/tmp/smoke_cookies2.txt
: > "$COOKIES"
: > "$COOKIES2"

UUID="$(python3 -c 'import uuid; print(uuid.uuid4().hex[:12])')"
EMAIL="smoke-${UUID}@example.com"
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

PASS=0
FAIL=0
record() {
  local label="$1" ok="$2"
  if [ "$ok" = "1" ]; then
    PASS=$((PASS+1)); echo "  RESULT: PASS — $label"
  else
    FAIL=$((FAIL+1)); echo "  RESULT: FAIL — $label"
  fi
}

# -------- Web SSR sanity --------
step "Web /login SSR returns 200"
HTTP=$(curl -sS -o /tmp/web_login.html -w "%{http_code}" "$WEB/login")
echo "GET /login HTTP=$HTTP"
[ "$HTTP" = "200" ] && record "GET /login -> 200" 1 || record "GET /login -> $HTTP" 0

step "Web /register SSR returns 200"
HTTP=$(curl -sS -o /tmp/web_register.html -w "%{http_code}" "$WEB/register")
echo "GET /register HTTP=$HTTP"
[ "$HTTP" = "200" ] && record "GET /register -> 200" 1 || record "GET /register -> $HTTP" 0

# Authenticated_Shell server-component on / should redirect to /login when
# no refresh cookie is present (Requirement 12.4 / §13.7).
step "Web / (no auth) redirects to /login?next=..."
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$WEB/")
LOC=$(curl -sS -o /dev/null -D - "$WEB/" | awk 'BEGIN{IGNORECASE=1}/^location:/{print $2}' | tr -d '\r')
echo "GET / HTTP=$HTTP location=$LOC"
case "$HTTP" in
  307|308|302) case "$LOC" in
    */login*next=*) record "GET / -> redirect to /login?next=…" 1 ;;
    *) record "GET / redirected but to '$LOC'" 0 ;;
  esac ;;
  *) record "GET / -> $HTTP (expected 307/308/302)" 0 ;;
esac

# -------- 1) /register --------
step "1) POST /api/v1/auth/register"
HTTP=$(curl -sS -o /tmp/reg.json -w "%{http_code}" \
  -c "$COOKIES" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/register" \
  --data "{\"email\":\"$EMAIL\",\"password\":\"$PASS_ORIG\",\"display_name\":\"$DISPLAY\"}")
echo "register HTTP=$HTTP"; python3 -m json.tool < /tmp/reg.json 2>/dev/null || cat /tmp/reg.json
[ "$HTTP" = "201" ] || { record "register expected 201 got $HTTP" 0; exit 1; }
ACCESS=$(python3 -c "import json; print(json.load(open('/tmp/reg.json'))['access_token'])")
[ -n "$ACCESS" ] || { record "empty access_token" 0; exit 1; }
[ -n "$(extract_refresh_present "$COOKIES")" ] || { record "matchlayer_refresh cookie missing" 0; exit 1; }
CSRF=$(extract_csrf "$COOKIES")
[ -n "$CSRF" ] || { record "matchlayer_csrf cookie missing" 0; exit 1; }
record "register 201 + refresh+csrf cookies + access_token" 1
echo "(web app would redirect to /)"

# -------- 2) GET /me (assert user name appears) --------
step "2) GET /api/v1/auth/me after register"
HTTP=$(curl -sS -o /tmp/me1.json -w "%{http_code}" \
  -H "Authorization: Bearer $ACCESS" \
  "$API/api/v1/auth/me")
echo "me HTTP=$HTTP"; python3 -m json.tool < /tmp/me1.json
[ "$HTTP" = "200" ] || { record "/me expected 200 got $HTTP" 0; exit 1; }
NAME=$(python3 -c "import json; print(json.load(open('/tmp/me1.json')).get('display_name',''))")
[ "$NAME" = "$DISPLAY" ] && record "/me display_name == '$DISPLAY'" 1 || record "display_name mismatch ($NAME)" 0

# -------- 3) Sign out --------
step "3) POST /api/v1/auth/logout"
HTTP=$(curl -sS -o /tmp/logout.json -w "%{http_code}" \
  -b "$COOKIES" -c "$COOKIES" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST "$API/api/v1/auth/logout")
echo "logout HTTP=$HTTP"
HEADERS=$(curl -sS -D - -o /dev/null \
  -b "$COOKIES" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST "$API/api/v1/auth/logout")
[ "$HTTP" = "204" ] && record "logout 204" 1 || record "logout expected 204 got $HTTP" 0

# -------- 4) /login --------
step "4) POST /api/v1/auth/login"
: > "$COOKIES"
HTTP=$(curl -sS -o /tmp/login.json -w "%{http_code}" \
  -c "$COOKIES" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/login" \
  --data "{\"email\":\"$EMAIL\",\"password\":\"$PASS_ORIG\"}")
echo "login HTTP=$HTTP"; python3 -m json.tool < /tmp/login.json
[ "$HTTP" = "200" ] || { record "login expected 200 got $HTTP" 0; exit 1; }
ACCESS=$(python3 -c "import json; print(json.load(open('/tmp/login.json'))['access_token'])")
[ -n "$ACCESS" ] || { record "empty access_token after login" 0; exit 1; }
CSRF=$(extract_csrf "$COOKIES")
[ -n "$CSRF" ] || { record "post-login csrf cookie missing" 0; exit 1; }
record "login 200 + tokens" 1

# -------- 5) Silent refresh path --------
step "5) Silent refresh-and-retry"
HTTP=$(curl -sS -o /tmp/me_unauth.json -w "%{http_code}" "$API/api/v1/auth/me")
echo "me (no bearer) HTTP=$HTTP"
[ "$HTTP" = "401" ] && record "/me without bearer -> 401" 1 || record "/me without bearer expected 401 got $HTTP" 0

HTTP=$(curl -sS -o /tmp/refresh.json -w "%{http_code}" \
  -b "$COOKIES" -c "$COOKIES" \
  -H "X-CSRF-Token: $CSRF" \
  -X POST "$API/api/v1/auth/refresh")
echo "refresh HTTP=$HTTP"; python3 -m json.tool < /tmp/refresh.json
[ "$HTTP" = "200" ] || { record "refresh expected 200 got $HTTP" 0; exit 1; }
NEW_ACCESS=$(python3 -c "import json; print(json.load(open('/tmp/refresh.json'))['access_token'])")
[ -n "$NEW_ACCESS" ] || { record "refresh returned no access_token" 0; exit 1; }
[ "$NEW_ACCESS" != "$ACCESS" ] && record "refresh issued a NEW access_token" 1 || record "refresh returned the same access_token" 0
NEW_CSRF=$(extract_csrf "$COOKIES")

HTTP=$(curl -sS -o /tmp/me_retry.json -w "%{http_code}" \
  -H "Authorization: Bearer $NEW_ACCESS" \
  "$API/api/v1/auth/me")
echo "me retry HTTP=$HTTP"
[ "$HTTP" = "200" ] && record "post-refresh /me 200" 1 || record "post-refresh /me expected 200 got $HTTP" 0
ACCESS="$NEW_ACCESS"; CSRF="$NEW_CSRF"

# -------- 6) /forgot-password --------
step "6) POST /api/v1/auth/password-reset/request"
HTTP=$(curl -sS -o /tmp/reset_req.json -w "%{http_code}" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/password-reset/request" \
  --data "{\"email\":\"$EMAIL\"}")
echo "reset request HTTP=$HTTP"; cat /tmp/reset_req.json
[ "$HTTP" = "202" ] && record "password-reset/request 202" 1 || record "reset request expected 202 got $HTTP" 0

# -------- 7) Dev reset-link surface --------
step "7) GET /api/v1/dev/last-reset-link"
HTTP=$(curl -sS -o /tmp/link.json -w "%{http_code}" "$API/api/v1/dev/last-reset-link")
echo "link HTTP=$HTTP"; python3 -m json.tool < /tmp/link.json
[ "$HTTP" = "200" ] || { record "dev last-reset-link expected 200 got $HTTP" 0; exit 1; }
RESET_LINK=$(python3 -c "import json; print(json.load(open('/tmp/link.json')).get('link') or '')")
CREATED=$(python3 -c "import json; print(json.load(open('/tmp/link.json')).get('created_at') or '')")
[ -n "$RESET_LINK" ] && [ -n "$CREATED" ] && record "dev/last-reset-link populated (link, created_at)" 1 || record "dev/last-reset-link missing fields" 0
TOKEN=$(python3 -c "from urllib.parse import urlparse, parse_qs; print(parse_qs(urlparse('$RESET_LINK').query).get('token',[''])[0])")
[ -n "$TOKEN" ] || { record "no token query param in reset link" 0; exit 1; }

# -------- 8) Submit new password --------
step "8) POST /api/v1/auth/password-reset/confirm"
HTTP=$(curl -sS -o /tmp/reset_conf.json -w "%{http_code}" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/password-reset/confirm" \
  --data "{\"token\":\"$TOKEN\",\"new_password\":\"$PASS_NEW\"}")
echo "reset confirm HTTP=$HTTP"; cat /tmp/reset_conf.json
[ "$HTTP" = "204" ] && record "password-reset/confirm 204" 1 || record "reset confirm expected 204 got $HTTP" 0
echo "(web app would redirect to /login?just-reset=1)"

# Confirm /login?just-reset=1 still serves a 200 page
HTTP=$(curl -sS -o /dev/null -w "%{http_code}" "$WEB/login?just-reset=1")
[ "$HTTP" = "200" ] && record "/login?just-reset=1 SSR 200" 1 || record "/login?just-reset=1 SSR $HTTP" 0

# -------- 9) Sign in with new password --------
step "9) POST /api/v1/auth/login (new password)"
: > "$COOKIES2"
HTTP=$(curl -sS -o /tmp/login2.json -w "%{http_code}" \
  -c "$COOKIES2" \
  -H "Content-Type: application/json" \
  -X POST "$API/api/v1/auth/login" \
  --data "{\"email\":\"$EMAIL\",\"password\":\"$PASS_NEW\"}")
echo "login (new pw) HTTP=$HTTP"; python3 -m json.tool < /tmp/login2.json
[ "$HTTP" = "200" ] || { record "login w/ new pw expected 200 got $HTTP" 0; exit 1; }
ACCESS2=$(python3 -c "import json; print(json.load(open('/tmp/login2.json'))['access_token'])")
record "login with new password 200" 1

# -------- 10) /me works again --------
step "10) GET /api/v1/auth/me after the reset"
HTTP=$(curl -sS -o /tmp/me_final.json -w "%{http_code}" \
  -H "Authorization: Bearer $ACCESS2" \
  "$API/api/v1/auth/me")
echo "me HTTP=$HTTP"; python3 -m json.tool < /tmp/me_final.json
[ "$HTTP" = "200" ] && record "post-reset /me 200" 1 || record "post-reset /me expected 200 got $HTTP" 0

# Export the email for the audit-event verification step
echo
echo "SUMMARY: pass=$PASS fail=$FAIL"
echo "EMAIL=$EMAIL" > /tmp/smoke_email.env
[ "$FAIL" = "0" ] || exit 1
