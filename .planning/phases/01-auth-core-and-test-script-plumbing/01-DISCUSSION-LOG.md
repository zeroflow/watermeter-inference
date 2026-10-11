# Phase 1: Auth Core and Test/Script Plumbing - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md. This log preserves the alternatives considered.

**Date:** 2026-10-11
**Phase:** 01-auth-core-and-test-script-plumbing
**Areas discussed:** Account controls in the UI, Setup & login pages, Dev/test credentials, Instance identity & sessions

---

## Account controls in the UI

| Question | Options | Selected |
|----------|---------|----------|
| Where should Logout live? | Nav item near theme toggle / Account page only / Section on Config page | Nav item near theme toggle |
| Where should Change password live? | Small Account page / Section in Config editor / Modal from nav | Small Account page |
| After changing the password, the current session? | Stay logged in / Log out everywhere including me | Stay logged in |
| Show session info? | No, minimal / Show on Account page | No, minimal |
| How is the Account page reached? | Two items: Account + Logout / Account in nav, logout desktop only / Link from Config page | Two items: Account + Logout |

---

## Setup & login pages

| Question | Options | Selected |
|----------|---------|----------|
| Look | Themed, no nav / Bare standalone HTML | Themed, no nav |
| Upgrade copy | Explain why / Same text for fresh + upgrade | Explain why |
| Pipeline before setup | Keep running / Pause until setup | Keep running |
| Error detail | Generic + wait time + recovery hint / Generic only | Generic + wait time |
| Setup token | Extra field, token in log / Token in URL | Extra field, token in log |
| After setup | Auto-login to dashboard / Redirect to login | Auto-login |

---

## Dev/test credentials

| Question | Options | Selected |
|----------|---------|----------|
| debug.sh password | Seed from .env / Always setup page | Seed from .env |
| Integration tests vs existing instance | Password via env/option, skip if missing / Only self-started containers | Password via env/option |
| Ephemeral container password | Random per run / Fixed known test password | Random per run |
| debug_clean.sh first boot | Flag to choose (`--setup`) / Always setup page / Always seeded | Flag to choose |
| Unit test client | Logged in by default + anon_client / You decide | Logged in by default |

---

## Instance identity & sessions

| Question | Options | Selected |
|----------|---------|----------|
| Cookie name uniqueness | Random instance ID in auth.json / From MQTT client_id / Env var WATERMETER_INSTANCE | Random instance ID |
| Session lifetime | Fixed 30 days sliding / Configurable in config.yaml | Fixed 30 days |
| Remember me | No, always persistent / Yes, unchecked = browser session | No |
| Upgrade detection | You decide / Any /data state file | You decide |

---

## Claude's Discretion

- Upgrade-detection heuristic for the setup notice
- Env flag name for the setup token, copy text, nav icons, mobile nav layout

## Deferred Ideas

None.
