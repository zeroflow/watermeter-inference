# Architecture Decisions

Accepted trade-offs and deliberate non-fixes. Each entry records the context so future reviewers don't re-raise the same issue.

---

## AD-01: No authentication on API endpoints

**Status:** Accepted
**Date:** 2026-02-22
**Context:** The application runs on an embedded device in a trusted home LAN. All clients on the network are trusted. Adding auth would complicate the setup (credential management, token flow) without meaningful security benefit in this deployment model.
**Decision:** No authentication middleware. All endpoints remain open.
**Revisit if:** The device becomes accessible from untrusted networks (port forwarding, VPN, cloud deployment).

## AD-02: No SSRF protection on image-source URL fetch

**Status:** Accepted (depends on AD-01)
**Date:** 2026-02-22
**Context:** `POST /api/roi/image-source` fetches a user-provided URL server-side (intended for IP camera snapshots). In theory this allows the server to be used as a proxy to reach internal services. In practice: the device sits in a trusted LAN where any client can already reach the same hosts directly. There is no cloud metadata endpoint, no DMZ boundary, no localhost-only service that would be newly exposed.
**Decision:** No URL blocklist. Accept the SSRF surface as low-risk given the deployment model.
**Revisit if:** Authentication is added (AD-01) — at that point SSRF becomes a privilege boundary bypass and should be mitigated.
