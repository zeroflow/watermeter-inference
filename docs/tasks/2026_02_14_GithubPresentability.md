# GitHub Presentability Audit

**Date:** 2026-02-14
**Goal:** Simulate a homelab user finding this repo on GitHub for the first time. Identify everything that makes the repo confusing, broken, or unpresentable.
**Done criteria:** Actionable list of issues, prioritized by severity.

---

## The Homelab User's Experience (Walkthrough)

### Step 1: I land on the repo

I see `README.md` — it's entirely in German. As an English-speaking homelab user, I'm already reaching for Google Translate. But wait, `QUICKSTART.md` is also German, `DOCKER.md` is English. Mixed languages across docs.

**No LICENSE file.** I can't tell if I'm allowed to use this, fork it, or contribute to it.

### Step 2: I try to understand what this does

The README describes a water meter AI inference service that:
- Connects to an "AI-on-the-edge" device via HTTP
- Uses OpenVINO to classify digit/arrow images
- Publishes readings to Home Assistant via MQTT

Good concept! But the README is a **design document**, not a user README. It describes architecture, formulas, Node-Red migration — stuff a *developer* wrote for themselves, not what a *user* needs.

### Step 3: I try `docker-compose up`

```bash
git clone <repo>
cd watermeter
docker-compose up -d
```

**Problem 1: Submodules fail.** `.gitmodules` points to:
```
ssh://gitea@<gitea-host>:2222/zeroflow/watermeter-arrows.git
ssh://gitea@<gitea-host>:2222/zeroflow/watermeter-digits.git
```
This is a **private Gitea** on a LAN IP. Clone will fail for anyone outside this network.

**Problem 2: docker-compose references a hardcoded huggingface cache path.**
```yaml
- ~/.cache/huggingface:/app/.cache/huggingface
```
This only works on one specific machine.

**Problem 3: Intel GPU assumed.** docker-compose.yml unconditionally maps `/dev/dri/renderD128`. On a system without an Intel GPU, the container won't start. No fallback to CPU-only.

**The build itself works** — Dockerfile is well-structured (CPU-only PyTorch, gosu for permissions, auto-config on first run). But the docker-compose is not portable.

### Step 4: I read QUICKSTART.md

It tells me to run:
```bash
python app.py
uvicorn app:app --host 0.0.0.0 --port 8000
```

**Neither command works.** The app is a Python package at `watermeter/app.py`, not `app.py` at root. Correct command:
```bash
uvicorn watermeter.app:app --host 0.0.0.0 --port 8001
```

Also, QUICKSTART says port **8000** (9 times!). The actual service runs on **8001**. The Dockerfile, docker-compose.yml, and config.yaml all say 8001. README also says 8000.

### Step 5: I look at config.yaml

Good — placeholder IPs (`192.168.x.x`) with "Replace with your..." comments. This is user-friendly.

But then I look at **README.md line 167**:
```yaml
LABEL_STUDIO_TOKEN: "<REDACTED>"
```
That's a **real API token** committed to git since the initial commit. It's in git history forever.

### Step 6: I notice the repo is 249MB

Because **~93MB of model binaries** are committed directly to git:

| File | Size |
|------|------|
| `arrows/selected/*.bin` | 43 MB |
| `digits/selected/*.bin` | 45 MB |
| `digits/ov_model/*.bin` | 3 MB |
| Various `.xml` files | ~2.5 MB |

These should be in Git LFS, a release artifact, or downloaded during build.

### Step 7: I notice the clutter at root

The repo root has **9 tracked markdown files**, half of which are internal notes:

| File | What it is | Should be public? |
|------|-----------|-------------------|
| `README.md` | Design doc posing as README | Rewrite |
| `QUICKSTART.md` | Setup guide (outdated, wrong port/commands) | Rewrite |
| `DOCKER.md` | Docker guide (accurate, English) | Keep |
| `CLAUDE.md` | AI assistant instructions | No |
| `Integrated_training.md` | Internal feature spec | No |
| `Progress.md` | Internal dev progress tracker | No |
| `plan_failure_tracking.md` | Internal debug plan | No |
| `review_0210_python.md` (54KB!) | Internal code review | No |
| `review_0213_structure.md` (23KB) | Internal code review | No |

Plus **8 tracked scripts/files** at root that are internal:

| File | What it is | Should be public? |
|------|-----------|-------------------|
| `train_arrows.py` | Standalone training script | Move to tools/ or remove |
| `train_digits.py` | Standalone training script | Move to tools/ or remove |
| `benchmark_arrows.py` | Standalone benchmark script | Move to tools/ or remove |
| `benchmark_digits.py` | Standalone benchmark script | Move to tools/ or remove |
| `send_models.sh` | rsync to private IP | No |
| `debug.sh` | Dev debug launcher | Maybe in scripts/ |
| `run.sh` | Docker run wrapper | Superseded by docker-compose |
| `setup.sh` | Installs Docker (!), creates venv | Rewrite or remove |

