# Full Codebase Review — Team-Based Multi-Perspective

> **For Claude:** This plan uses a team of parallel review agents. The coordinator orchestrates via `TeamCreate` and `SendMessage`.

**Goal:** Comprehensive code review of the entire watermeter-inference codebase from three distinct perspectives, with immediate small fixes and a structured findings document for larger items.

**Architecture:** Three personality-driven review agents (Sonnet) work in parallel, each scanning the full codebase through their specific lens. A fixer agent (Sonnet) implements trivial fixes as they're reported. The coordinator (Opus) triages findings, commits fixes, and compiles the final review document.

**Codebase:** ~11,143 lines Python (35 files), 54 test files, 8 HTML templates, 7 JS/CSS static files.

---

## Team Structure

| Agent Name | Model | Personality | Focus Areas |
|------------|-------|-------------|-------------|
| `coordinator` | opus | Orchestrator | Triage findings, commit fixes, compile report |
| `security-hawk` | sonnet | Security-Hardliner | Input validation, injection, path traversal, auth, secrets, CORS, SSRF |
| `perf-nerd` | sonnet | Performance-Optimierer | N+1 queries, blocking I/O, memory leaks, unnecessary copies, caching |
| `clean-coder` | sonnet | Clean-Code-Purist | SOLID, naming, dead code, error handling, type safety, DRY/YAGNI |
| `fixer` | sonnet | Pragmatic Fixer | Implements trivial fixes immediately, runs tests |

## Review Scope per Personality

### security-hawk
- **Routes layer** (`watermeter/routes/*.py`): Input validation on all endpoints, path traversal in file operations, SSRF in image fetching, injection in config handling
- **MQTT** (`mqtt_publisher.py`): Credential handling, topic injection, TLS config
- **Config** (`config_utils.py`): YAML deserialization safety, env var resolution edge cases
- **Image pipeline** (`image_pipeline.py`): URL fetching, file path handling
- **File operations**: All `open()`, `Path()`, `shutil` calls — traversal, symlink attacks
- **Templates**: XSS in Jinja2 templates, unsafe `|safe` filters, JS injection vectors

### perf-nerd
- **Service layer** (`watermeter_service.py`): Sync vs async, blocking calls in event loop, unnecessary delegation overhead
- **Inference** (`inference.py`): Model loading efficiency, prediction hot path, memory management
- **Training** (`training_manager.py`, `training_core.py`): Thread management, dataset loading, GPU memory
- **Image processing** (`image_pipeline.py`, `image_hash.py`): OpenCV efficiency, buffer copies, hash computation
- **Static/Templates**: JS bundle size, unnecessary DOM operations, polling efficiency
- **Startup path**: Lifespan function, singleton initialization, import-time side effects

### clean-coder
- **All Python files**: Naming conventions, function length, parameter counts, return type consistency
- **Dead code**: Unused imports, unreachable branches, commented-out code, deprecated patterns
- **Error handling**: Bare excepts, swallowed exceptions, inconsistent error responses
- **Type safety**: Missing type hints on public APIs, Any abuse, Pydantic model completeness
- **DRY violations**: Duplicated logic across routes, copy-paste patterns
- **Test quality**: Missing edge cases, test isolation, fixture overuse, assertion quality

---

## Workflow

### Phase 1: Parallel Review (3 agents simultaneously)

Each reviewer agent receives:
1. Instruction to read `docs/codebase_map.md` first
2. Their personality brief and focus areas (from above)
3. Instruction to categorize each finding as:
   - **FIX-NOW**: Trivial fix, <=3 lines changed, no behavior change (typos, unused imports, missing type hints on obvious cases)
   - **FINDING**: Non-trivial issue requiring design thought, refactoring, or discussion

Each reviewer returns a structured report:
```
## [Personality] Review Report

### FIX-NOW Items
1. [file:line] Description — Suggested fix
2. ...

### FINDINGS (Backlog)
1. **[Severity: HIGH/MEDIUM/LOW]** [file:line] Title
   - Description
   - Impact
   - Suggested approach
2. ...
```

### Phase 2: Triage & Fix

Coordinator:
1. Collects all three reports
2. Deduplicates overlapping findings
3. Sends all FIX-NOW items to `fixer` agent
4. Fixer implements fixes, runs `pytest`, reports back
5. Coordinator commits each thematic group of fixes to `claude/main`

### Phase 3: Compile Review Document

Coordinator creates `docs/reviews/2026-02-22-full-review.md` containing:
- Executive summary (finding counts by severity and category)
- All FINDINGS organized by severity, then by module
- Cross-references between related findings from different reviewers
- Suggested priority order for addressing findings

---

## Task Breakdown

### Task 1: Create team and spawn reviewers
- Create team `code-review`
- Create tasks for each reviewer
- Spawn 3 reviewer agents in parallel

### Task 2: security-hawk reviews full codebase
- Read `docs/codebase_map.md`
- Review all files through security lens
- Return structured report

### Task 3: perf-nerd reviews full codebase
- Read `docs/codebase_map.md`
- Review all files through performance lens
- Return structured report

### Task 4: clean-coder reviews full codebase
- Read `docs/codebase_map.md`
- Review all files through clean-code lens
- Return structured report

### Task 5: Triage findings
- Coordinator collects 3 reports
- Deduplicate and categorize
- Create fix task list for fixer

### Task 6: Fixer implements FIX-NOW items
- Implement trivial fixes
- Run pytest after each group
- Report results

### Task 7: Commit fixes
- Coordinator commits fix groups to `claude/main`
- Each commit: `claude: code-review fix — [description]`

### Task 8: Compile final review document
- Create `docs/reviews/2026-02-22-full-review.md`
- Organize all FINDINGS by severity
- Add executive summary

---

## Commit Strategy

Small fixes committed thematically:
- `claude: code-review — remove unused imports and dead code`
- `claude: code-review — fix missing input validation in routes`
- `claude: code-review — add missing type hints on public APIs`
- etc.

Each commit only after pytest passes.

## Output

1. **Immediate fixes** committed to `claude/main`
2. **Review document** at `docs/reviews/2026-02-22-full-review.md` with all larger findings
