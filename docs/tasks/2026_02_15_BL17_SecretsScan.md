# BL-17: Secrets Scan Results

**Date**: 2026-02-15
**Scan scope**: Full git history (all branches, all commits)
**Status**: COMPLETED

## Executive Summary

**Secrets found**: 1 CRITICAL (leaked API token)
**Action required**: YES - Token rotation recommended, history cleaning optional

The scan identified one leaked Label Studio API token in git history. The token has been removed from the working tree but remains in git history. Additional findings include private network IPs in configuration examples and a private Gitea server URL in submodule configuration.

---

## Findings

### [CRITICAL] Label Studio API Token Leaked

**Token**: `<REDACTED>`

**Where found**:
- Initial commit `296a44e` (2024-11-27): `README.md` — example config with real token
- Commit `c1f757c` (2024-12-06): Continued presence in README
- Commit `1239304` (2026-02-14): Removed from `flows.json` when file was deleted
- Last appearance in working tree: Before commit `e038439` (2026-02-14)

**Status**:
- Removed from current working tree (HEAD)
- Still present in git history (permanent record)
- Found in 2 occurrences across multiple commits:
  - `flows.json` Node-RED flow export (now deleted from working tree)
  - `README.md` example config (now rewritten without token)

**Impact**:
- If this token is still active, anyone with read access to the git history can authenticate to the Label Studio instance at `http://<server-ip>:8080`
- Given that the Label Studio URL uses a private IP (<server-ip>), the token is only exploitable from within the same network
- Risk level: MEDIUM (private network exposure) to HIGH (if repo becomes public)

**Action**:
1. **REQUIRED**: Rotate the Label Studio token immediately
   - Generate new token at Label Studio instance
   - Update local configuration files (not tracked in git)
   - Invalidate old token `<REDACTED>`

2. **OPTIONAL**: Clean git history
   - Use BFG Repo Cleaner or `git filter-branch` to remove token from history
   - Requires force push to all remotes
   - Affects all collaborators (everyone must re-clone)
   - Only recommended if repository will become public

**Commands to clean history** (if needed):
```bash
# Using BFG Repo Cleaner (recommended)
bfg --replace-text <(echo '<REDACTED>==>REDACTED') .git
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# Force push to all remotes
git push --force --all
git push --force --tags
```

---

### [WARNING] Private Network Infrastructure Exposed

**What**: Private IP addresses and internal infrastructure URLs in git history

**Where**:
- `.gitmodules`: Gitea server at `ssh://gitea@<gitea-host>:2222/zeroflow/watermeter-arrows.git` and `/zeroflow/watermeter-digits.git`
- Historical config files and docs (before sanitization):
  - `<mqtt-broker-ip>` — MQTT broker
  - `<server-ip>` — Label Studio server, old watermeter service
  - `<camera-ip>` — AI-on-the-edge device

**Status**:
- Current `config.yaml` uses placeholder `192.168.x.x` with "Replace with your..." comments (good practice)
- Submodules still reference private Gitea server (current HEAD)
- Historical commits contain actual private IPs in documentation and code

**Impact**:
- Exposes internal network topology and infrastructure layout
- Reveals existence of internal Gitea instance on non-standard port
- If repository becomes public: provides reconnaissance information for attackers
- Risk level: LOW (informational disclosure only, no credentials)

**Action**:
- **Current state acceptable**: If repository remains private, no action needed
- **If going public**:
  - Replace `.gitmodules` URLs with public GitHub URLs or remove submodules
  - Consider that historical commits will still contain private IPs (informational only)

---

### [INFO] HF_TOKEN Environment Variable (Not Leaked)

**What**: References to `HF_TOKEN` environment variable throughout codebase

**Where**:
- `docker-compose.yml`: Passes `${HF_TOKEN:-}` as environment variable
- `docker-entrypoint.sh`: Checks for `HF_TOKEN` presence
- `.env.example`: Documents `HF_TOKEN` variable with placeholder
- `.gitignore`: Excludes `.env` (contains actual token)

**Status**:
- NO actual HF_TOKEN values found in git history
- Only variable names and references
- `.env` file never committed (confirmed via `git log --all --diff-filter=A -- .env`)
- `.env.example` exists with placeholder `hf_your_token_here`

**Impact**: NONE - This is correct implementation

**Action**: No action required. This is best practice.

---

### [INFO] MQTT Credentials (Not Found)

**What**: MQTT broker configuration in `config.yaml`

**Where**:
- `config.yaml`: Broker IP, port, topics
- No passwords or usernames in configuration

**Status**: Clean — no MQTT passwords or credentials found in git history

**Impact**: NONE

**Action**: No action required.

---

### [INFO] Test Data False Positives

