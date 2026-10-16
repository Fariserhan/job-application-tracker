"""Keep the app's own modules in step with the code on disk.

Streamlit re-runs the page script on every interaction but keeps imported modules in
memory. When new code arrives in a running process (Streamlit Community Cloud pulls each
push into the live app), the new page script can meet old helper modules and fail with
errors such as ``AttributeError: module 'app.ui.data' has no attribute 'sync_url'``.

``purge_stale()`` runs before the app imports anything else: it drops every ``app.*`` and
``jobtracker.*`` module whose source file changed since it was loaded, so the imports that
follow load the current code. ``remember()`` records what is loaded after each run.
"""
from __future__ import annotations

import os
import sys

PACKAGES = ("app", "jobtracker")
_stamps: dict[str, float] = {}
_first_call = True


def _ours(name: str) -> bool:
    return name.split(".")[0] in PACKAGES and name not in ("app", __name__)


def _loaded() -> dict[str, float]:
    out: dict[str, float] = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if _ours(name) and path:
            try:
                out[name] = os.path.getmtime(path)
            except OSError:
                continue
    return out


def purge_stale() -> list[str]:
    """Drop our modules if any of them is out of date. Returns the stale module names."""
    global _first_call
    current = _loaded()
    if _first_call:
        # This module is new to the process: anything already loaded predates it, so we
        # cannot tell whether it is current. Reloading once is cheap and always safe.
        stale = sorted(current)
        _first_call = False
    else:
        stale = sorted(n for n, m in current.items() if n in _stamps and _stamps[n] != m)
    if stale:
        for name in [n for n in sys.modules if _ours(n)]:
            sys.modules.pop(name, None)
        _stamps.clear()
    return stale


def remember() -> None:
    """Record the source timestamps of everything loaded so far."""
    _stamps.update(_loaded())
