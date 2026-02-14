# Task: Rewrite README.md

## Goal
Transform the current README from an internal project-scope document into a polished, public-facing GitHub README that helps new users understand, install, and use the project quickly.

## Current State
- README.md is ~325 lines, heavy on internal architecture details
- Reads like a design document, not a user-facing README
- Missing: badges, screenshots mention, feature highlights, contributing section
- Existing QUICKSTART.md and DOCKER.md cover installation well but aren't linked prominently

## Plan

### Phase 1: Research (researcher agent)
- Analyze best practices for open-source README structure
- Identify key sections a good README needs

### Phase 2: Write (senior-dev agent)
- Rewrite README.md with this structure:
  1. Project title + one-line description
  2. Key features (bullet list, concise)
  3. Quick Start (3-step: clone, configure, run via Docker)
  4. Architecture overview (simplified diagram)
  5. Configuration (brief, link to config.yaml)
  6. Web UI features (brief, no screenshot placeholders)
  7. API reference (table format, compact)
  8. Training & Benchmarking (brief mention, link to docs)
  9. Development (local setup, tests)
  10. License (AGPL-3.0)
- Keep technical accuracy from original
- Remove verbose workflow details (belong in separate docs)
- Link to QUICKSTART.md, DOCKER.md for details

### Phase 3: Quality Check (main agent)
- Verify all links work
- Check technical accuracy
- Ensure nothing critical was lost
- Verify markdown renders correctly

## Progress
- [x] Task file created
- [x] Research phase (researcher agent -- best practices for Docker-based service READMEs)
- [x] Writing phase (senior-dev agent -- rewrote from 325 lines to 160 lines)
- [x] Quality review:
  - Fixed 4 incorrect API endpoint paths (config/save, label/next-image, roi/config, roi/digits+analogs)
  - Fixed arrow class description (was "0.0 through 9.0", now accurate with class count range)
  - Replaced placeholder git URL with generic `<repository-url>`
  - Verified all 7 project structure files exist
  - Verified all 4 linked docs exist (QUICKSTART.md, DOCKER.md, config.yaml, LICENSE)
  - All 104 tests pass

## Done Criteria
- [x] README is concise (160 lines, was 325), well-structured, accurate
- [x] All existing docs are properly linked
- [x] No broken internal references
- [x] Suitable for public GitHub presentation
