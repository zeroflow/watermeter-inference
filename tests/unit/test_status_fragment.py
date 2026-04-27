"""Unit tests for the dashboard status fragment template (Issue #2).

These tests render `status_fragment.html` directly through Jinja2 with a
controlled context dict — they do NOT import WatermeterService.  This isolates
template logic from service wiring; service-side derivation is covered by the
service unit tests.

The template receives flattened state keys (matching the production route
handler in `watermeter/routes/pages.py`, which does `context=dict(service.current_state)`).
"""

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "watermeter" / "templates"


@pytest.fixture
def env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def render(env, **overrides) -> str:
    """Render status_fragment.html with sensible defaults + per-test overrides.

    Mirrors the flat-key context the production route builds from
    `dict(service.current_state)`.
    """
    base = {
        "mqtt_connected": True,
        "models_loaded": True,
        "processing": False,
        "total_value": 12.345,
        "unit": "m³",
        "last_update": "2026-04-27T10:00:00",
        "status": "ok",
        "warnings": [],
        "predictions": [],
        # Pipeline-status fields (Issue #2)
        "pipeline_status": "OK",
        "last_valid_reading_age_seconds": 30,
        "consecutive_alignment_failures": 0,
        "is_live": True,
        "last_alignment_error": None,
        # Rejected-reading comparison fields (left blank)
        "last_rejected_value": None,
        "last_published_value": None,
        "last_rejected_timestamp": None,
        "last_published_timestamp": None,
        "last_rejected_reasons": None,
    }
    base.update(overrides)
    return env.get_template("status_fragment.html").render(**base)


def test_pipeline_ok_renders_green_badge(env):
    html = render(env, pipeline_status="OK", total_value=12.345, is_live=True)
    assert "pipeline-status-badge" in html
    assert "pipeline-ok" in html
    assert "OK" in html
    # Live value is shown
    assert "total-value-live" in html
    assert "total-value-stale" not in html


def test_alignment_failed_renders_red_badge_and_suppresses_live_value(env):
    html = render(
        env,
        pipeline_status="FAILED",
        status="alignment_failed",
        total_value=12.345,
        consecutive_alignment_failures=3,
        last_valid_reading_age_seconds=600,
        last_alignment_error="low_confidence",
        is_live=False,
    )
    assert "pipeline-status-badge" in html
    assert "pipeline-failed" in html
    assert "FAILED" in html
    assert "3 consecutive failures" in html
    # Live value is suppressed (no .total-value-live class)
    assert "total-value-live" not in html
    # Stale variant is applied + stale marker is rendered
    assert "total-value-stale" in html
    assert "stale-marker" in html
    # Stale timestamp shown (10 minutes ago)
    assert "10" in html
    # Failure reason surfaces
    assert "low_confidence" in html


def test_degraded_state_shows_yellow_badge(env):
    html = render(
        env,
        pipeline_status="DEGRADED",
        consecutive_alignment_failures=0,
        total_value=12.345,
        last_valid_reading_age_seconds=30,
        is_live=True,
    )
    assert "pipeline-status-badge" in html
    assert "pipeline-degraded" in html
    assert "DEGRADED" in html


def test_stale_state_shows_alarm_badge(env):
    html = render(
        env,
        pipeline_status="STALE",
        consecutive_alignment_failures=12,
        total_value=12.345,
        is_live=False,
    )
    assert "pipeline-status-badge" in html
    assert "pipeline-stale" in html
    assert "STALE" in html
    assert "12 consecutive failures" in html
    # Live value is suppressed
    assert "total-value-live" not in html
    assert "total-value-stale" in html
