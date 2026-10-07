"""Calibration tab: nav gating, /calibration page, arrows-mode select on the ROI page."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "watermeter" / "templates"


def _service(mode):
    svc = MagicMock()
    svc.config = {"inference": {"arrows_mode": mode}} if mode else {}
    return svc


def _wire_service(svc):
    for name in ("watermeter_service", "watermeter.watermeter_service"):
        if name in sys.modules:
            sys.modules[name].get_service = MagicMock(return_value=svc)


@pytest.fixture
def real_templates(test_client, monkeypatch):
    """Render the real templates (the shared test_client fixture stubs them)."""
    import watermeter.routes.pages as pages

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    pages.register_template_globals(templates)
    monkeypatch.setattr(pages, "templates", templates)
    return templates


@pytest.mark.parametrize("mode,expected", [("calibrated", "calibrated"), ("opencv", "opencv"), (None, "model")])
def test_arrows_mode_global_reads_service_config(test_client, mode, expected):
    from watermeter.routes.pages import current_arrows_mode

    _wire_service(_service(mode))
    assert current_arrows_mode() == expected


def test_arrows_mode_global_survives_broken_service(test_client):
    from watermeter.routes.pages import current_arrows_mode

    for name in ("watermeter_service", "watermeter.watermeter_service"):
        if name in sys.modules:
            sys.modules[name].get_service = MagicMock(side_effect=RuntimeError("no service"))
    assert current_arrows_mode() == "model"


def test_nav_shows_calib_only_when_calibrated(real_templates):
    nav = real_templates.get_template("_nav.html")
    _wire_service(_service("calibrated"))
    assert 'href="/calibration"' in nav.render(nav_active="")
    _wire_service(_service("opencv"))
    assert 'href="/calibration"' not in nav.render(nav_active="")


def test_nav_hidden_when_not_calibrated(test_client):
    """Without the global registered (e.g. stubbed templates) the nav must still render."""
    env = Jinja2Templates(directory=str(TEMPLATES_DIR)).env
    assert 'href="/calibration"' not in env.get_template("_nav.html").render(nav_active="")


def test_calibration_page_renders_in_calibrated_mode(test_client, real_templates):
    _wire_service(_service("calibrated"))
    resp = test_client.get("/calibration")
    assert resp.status_code == 200
    assert "/static/calibration.js" in resp.text
    assert 'class="nav-item active"' in resp.text or "nav-item active" in resp.text


def test_calibration_page_shows_hint_when_mode_off(test_client, real_templates):
    _wire_service(_service("model"))
    resp = test_client.get("/calibration")
    assert resp.status_code == 200
    assert "/roi-config" in resp.text
    assert "/static/calibration.js" not in resp.text


def test_roi_page_has_arrows_mode_select(test_client, real_templates, monkeypatch):
    _wire_service(_service("opencv"))
    resp = test_client.get("/roi-config")
    assert resp.status_code == 200
    assert 'id="arrows-mode-select"' in resp.text
    assert '<option value="opencv" selected>' in resp.text
