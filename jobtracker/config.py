"""Runtime settings: where data lives and the user-tunable rules.

Resolution order for every value: environment variable -> ``settings.json`` in the data
directory (written by the app's Settings page) -> the default below.

Data directory: ``JOBTRACKER_DATA_DIR`` if set, else ``<project>/data``. A checkout that still
has the v1 layout (``applications.db`` in the project root) keeps working: the root is used
until the files are moved, so upgrading never strands existing data.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SETTINGS_FILE = "settings.json"


def _default_data_dir() -> Path:
    env = os.environ.get("JOBTRACKER_DATA_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    modern = PROJECT_ROOT / "data"
    legacy_db = PROJECT_ROOT / "applications.db"
    if legacy_db.exists() and not (modern / "applications.db").exists():
        return PROJECT_ROOT
    return modern


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)

    # Pipeline rules (editable in the app).
    ghost_after_days: int = 21
    follow_up_after_days: int = 7
    gmail_after: str = "2026/01/01"
    gmail_max_results: int = 2000

    # Optional Claude fallback for emails the rules cannot classify confidently.
    llm_enabled: bool = False
    llm_model: str = "claude-haiku-5-5"
    llm_monthly_budget_usd: float = 1.00
    llm_max_calls_per_sync: int = 150

    # Job-posting enrichment (OpenGraph / JSON-LD scrape of the posting link).
    enrich_enabled: bool = True

    # ------------------------------------------------------------------ paths
    @property
    def db_path(self) -> Path:
        return self.data_dir / "applications.db"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse.duckdb"

    @property
    def state_path(self) -> Path:
        return self.data_dir / "sync_state.json"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def secret_path(self, name: str) -> Path:
        """credentials.json / token.json: data dir first, then the project root (v1 layout)."""
        primary = self.data_dir / name
        if primary.exists():
            return primary
        legacy = PROJECT_ROOT / name
        return legacy if legacy.exists() else primary

    def ensure_dirs(self) -> Settings:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return self

    # ------------------------------------------------------------------ persistence
    def editable(self) -> dict:
        data = asdict(self)
        data.pop("data_dir")
        return data

    def save(self) -> Path:
        self.ensure_dirs()
        path = self.data_dir / SETTINGS_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.editable(), indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path

    def with_updates(self, **changes) -> Settings:
        return replace(self, **changes)


_ENV = {
    "ghost_after_days": "JOBTRACKER_GHOST_AFTER_DAYS",
    "follow_up_after_days": "JOBTRACKER_FOLLOW_UP_AFTER_DAYS",
    "gmail_after": "GMAIL_AFTER",
    "gmail_max_results": "GMAIL_MAX_RESULTS",
    "llm_enabled": "JOBTRACKER_LLM",
    "llm_model": "JOBTRACKER_LLM_MODEL",
    "llm_monthly_budget_usd": "JOBTRACKER_LLM_BUDGET_USD",
    "llm_max_calls_per_sync": "JOBTRACKER_LLM_MAX_CALLS",
    "enrich_enabled": "JOBTRACKER_ENRICH",
}


def _coerce(kind, raw):
    if kind is bool or kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if kind is int or kind == "int":
        return int(float(raw))
    if kind is float or kind == "float":
        return float(raw)
    return str(raw)


def load_settings(data_dir: Path | str | None = None) -> Settings:
    base = Settings(data_dir=Path(data_dir).resolve()) if data_dir else Settings()
    values: dict = {}
    stored = base.data_dir / SETTINGS_FILE
    if stored.exists():
        try:
            loaded = json.loads(stored.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                values.update(loaded)
        except (OSError, ValueError):
            pass
    for name, env in _ENV.items():
        if os.environ.get(env, "").strip():
            values[name] = os.environ[env]
    known = {f.name: f.type for f in fields(Settings) if f.name != "data_dir"}
    clean = {}
    for name, raw in values.items():
        if name in known:
            try:
                clean[name] = _coerce(known[name], raw)
            except (TypeError, ValueError):
                continue
    return replace(base, **clean)
