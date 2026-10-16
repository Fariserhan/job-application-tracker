"""Smoke tests for the Streamlit app: every page renders against a real demo warehouse.

Hermetic: the demo is built through the real pipeline into a pytest temp dir, the app's own
demo cache is pointed at a temp dir too, and nothing touches the network or the repo's data.
"""
from __future__ import annotations

import html
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(ROOT / "app" / "streamlit_app.py")
PAGES = {
    "views/overview.py": "The search so far",
    "views/pipeline.py": "Pipeline",
    "views/analytics.py": "Analytics",
    "views/follow_ups.py": "Follow-ups",
    "views/data_model.py": "Data & model",
    "views/settings.py": "Settings",
}
# Removed features (AGENTS.md) must never resurface in the UI copy.
BANNED = ("fit score", "fit_score", "cv gap", "cv match", "job discovery", "scan market",
          "scan_market", "market scanner")
ENV_KEYS = ("JOBTRACKER_DATA_DIR", "JOBTRACKER_DEMO_DIR", "JOBTRACKER_DEMO")


@contextmanager
def app_env(**values: str) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
def demo_dirs(tmp_path_factory) -> dict[str, str]:
    from jobtracker.demo import build_demo

    base = tmp_path_factory.mktemp("app")
    build_demo(base / "data")
    return {"JOBTRACKER_DATA_DIR": str(base / "data"),
            "JOBTRACKER_DEMO_DIR": str(base / "demo-cache")}


def _texts(at: AppTest) -> str:
    """Everything the user can read: HTML blocks, markdown, captions, code, headings."""
    parts = [h.proto.body for h in at.get("html")]
    for kind in ("markdown", "caption", "code", "title", "header", "subheader", "info",
                 "warning", "error", "success"):
        parts += [str(getattr(e, "value", "")) for e in at.get(kind)]
    parts += [str(b.label) for b in at.button]
    text = re.sub(r"<style.*?</style>", "", "\n".join(parts), flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _open(page: str | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    if page and page != "views/overview.py":
        at.switch_page(page)
        at.run()
    return at


@pytest.mark.parametrize("page", list(PAGES))
def test_every_page_renders(demo_dirs, page):
    with app_env(**demo_dirs):
        at = _open(page)
        assert not at.exception, [e.value for e in at.exception]
        text = _texts(at)
        assert PAGES[page] in text
        assert "Job Search Analytics" in text  # the masthead renders on every page
        lowered = text.lower()
        for term in BANNED:
            assert term not in lowered, f"removed feature {term!r} resurfaced on {page}"


def test_pages_render_under_filters(demo_dirs):
    with app_env(**demo_dirs):
        at = _open()
        at.session_state["f_period"] = "Last 90 days"
        at.session_state["f_platforms"] = ["LinkedIn"]
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        for page in ("views/analytics.py", "views/pipeline.py", "views/follow_ups.py"):
            at.switch_page(page)
            at.run()
            assert not at.exception, (page, [e.value for e in at.exception])


def test_filters_that_match_nothing_show_an_empty_state(demo_dirs):
    with app_env(**demo_dirs):
        at = _open()
        at.session_state["f_search"] = "zzz-no-such-employer"
        at.run()
        assert not at.exception
        assert "Nothing to show yet" in _texts(at)


def test_pipeline_table_view_and_manual_entry(demo_dirs):
    from jobtracker.config import load_settings
    from jobtracker.store import Repository

    with app_env(**demo_dirs):
        before = Repository(load_settings()).counts()["valid"]
        at = _open("views/pipeline.py")
        at.session_state["pipe_view"] = "Table"
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert len(at.dataframe) >= 1

        at.session_state["pipe_view"] = "Board"
        at.run()
        next(w for w in at.text_input if w.label == "Company *").input("Acme Analytics")
        next(w for w in at.text_input if w.label == "Role *").input("BI Analyst")
        next(b for b in at.button if b.label == "Add application").click()
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert Repository(load_settings()).counts()["valid"] == before + 1


def test_demo_mode_when_data_dir_has_no_database(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    demo_cache = tmp_path / "demo-cache"
    with app_env(JOBTRACKER_DATA_DIR=str(empty), JOBTRACKER_DEMO_DIR=str(demo_cache)):
        at = _open()
        assert not at.exception, [e.value for e in at.exception]
        text = _texts(at)
        assert "synthetic data generated by the real pipeline" in text
        assert "Regenerate demo" in text and "Sync Gmail" not in text
        assert (demo_cache / "applications.db").exists()
        assert not (empty / "applications.db").exists()  # the user's dir is never written


def test_demo_refuses_to_overwrite_real_data(tmp_path):
    import sqlite3

    from app.ui.data import regenerate_demo

    real = tmp_path / "real"
    real.mkdir()
    sqlite3.connect(real / "applications.db").close()
    with pytest.raises(RuntimeError):
        regenerate_demo(real)
    assert (real / "applications.db").exists()


def test_stylesheet_survives_the_html_sanitiser():
    css = (ROOT / "app" / "assets" / "style.css").read_text(encoding="utf-8")
    assert "<" not in css  # DOMPurify drops a <style> whose text looks like markup
    assert "prefers-reduced-motion: no-preference" in css  # motion is opt-in by media query


def test_narrative_copy():
    from app.ui import narrative

    assert narrative.one_in(25.0) == "1 in 4"
    assert narrative.one_in(2.7) == "2.7%"
    assert narrative.one_in(None) == "—"
    segments = narrative.lede_segments({"applications": 168, "companies": 28,
                                        "first_applied": "2026-04-23", "response_rate_pct": 25.0,
                                        "response_band": "Strong"})
    sentence = "".join(text for text, _ in segments)
    assert sentence.startswith("168 applications to 28 employers since 23 April.")
    assert "well ahead of" in sentence
