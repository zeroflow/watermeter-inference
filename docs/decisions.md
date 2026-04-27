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

## 2026-04-27: Marker Confidence Threshold

**Status:** Provisional — review after 7 days of archive data.

**Decision:** Default `alignment.marker_confidence_threshold = 0.5` retained for back-compat.
Threshold is now configurable via `config.yaml`. Production should re-tune after running
`scripts/replay_marker_confidence.py` against `/data/raw_archive/` (enable
`alignment.archive_raw_images: true` to populate).

**Why 0.5 default:** Matches pre-existing hardcoded value at `image_pipeline.py:82`.
Conventional ranges for `cv2.TM_CCOEFF_NORMED` are 0.7-0.8 but actual baseline
in this deployment is unknown without sample data. Tightening blindly risks regressing
the happy path.

**Followup:** After 7 days of archive collection, run the replay script. If p5
> 0.7 across both markers, raise threshold to 0.7. If p5 < 0.6 on either marker,
investigate marker template quality before raising threshold (false-rejection risk).
