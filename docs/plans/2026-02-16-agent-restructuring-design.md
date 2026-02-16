# Agent Restructuring: Opus as Advisor, Sonnet as Worker

**Date:** 2026-02-16
**Goal:** Reduce Opus usage to planning & review only. All implementation via Sonnet.

## Motivation

- **Cost:** Opus is significantly more expensive per token
- **Speed:** Sonnet is faster, reducing wait times in the pipeline
- **Quality:** Sonnet 4.5 is capable enough for implementation when guided by good plans

## New Agent Table

| Agent | Model | Use for |
|-------|-------|---------|
| `planner` | **opus** | Codebase exploration, task doc creation, architecture decisions |
| `reviewer` | **opus** | Pre-commit code review, security checks, convention violations, architecture validation |
| `dev` | **sonnet** | All implementation: multi-file features, refactoring, API changes, routes, config |
| `frontend` | **sonnet** | Templates (Jinja2), HTMX interactions, CSS styling |
| `tester` | **sonnet** | Unit/regression tests, browser testing (Playwright on port 8002) |
| `debugger` | **sonnet** | Test failures, stack traces, runtime errors |
| `researcher` | **sonnet** | Library docs, API research, best practices, web search |

## Changes from Previous Setup

1. **`senior-dev` (opus) removed** — no more Opus for implementation
2. **`junior-dev` renamed to `dev`** — single implementation agent (sonnet) for all complexity levels
3. **`reviewer` expanded** — now also validates architecture conformity (previously implicit in senior-dev)
4. **Planner always assigns to `dev`** — no more junior/senior distinction

## Workflow Impact

- Planner (opus) creates detailed plans with clear WPs
- All WPs assigned to `dev` (sonnet) or `frontend` (sonnet)
- After implementation, `reviewer` (opus) checks code quality AND architecture
- Quality is maintained through better planning upfront, not expensive implementation agents

## Fallback Rule

If `dev` (sonnet) fails at a complex implementation after 2 attempts, the coordinator may escalate to a one-time Opus agent as `senior-dev`. This is the exception, not the rule.

## Opus Budget

- **Before:** 3 opus agents (planner, senior-dev, reviewer) — opus used for ~40-50% of work
- **After:** 2 opus agents (planner, reviewer) — opus used for ~15-20% of work (plan + review only)
