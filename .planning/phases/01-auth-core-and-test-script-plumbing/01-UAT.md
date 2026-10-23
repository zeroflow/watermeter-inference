---
status: testing
phase: 01-auth-core-and-test-script-plumbing
source: [01-VERIFICATION.md]
started: 2026-10-23T22:20:00+02:00
updated: 2026-10-23T22:20:00+02:00
---

## Current Test

number: 1
name: LAN login over plain HTTP on :8002
expected: |
  Login works, session cookie has no Secure flag and is kept, polling updates, no 403 in the console.
awaiting: user response

## Tests

### 1. LAN login over plain HTTP on :8002
Open http://<LAN IP of this host>:8002 from another device over plain HTTP. Log in, watch the dashboard poll for a minute, press Read Now.
expected: Login works, session cookie has no Secure flag and is kept, polling updates, no 403 in the console.
result: [pending]

### 2. Two instances' cookies side by side
Log in to :8002 and to a second instance (./debug_clean.sh --detach, :8003, password printed); log out of :8003.
expected: Two different wm_session_xxxxxxxx cookies exist and :8002 stays logged in.
result: [pending]

### 3. HTMX poll after session loss
On :8002 delete the wm_session_ cookie in dev tools and wait for the next 5 s HTMX poll.
expected: Browser is sent to /login?next=/ and returns to the dashboard after login; no login HTML appears inside a fragment.
result: [pending]

### 4. Browser restart and password change across two browsers
Close and reopen the browser on :8002 (still logged in). Change the password on /account in browser A while browser B is logged in.
expected: A stays logged in; B is sent to /login on its next poll.
result: [pending]

### 5. Themed setup page and TTY set-password prompt
Open http://localhost:8003/setup from ./debug_clean.sh --detach --setup in light and dark themes; run `docker exec -it -u watermeter watermeter-dashboard-debug python3 -m watermeter set-password` in a real terminal and log in to :8002 with the new password.
expected: Themed setup card without the nav rail in both themes; interactive TTY prompt asks for the password and a confirmation; login with the new password works. The debug container's current password is unknown, so this step is also how you get back into :8002.
result: [pending]

### 6. Nav on a real phone at 360 and 390 px (optional)
Check the nav at 360 and 390 px, light and dark.
expected: Account and Logout fit the bottom bar without overflow.
result: [pending]

### 7. AUTH-15 scope decision
The full integration run against :8002 is 29 passed / 4 failed: test_training_e2e, test_training_cancel, test_training_queue (timeouts on this CPU-only host, same failure on a pre-phase image) and test_benchmark_cancel (timing race, passed on reruns).
expected: Accept that the failures are pre-existing/flaky and independent of auth, or ask for the tests to be fixed or skipped on slow hosts before the phase is closed.
result: [pending]

### 8. CSRF statelessness and no auth-disable switch
Skim watermeter/auth_middleware.py `_csrf_refusal`.
expected: The CSRF verdict depends only on the request's own headers and cookie validity, and no auth-disable switch exists.
result: [pending]

## Summary

total: 8
passed: 0
issues: 0
pending: 8
skipped: 0
blocked: 0

## Gaps
