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

## Conventions

- Use `safe_subpath()` from `watermeter.app` for any path joining with user input
- YAML config uses ruamel.yaml (preserves comments), not PyYAML
- Digit classes: `['0','1',...,'9','NAN']` — NAN not N
- Arrow classes: decimal strings like `'0.0'` through `'9.9'`
- Model IDs must pass `ModelManager._validate_model_id()` (no slashes, no `..`)

## Git

- Always use git for version control
- Prefix all commit messages with `claude: ` (e.g., `claude: fix arrow classification threshold`)
- Never amend or force-push existing commits