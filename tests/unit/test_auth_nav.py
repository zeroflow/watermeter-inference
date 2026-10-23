"""Nav and theme includes: Account and Logout items, the auth shim on every page."""

from pathlib import Path

import pytest
from fastapi.templating import Jinja2Templates

from watermeter.routes.pages import register_template_globals

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "watermeter" / "templates"
PAGES = [
    "dashboard",
    "label",
    "training",
    "roi_config",
    "config_editor",
    "explore",
    "calibration",
    "setup",
    "login",
    "account",
]


@pytest.fixture(scope="module")
def templates():
    """The real templates (the shared test_client fixture stubs the pages ones)."""
    tmpl = Jinja2Templates(directory=str(TEMPLATES_DIR))
    register_template_globals(tmpl)
    return tmpl


def test_nav_offers_account_and_a_post_logout(templates):
    """The nav links to /account and logs out through a POST form, never a GET link."""
    html = templates.get_template("_nav.html").render(nav_active="")
    assert 'href="/account"' in html
    assert 'action="/logout"' in html
    assert 'method="post"' in html
    assert 'href="/logout"' not in html


def test_nav_marks_account_active(templates):
    """The Account item carries the active class on the account page only."""
    nav = templates.get_template("_nav.html")
    assert 'class="nav-item nav-item-compact active"' in nav.render(nav_active="account")
    assert 'class="nav-item nav-item-compact active"' not in nav.render(nav_active="")


def test_nav_items_are_accessible_without_labels(templates):
    """On mobile the labels are hidden, so the icons need an aria-label and a title."""
    html = templates.get_template("_nav.html").render(nav_active="")
    assert 'aria-label="Account"' in html and 'title="Account"' in html
    assert 'aria-label="Log out"' in html and 'title="Log out"' in html


def test_theme_include_loads_the_auth_shim(templates):
    """_theme.html loads /static/auth.js."""
    assert 'src="/static/auth.js"' in templates.get_template("_theme.html").render()


@pytest.mark.parametrize("page", PAGES)
def test_every_page_includes_the_theme_partial(page):
    """Each page template pulls in _theme.html, which is how the shim reaches every page."""
    source = (TEMPLATES_DIR / f"{page}.html").read_text(encoding="utf-8")
    assert "{% include '_theme.html' %}" in source