And **2 Jupyter notebooks** tracked (`arrows/Arrows.ipynb`, `digits/Digits.ipynb`) that aren't mentioned in any docs.

---

## Prioritized Issue List

### P0 — Security (fix before making public)

1. **Leaked Label Studio token** in README.md line 167 — in git history since initial commit. Must rotate the token AND scrub from history (BFG or filter-repo).

2. **HuggingFace token in .env** — `.env` is gitignored (good), but no `.env.example` exists. Users won't know what env vars are needed.

3. **Private Gitea SSH URLs in .gitmodules** — exposes internal infrastructure IPs. Anyone can see `<gitea-host>:2222`.

### P1 — Blocks setup (fix to be usable)

4. **Submodules point to private server** — `git clone --recursive` fails for everyone. Either make submodule repos public, host training data elsewhere, or remove submodules and document how to get ground truth data.

5. **docker-compose.yml has hardcoded path** — `~/.cache/huggingface` won't exist on any other machine. Use `${HOME}/.cache/huggingface` or a named volume.

6. **docker-compose.yml assumes Intel GPU** — `/dev/dri/renderD128` and `group_add` will error on AMD/Nvidia/no-GPU systems. Need a CPU-only profile or conditional.

7. **Port mismatch** — 14 references to port 8000 in docs vs actual port 8001 in code. Users following docs will hit nothing.

8. **QUICKSTART commands don't work** — `python app.py` and `uvicorn app:app` are wrong. Must be `uvicorn watermeter.app:app` or `python -m uvicorn watermeter.app:app`.

### P2 — Repo hygiene (fix to be presentable)

9. **No LICENSE** — Can't use, fork, or contribute without a license. Pick MIT/Apache-2.0/GPL.

10. **93MB of model binaries in git** — Bloats clone. Use Git LFS, or host models as GitHub release assets, or download in Dockerfile.

11. **Internal docs at root** — 5 internal markdown files (77KB+) cluttering the root. Move to `docs/internal/` or remove from tracking.

12. **Internal scripts at root** — 6 scripts (train_*, benchmark_*, send_models.sh) that are dev-only. Move to `scripts/` or `tools/`.

13. **Mixed languages** — README/QUICKSTART in German, DOCKER.md in English, config comments in English. Pick one language for public docs.

14. **README is a design doc** — It describes architecture, formulas, Node-Red migration. A public README should have: what it does, screenshot, prerequisites, quick start, configuration, contributing.

15. **.env.example missing** — No way for users to know they need `HF_TOKEN` or `RENDER_GROUP`.

### P3 — Nice to have

16. **DOCKER.md lists wrong default models** — Lists `model_mobilenetv3_small_100_c11_r144` as digits default, but config.yaml and selected/ use `model_digits_resnext50_32x4d_r128`.

17. **setup.sh installs Docker** — Aggressive for a setup script. Should just set up the venv.

18. **No .env.example / env documentation** — `RENDER_GROUP` variable in docker-compose.yml is undocumented.

19. **Jupyter notebooks tracked but undocumented** — `arrows/Arrows.ipynb` and `digits/Digits.ipynb` exist but no docs mention them.

20. **requirements.txt vs requirements-dev.txt** — Dev requirements not mentioned in any user-facing docs.

21. **config.yaml ships with `client_id: "watermeter-ai-service-debug"`** — "debug" in the default config.

---

## Recommended Action Plan

### Phase 1: Security scrub
- [ ] Rotate Label Studio token
- [ ] Scrub token from git history (BFG repo-cleaner)
- [ ] Remove or anonymize private IPs from .gitmodules
- [ ] Create `.env.example` with placeholder values

### Phase 2: Make it clonable
- [ ] Fix or remove submodules (decide: public data repo or remove)
- [ ] Fix docker-compose.yml (parameterize paths, CPU fallback)
- [ ] Fix port references (8000 → 8001 everywhere)
- [ ] Fix QUICKSTART commands

### Phase 3: Clean up repo
- [ ] Add LICENSE file
- [ ] Move model binaries to Git LFS or release assets
- [ ] Move internal docs to docs/internal/
- [ ] Move standalone scripts to scripts/
- [ ] Rewrite README.md as a proper project README (English)
- [ ] Translate QUICKSTART.md to English or merge into README

### Phase 4: Polish
- [ ] Add screenshot of dashboard to README
- [ ] Add `.env.example`
- [ ] Fix DOCKER.md model list
- [ ] Remove "debug" from default MQTT client_id
- [ ] Consider a `CONTRIBUTING.md`

---

## Open Items

- Decision needed: keep submodules (requires public repos) or remove them entirely?
- Decision needed: language — English only? Or bilingual?
- Decision needed: where to host model weights (LFS, release, Docker-only)?
