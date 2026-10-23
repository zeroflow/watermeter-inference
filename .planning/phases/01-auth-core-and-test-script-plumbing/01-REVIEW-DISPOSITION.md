---
phase: 01
review: 01-REVIEW.md
titles: json
findings:
  - id: WR-01
    severity: warning
    disposition: open
    title: "An invalid or unreadable password seed leaves the unauthenticated setup page open"
  - id: WR-02
    severity: warning
    disposition: open
    title: "`AuthStore.create` requires hard links, and the setup route turns any `OSError` into a bare 500"
  - id: WR-03
    severity: warning
    disposition: open
    title: "A form with more than 20 fields raises an unhandled `ValueError` (HTTP 500)"
  - id: WR-04
    severity: warning
    disposition: open
    title: "The failure throttle is keyed on the full IPv6 address, so any IPv6 client can bypass it"
  - id: IN-01
    severity: info
    disposition: open
    title: "The `_client_ip` docstring is inaccurate, and clients behind a proxy share one throttle key"
  - id: IN-02
    severity: info
    disposition: open
    title: "Logout does not revoke the session token"
  - id: IN-03
    severity: info
    disposition: open
    title: "A double-submitted login form can show a 429 for a login that succeeded"
  - id: IN-04
    severity: info
    disposition: open
    title: "`debug.sh` loads `.env` with `source`, which mangles passwords containing `$`, spaces or `#`"
  - id: IN-05
    severity: info
    disposition: open
    title: "The scripts exit without a message on some failures"
  - id: IN-06
    severity: info
    disposition: open
    title: "`change_password` reads the record before the awaited hash and does not re-check it"
open: 10
total: 10
recorded: 2026-10-23T20:11:27.937Z
---

# Phase 01: Code Review Disposition

| Finding | Severity | Disposition | Source |
|---------|----------|-------------|--------|
| WR-01 | warning | open | - |
| WR-02 | warning | open | - |
| WR-03 | warning | open | - |
| WR-04 | warning | open | - |
| IN-01 | info | open | - |
| IN-02 | info | open | - |
| IN-03 | info | open | - |
| IN-04 | info | open | - |
| IN-05 | info | open | - |
| IN-06 | info | open | - |

Dispositions: `open` (recorded, not yet triaged), `fixed`, `skipped`, `deferred`.
Set `deferred` by hand and put the reason in the Source cell; both are preserved. A `|` in the reason is kept as prose and escaped on the next run.
Re-running the gate keeps every row it can. A row the current review no longer reports is kept and its Source cell flagged, so a finding does not leave this record silently. ONE exception: when a finding id is REUSED by a different finding, the earlier decision cannot keep a row — the id is taken — and it is dropped. A RECORDED decision (anything but `open`) is named on the console when that happens; a row still at `open` is replaced silently, because `open` records no decision to lose.