**What**: Test files containing strings like "secret.jpg", "passwd", "password"

**Where**:
- `test_label_page.py`: Test fixtures for path traversal prevention
- `test_model_manager.py`: Security test cases using `../etc/passwd` as test input

**Status**: These are security test cases, not leaked secrets

**Impact**: NONE - These are test fixtures demonstrating security protection

**Action**: No action required.

---

### [INFO] Large Binary Files in Git History

**What**: Model binaries committed to git history (not secrets, but bloat)

**Largest files**:
- `digits/selected/model_digits_resnext50_32x4d_r128.bin` — 45.9 MB (2 versions)
- `arrows/selected/model_arrows_efficientnetv2_rw_s_c10_r128_s8820.bin` — 44.2 MB (2 versions)
- Smaller models: 3 MB each

**Total**: ~93 MB of model binaries in git history

**Impact**:
- Slows down clone operations
- Increases repository size
- Not a security issue, but a repository hygiene issue

**Action**:
- Consider migrating to Git LFS for model files (future improvement)
- Or host models as GitHub release assets
- Or download models in Dockerfile from external source
- Not urgent for BL-17 (secrets scan), but noted for BL-19 (`.gitignore` audit)

---

### [INFO] Deleted Sensitive Files (Good)

**Files successfully removed**:
- `flows.json` — Deleted in commit `1239304` (contained Label Studio token)
- `server_detect.py` — Deleted in commit `3e8dccb` (contained hardcoded `SERVER_IP = "<server-ip>"`, `SERVER_USER = "<user>"`)

**Status**: Both files removed from working tree, still in git history

**Impact**: Positive — cleanup already performed

**Action**: No further action required.

---

## Summary of Actions

### Immediate (REQUIRED)
1. **Rotate Label Studio API token** `<REDACTED>`
   - Generate new token in Label Studio UI
   - Update local config files (`.env` or `config.yaml`)
   - Invalidate/delete old token

### Before Making Repository Public (RECOMMENDED)
2. **Clean git history** using BFG Repo Cleaner to remove:
   - Label Studio token `<REDACTED>`
   - (Optional) Private IP addresses for defense-in-depth

3. **Update `.gitmodules`** to remove private Gitea URLs:
   - Replace `ssh://gitea@<gitea-host>:2222/zeroflow/*` with public URLs
   - Or remove submodules entirely and document alternative setup

### Optional (Repository Hygiene)
4. **Migrate model binaries** to Git LFS or external hosting
5. **Document environment variables** in `.env.example` (already done)

---

## Scan Methodology

### Tools Used
- `git log -p --all` with grep patterns
- Manual inspection of:
  - Password/token patterns: `(password|passwd|secret|token|api_key|apikey|private_key|credential)`
  - HuggingFace tokens: `hf_[A-Za-z0-9]{20,}`
  - Label Studio token: `<REDACTED>`
  - Private IPs: `192\.168\.|10\.`
  - MQTT credentials: `mqtt.*password|mqtt.*user`
  - Environment files: `git log --all --diff-filter=A -- .env`
  - SSH keys: `git log --all --diff-filter=A -- '*.pem' '*.key' '*_rsa'`
  - Large binaries: `git rev-list --objects --all | git cat-file --batch-check`

### Files Scanned
- All commits in all branches (`git log -p --all`)
- All file additions (`--diff-filter=A`)
- Working tree files (`.gitignore`, `config.yaml`)
- Deleted files (flows.json, server_detect.py)

### Coverage
- Full git history from initial commit `296a44e` to current HEAD `1e3f4d4`
- All branches (only `main` exists)
- Submodules configuration (`.gitmodules`)
- Jupyter notebooks (`*.ipynb`)
- Python source files (`*.py`)
- Configuration files (`*.yaml`, `*.yml`)
- Documentation (`*.md`)

---

## Conclusion

**Overall risk level**: LOW to MEDIUM

The repository has **one critical finding** (leaked Label Studio token) that should be addressed before making the repository public. However, since the Label Studio instance is on a private IP, the risk is mitigated unless the repository is cloned within the same network by untrusted parties.

The development team has already demonstrated good security practices:
- `.env` file properly gitignored (never committed)
- Environment variables documented in `.env.example`
- Sensitive files (`flows.json`, `server_detect.py`) removed from working tree
- Placeholder IPs in current documentation

**Recommendation**: Rotate the Label Studio token immediately as a precaution. If the repository will remain private and internal to the development team, no further action is required. If the repository will be made public, clean the git history using BFG Repo Cleaner.

---

**Scan completed**: 2026-02-15
**Scanned by**: researcher agent (Claude Sonnet 4.5)
**Review status**: Ready for coordinator review
