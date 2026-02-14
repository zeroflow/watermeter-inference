# Claude Code Instructions

## After making changes to Python code

Run the unit tests to verify nothing is broken:

```
python -m pytest tests/unit/ tests/regression/ --tb=short -q
```

This takes ~6 seconds and covers all code that can be tested without Docker.

## Integration tests (manual)

Integration tests require a Docker container and are NOT run automatically:

```
python -m pytest -m integration --no-build --base-url http://localhost:8001
```

Or to build + start a fresh container:

```
python -m pytest -m integration
```

## Project structure

- **Framework**: FastAPI on port 8001
- **Main app**: `watermeter/app.py`
- **Routes**: `watermeter/routes/` (pages, training, models, label, roi, service, config)
- **Core modules**: `watermeter/training_manager.py`, `watermeter/model_manager.py`, `watermeter/config_utils.py`, `watermeter/persistence.py`
- **Templates**: `watermeter/templates/` (Jinja2 + HTMX)
- **Static**: `watermeter/static/`
- **Tests**: `tests/unit/`, `tests/regression/`, `tests/integration/`

## Common tasks

- New route: create in `watermeter/routes/`, register in `app.py`, add template in `watermeter/templates/`
- New config field: add to YAML schema, update `config_utils.py`, add migration if needed

## Conventions

- Use `safe_subpath()` from `watermeter.app` for any path joining with user input
- YAML config uses ruamel.yaml (preserves comments), not PyYAML
- Digit classes: `['0','1',...,'9','NAN']` — NAN not N
- Arrow classes: decimal strings like `'0.0'` through `'9.9'`
- Model IDs must pass `ModelManager._validate_model_id()` (no slashes, no `..`)

## Git

- Commit after every completed task — do not wait for the user to remind you
- Prefix all commit messages with `claude: ` (e.g., `claude: fix arrow classification threshold`)
- Never amend or force-push existing commits

## Code quality

- No placeholder or stub implementations — complete every function fully
- No `# TODO` or `pass` left behind unless explicitly told to defer
- Do not simplify or skip error handling for brevity
- Write tests for new functionality before marking done
- Refactor adjacent code if it's clearly broken or inconsistent with the change

## Context management

- For each task, create `docs/tasks/YYYY_MM_DD_TaskName.md`
- Structure: Goal, Plan, Progress log, Open items, Done criteria
- Update the task file as you go — this is your persistent memory
- Reference the task file in commit messages when relevant (e.g., `claude: fix ROI validation — see docs/tasks/2026_02_14_IntegrationRework.md`)
- Do not keep large file contents in conversation — read, process, reference by path
- When debugging, log findings in the task file, not in conversation

## Autonomous backlog mode

When told to "work through the backlog" or "autonomous mode":

1. Read `backlog.md` — pick the next `planned` item (priority order below)
2. Create task doc `docs/tasks/YYYY_MM_DD_BL{NN}_{Name}.md` with architecture + work packages
3. Set BL status to `in-progress`
4. For each work package, use the Task tool with the right subagent:
   - `senior-dev` for complex implementations
   - `junior-dev` for simple changes
   - `tester` for writing/running tests
   - `reviewer` for pre-commit review
   - `debugger` for test failures
   - `researcher` for codebase exploration
5. Commit after each WP. Push after completing a BL item.
6. Set BL status to `done`, move to next item.

**Priority order**: BL-06 → BL-04 → BL-05 → BL-08 → BL-10 → BL-02 → BL-03 → BL-07

**Constraints**:
- Do NOT modify `watermeter/templates/` or `watermeter/static/` — another session handles UI
- Keep your own context clean — delegate file reads and coding to subagents
- If stuck for 3+ turns on a problem, log it in the task doc and move on
- If context is lost, recover from: `backlog.md` → active task doc → `git log`