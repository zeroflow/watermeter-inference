# Persona-Based Project Evaluation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Evaluate the watermeter-inference project from 10 realistic external perspectives to identify UX gaps, documentation holes, onboarding friction, and feature priorities.

**Architecture:** 10 Sonnet agents roleplay as distinct personas, each evaluating the project (README, code, UI, docs, setup). One Opus agent synthesizes all feedback into prioritized action items.

**Tech Stack:** Agent teams, structured evaluation prompts, markdown output.

---

## The 10 Personas

| # | Persona | Background | What they care about |
|---|---------|-----------|---------------------|
| 1 | **Home Assistant Hobbyist** | Runs HA on a Pi, no coding skills, found this via HACS/Reddit | Setup simplicity, Docker compose, HA integration, "does it just work?" |
| 2 | **IoT Startup Developer** | Building commercial smart metering, evaluating OSS components | API quality, scalability, license, extensibility, multi-meter support |
| 3 | **ML Engineer** | Interested in the training pipeline, model architecture choices | Training code quality, model selection, data augmentation, benchmarks |
| 4 | **DevOps Engineer** | Evaluating for deployment at scale, CI/CD, monitoring | Dockerfile quality, health checks, logging, config management, observability |
| 5 | **Open Source Contributor** | Wants to contribute, evaluating project health and code quality | Contributing guide, issue labels, test coverage, code style, PR process |
| 6 | **Embedded/Edge Engineer** | Running on Coral/Jetson, cares about inference speed and memory | OpenVINO usage, model size, memory footprint, ARM support, GPU acceleration |
| 7 | **Non-Technical Homeowner** | Saw a YouTube video, wants to monitor water usage, barely knows Docker | README clarity, screenshots, step-by-step guide, "ELI5" setup |
| 8 | **Security Auditor** | Pentester evaluating the attack surface before recommending deployment | Auth, input validation, network exposure, secrets management, CORS |
| 9 | **UX/Frontend Developer** | Evaluating the web dashboard, mobile experience, accessibility | Responsive design, accessibility (a11y), error states, loading states, dark mode |
| 10 | **Data Scientist** | Interested in the training data pipeline, synthetic data, labeling workflow | Data quality tools, labeling UX, synthetic generation, class balance, augmentation |

---

## Task Breakdown

### Task 1: Spawn evaluation team (coordinator)

**Step 1: Create team**
Create team `persona-eval` with coordinator.

**Step 2: Create tasks for each persona**
10 tasks, one per persona. Plus 1 synthesis task (blocked by all 10).

**Step 3: Spawn 10 Sonnet agents in parallel**
Each agent gets their persona brief, evaluation criteria, and structured output format.

**Step 4: Spawn 1 Opus synthesis agent (after all 10 complete)**
Collects all reports, deduplicates, prioritizes.

---

### Task 2-11: Persona evaluations (10x Sonnet, parallel)

Each persona agent receives:

1. Their persona description and background
2. Instruction to evaluate from THEIR perspective (not as a developer)
3. Access to: README, docs/, templates, config, Dockerfile, static/
4. Structured output format:

```markdown
## [Persona Name] Evaluation

### First Impression (README + docs)
- What's clear?
- What's confusing?
- What's missing?

### Setup Experience (would I succeed?)
- Rate 1-5: How likely am I to get this running?
- Biggest blocker?
- What would make it easier?

### Feature Gaps (what do I need that's missing?)
1. ...
2. ...

### Strengths (what impressed me?)
1. ...
2. ...

### Verdict
Would I use/recommend this? Why/why not?
One-line summary of the #1 thing to fix.
```

---

### Task 12: Synthesis (1x Opus, after all evaluations)

**Input:** All 10 persona reports

**Output:** `docs/reviews/2026-02-22-persona-evaluation.md` containing:

```markdown
## Executive Summary
- Personas who would succeed: X/10
- Personas who would give up: X/10
- Top 3 cross-cutting themes

## Action Items (prioritized)
### Must-Fix (blocks adoption)
1. ...

### Should-Fix (improves experience)
1. ...

### Nice-to-Have (delights users)
1. ...

## Per-Persona Highlights
[condensed key quote from each]
```

---

## Commit Strategy

No code changes — this is an evaluation exercise. Output is:
- `docs/reviews/2026-02-22-persona-evaluation.md` — synthesized findings
- Committed to `claude/main` after synthesis
