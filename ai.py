"""Free, zero-cost AI analysis layer for the dashboard.

Uses LOCAL open-weight models served by Ollama (no API key, no billing, data stays on the
machine), with an optional free-tier OpenAI-compatible endpoint as a secondary provider
(Groq / OpenRouter `:free` / Gemini free — opt-in via env vars).

Every function is defensive: on any failure (no model, timeout, bad JSON) it returns `None`
or an empty dict, and the dashboard falls back to its deterministic logic. AI can never
break the app or cost money.

Providers, in priority order:
  1. Ollama (local)            -> OLLAMA_HOST (default http://127.0.0.1:11434), model = AI_MODEL
                                   or the smallest available model.
  2. OpenAI-compatible free    -> OPENAI_API_KEY + OPENAI_BASE_URL + AI_MODEL.

Env switches:
  AI_DISABLE=1                 force every provider off (pure deterministic mode).
  AI_TIMEOUT=20                seconds to allow a single generation (default 20, capped 60).
  AI_MAX_CACHE=1000            max cached responses (default 1000).
"""
import hashlib
import json
import os
import re
import threading

import requests

CACHE_PATH = os.environ.get("AI_CACHE_PATH", "ai_cache.json")
DEFAULT_TIMEOUT = float(os.environ.get("AI_TIMEOUT", "20"))
MAX_CACHE = int(os.environ.get("AI_MAX_CACHE", "1000"))

# Curated free providers. All are OpenAI-compatible, free tiers, no credit card required.
# 'ollama' kind = fully local (no key). Everything else needs a free API key.
DEFAULT_PROVIDER = "Google Gemini (free)"
PROVIDERS = {
    "Auto (env or Ollama)": {
        "kind": "auto",
        "base_url": "",
        "models": [],
        "key_hint": "Uses OPENAI_API_KEY + OPENAI_BASE_URL env vars, or a local Ollama.",
        "signup": "",
    },
    "Google Gemini (free)": {
        "kind": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash"],
        "key_hint": "Free Gemini API key from Google AI Studio (aistudio.google.com/apikey). "
                    "No credit card needed.",
        "signup": "https://aistudio.google.com/apikey",
    },
    "Groq (free)": {
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen-2.5-32b"],
        "key_hint": "Free Groq API key at console.groq.com/keys (fastest inference, tighter "
                    "rate limits).",
        "signup": "https://console.groq.com/keys",
    },
    "OpenRouter (free)": {
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            "deepseek/deepseek-chat-v3-0324:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen-2.5-72b-instruct:free",
        ],
        "key_hint": "Free OpenRouter key at openrouter.ai/keys — use models with the :free "
                    "suffix (may be rate-throttled).",
        "signup": "https://openrouter.ai/keys",
    },
    "Cerebras (free)": {
        "kind": "openai",
        "base_url": "https://api.cerebras.ai/v1",
        "models": ["llama-3.3-70b", "llama3.1-8b"],
        "key_hint": "Free Cerebras key at cloud.cerebras.ai.",
        "signup": "https://cloud.cerebras.ai",
    },
    "OpenCode Go (your key)": {
        "kind": "openai",
        "base_url": os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go/v1"),
        "models": ["deepseek-v4-flash", "glm-5.3-flash", "mimo-v2.5", "kimi-k2.7-code",
                   "longcat-2.0", "glm-5.1", "deepseek-v4-pro", "hy3", "kimi-k3", "omen-alpha"],
        "key_hint": "Your OpenCode Go subscription key (opencode.ai/auth). Uses OPENCODE_API_KEY "
                    "or paste it below. OpenAI-compatible at opencode.ai/zen/go/v1 — every model "
                    "here is included in your flat subscription (no extra per-request cost).",
        "signup": "https://opencode.ai/auth",
    },
    "Ollama (local)": {
        "kind": "ollama",
        "base_url": "",
        "models": [],
        "key_hint": "Fully local & private — install Ollama and pull a model, e.g. "
                    "`ollama pull llama3.2:3b`. No key.",
        "signup": "https://ollama.com",
    },
}

_active_provider = None      # e.g. "Google Gemini (free)" or "Ollama (local)"
_active_key = None           # session-only free-tier API key (never written to disk)
_active_model = None         # chosen model name

_models_cache = None
_models_cache_lock = threading.Lock()
_cache_lock = threading.Lock()
_provider_lock = threading.Lock()
_models_by_base = {}  # (base_url|keyhash) -> list of model ids, fetched live once per session

_session_id = None
_SESSION_FILE = os.environ.get("OPENCODE_SESSION_FILE", "opencode_session.txt")


def _opencode_session() -> str:
    """A stable, per-install session id sent in the x-opencode-session header. OpenCode Go
    requires it for routing/caching (missing it => HTTP 400 MissingSessionID)."""
    global _session_id
    if _session_id:
        return _session_id
    env = os.environ.get("OPENCODE_SESSION")
    if env and env.strip():
        _session_id = env.strip()
        return _session_id
    try:
        if os.path.exists(_SESSION_FILE):
            with open(_SESSION_FILE, "r", encoding="utf-8") as fh:
                val = fh.read().strip()
            if val:
                _session_id = val
                return val
    except OSError:
        pass
    import uuid
    val = str(uuid.uuid4())
    _session_id = val
    try:
        with open(_SESSION_FILE, "w", encoding="utf-8") as fh:
            fh.write(val)
    except OSError:
        pass
    return val

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


# ---------------------------------------------------------------- provider discovery

def _ollama_base() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def providers() -> list:
    return list(PROVIDERS.keys())


def _fetch_models(base_url: str, api_key: str, fallback: list) -> list:
    """Try to list the provider's actual available models (OpenAI-compatible /models)."""
    ckey = base_url.rstrip("/") + "|" + hashlib.sha1(api_key.encode("utf-8")).hexdigest()[:8]
    with _provider_lock:
        if ckey in _models_by_base:
            return _models_by_base[ckey]
    chosen = list(fallback)
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=(2.0, 5.0),
        )
        if resp.status_code == 200:
            ids = [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
            if "generativelanguage" in base_url:
                # Gemini free tier: keep the flash/lite (fast, free) models
                fast = [i for i in ids if "flash" in i.lower() or "lite" in i.lower()]
                chosen = _newest_first(fast) or ids or chosen
            else:
                chosen = _newest_first(ids) or chosen
    except Exception:
        pass
    with _provider_lock:
        _models_by_base[ckey] = chosen
    return chosen


_VERSION_RE = re.compile(r"gemini[-/](\d+(?:\.\d+)*)", re.IGNORECASE)


def model_version(model: str | None):
    """Sortable version tuple for a model id (e.g. gemini-3.6-flash -> (3, 6))."""
    match = _VERSION_RE.search(model or "")
    if not match:
        return None
    return tuple(int(x) for x in match.group(1).split("."))


def _sort_key(model: str):
    low = model.lower()
    if "flash" in low and "lite" not in low:
        quality = 0
    elif "lite" in low:
        quality = 1
    else:
        quality = 2
    return (model_version(model) or (-1,), -quality)


def _newest_first(models: list) -> list:
    return sorted(models, key=_sort_key, reverse=True)


def provider_models(provider: str | None = None, api_key: str | None = None) -> list:
    p = provider or _active_provider
    if not p or p not in PROVIDERS:
        return []
    info = PROVIDERS[p]
    if info["kind"] == "ollama":
        return ollama_models()
    if p == "OpenCode Go (your key)":
        # Curated, subscription-included models only (OpenAI chat-completions transport).
        # No per-request cost beyond your flat Go subscription.
        return list(info["models"])
    key = _sanitize_key(api_key or _active_key or _provider_env_key(p))
    if key:
        return _fetch_models(info["base_url"], key, list(info["models"]))
    return list(info["models"])


def provider_signup(provider: str) -> str:
    info = PROVIDERS.get(provider, {})
    return info.get("signup", "")


def provider_key_hint(provider: str) -> str:
    info = PROVIDERS.get(provider, {})
    return info.get("key_hint", "")


def default_provider() -> str:
    if os.environ.get("OPENCODE_API_KEY") or os.environ.get("ZEN_API_KEY"):
        return "OpenCode Go (your key)"
    return os.environ.get("AI_DEFAULT_PROVIDER", DEFAULT_PROVIDER)


def _sanitize_key(key) -> str:
    """API keys are ASCII. Strip copy-paste junk (emojis, whitespace, newlines) so a stray
    '❌' or newline from the clipboard can't crash the HTTP request (headers are latin-1)."""
    if not key:
        return ""
    return "".join(ch for ch in str(key) if ord(ch) < 128).strip()


def set_provider(provider: str | None, api_key: str | None = None,
                 model: str | None = None):
    """Select the active provider. None or 'Auto (env or Ollama)' => auto-detect (env → Ollama)."""
    global _active_provider, _active_key, _active_model
    if provider in (None, "", "Auto (env or Ollama)"):
        _active_provider = None
    else:
        _active_provider = provider if (provider and provider in PROVIDERS) else None
    _active_key = _sanitize_key(api_key) or None
    _active_model = (model or "").strip() or None


def active_provider() -> str | None:
    return _active_provider


def active_model() -> str:
    if _active_model:
        return _active_model
    if _active_provider:
        info = PROVIDERS.get(_active_provider, {})
        if info.get("kind") == "ollama":
            return preferred_model()
        if info.get("models"):
            return info["models"][0]
    return preferred_model() or os.environ.get("AI_MODEL", "")


def _provider_env_key(provider: str) -> str:
    if provider == "OpenCode Go (your key)":
        return os.environ.get("OPENCODE_API_KEY") or os.environ.get("ZEN_API_KEY") or ""
    return os.environ.get("OPENAI_API_KEY") or ""


def _openai_compatible() -> dict | None:
    key = _active_key or _provider_env_key(_active_provider or "")
    base = os.environ.get("OPENAI_BASE_URL")
    model = _active_model or os.environ.get("AI_MODEL")
    if key and base and model:
        return {"base_url": base.rstrip("/"), "api_key": key, "model": model}
    return None


def ollama_models() -> list:
    """Installed local model names (empty when Ollama is absent)."""
    global _models_cache
    if _models_cache is not None:
        return _models_cache
    with _models_cache_lock:
        if _models_cache is not None:
            return _models_cache
        try:
            resp = requests.get(f"{_ollama_base()}/api/tags", timeout=2.0)
            if resp.status_code == 200:
                _models_cache = [m.get("name", "") for m in resp.json().get("models", [])
                                 if m.get("name")]
                return _models_cache
        except Exception:
            pass
        _models_cache = []
        return _models_cache


def refresh_models():
    global _models_cache
    with _models_cache_lock:
        _models_cache = None


def preferred_model() -> str:
    pinned = os.environ.get("AI_MODEL") or os.environ.get("OLLAMA_MODEL")
    if pinned:
        return pinned
    models = ollama_models()
    if not models:
        return ""
    # prefer small/fast instruction-tuned models for cheap local inference
    for m in models:
        low = m.lower()
        if any(k in low for k in ("llama3.2:3b", "llama3.2", "qwen2.5:3b", "gemma3:4b", "gemma3", "deepseek-r1:7b")):
            return m
    return models[0]


def available() -> bool:
    if os.environ.get("AI_DISABLE") == "1":
        return False
    return _client_config() is not None


def provider_label() -> str:
    """Human-readable 'where the AI is running' label for the UI."""
    if _active_provider:
        model = _active_model or ""
        if _active_provider == "Ollama (local)":
            return f"Ollama (local) · {model or (ollama_models() or ['?'])[0]}"
        return f"{_active_provider} · {model}" if model else _active_provider
    models = ollama_models()
    if models:
        return f"Ollama (local) · {preferred_model() or models[0]}"
    cfg = _openai_compatible()
    if cfg:
        return f"Free endpoint · {cfg['model']}"
    return "not available"


# ---------------------------------------------------------------- generation

def key_preview(key=None) -> str:
    """Masked view of the key actually being used, for debugging (never shows the full key)."""
    k = _sanitize_key(key or _active_key or _provider_env_key(_active_provider or ""))
    if not k:
        return "(no key)"
    if len(k) <= 12:
        return f"{k} (len {len(k)})"
    return f"{k[:6]}…{k[-4:]} (len {len(k)})"


def _client_config() -> dict | None:
    if os.environ.get("AI_DISABLE") == "1":
        return None
    # An explicit provider selection wins.
    if _active_provider:
        info = PROVIDERS.get(_active_provider)
        if not info:
            return None
        if info["kind"] == "ollama":
            models = ollama_models()
            if not models:
                return None
            return {
                "base_url": f"{_ollama_base()}/v1",
                "api_key": "ollama",
                "model": _active_model or preferred_model() or models[0],
            }
        # openai-compatible preset
        key = _active_key or _provider_env_key(_active_provider)
        if not key:
            return None
        model = _active_model or (info["models"][0] if info["models"] else None) \
            or os.environ.get("AI_MODEL")
        if not model:
            return None
        return {"base_url": info["base_url"], "api_key": key, "model": model}
    # Fallback: env vars, then local Ollama.
    models = ollama_models()
    if models:
        return {
            "base_url": f"{_ollama_base()}/v1",
            "api_key": "ollama",
            "model": os.environ.get("AI_MODEL") or preferred_model() or models[0],
        }
    return _openai_compatible()


_last_error = None


def last_error() -> str | None:
    return _last_error


def clear_last_error():
    global _last_error
    _last_error = None


def _set_error(message: str | None):
    global _last_error
    _last_error = message


def generate(system: str, prompt: str, model: str | None = None,
             max_tokens: int = 800, temperature: float = 0.4,
             timeout: float | None = None, json_mode: bool = False) -> str | None:
    """One chat completion. Returns trimmed text or None on any failure.

    The real reason for failure is recorded (see `last_error()`) so the UI can tell the
    user whether it's an invalid key, unknown model, quota, or a network problem.

    `json_mode` asks the provider to constrain decoding to a JSON object (native structured
    output), which removes prose/fences and makes the reply parse reliably. Providers that
    reject the field are handled by the existing minimal-payload retry below.
    """
    cfg = _client_config()
    if not cfg:
        _set_error("no provider configured (missing key/model or Ollama)")
        return None
    model = model or cfg["model"]
    base_url = cfg["base_url"]
    if not model:
        _set_error("no model selected")
        return None
    t = min(DEFAULT_TIMEOUT if timeout is None else timeout, 60.0)
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    if _active_provider == "OpenCode Go (your key)":
        # OpenCode Go requires a stable session header + a real user agent, not a generic HTTP lib
        headers["x-opencode-session"] = _opencode_session()
        headers["User-Agent"] = "job-suite/1.0"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    # rstrip('/') so a preset base_url with a trailing slash can't produce '//chat/completions'
    endpoint = f"{base_url.rstrip('/')}/chat/completions"

    def _call(payload, timeout_s):
        try:
            resp = requests.post(endpoint, headers=headers, json=payload,
                                 timeout=(2.05, timeout_s))
        except requests.exceptions.Timeout:
            return f"timed out after {timeout_s:.0f}s", None
        except requests.exceptions.SSLError:
            return "SSL error — the connection to the provider was rejected", None
        except requests.exceptions.ConnectionError:
            return "connection failed — can this machine reach the provider?", None
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}", None
        if resp.status_code == 200:
            try:
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                text = (msg.get("content") or "").strip()
                finish = choice.get("finish_reason")
            except Exception:
                return f"bad response (HTTP 200): {resp.text[:200]}", None
            if text:
                return None, text
            if finish == "length":
                # model burned all its token budget on reasoning with no visible answer yet
                return "LENGTH_EMPTY", None
            return f"empty response (HTTP 200): {resp.text[:120]}", None
        # grab the provider's error body — it usually explains exactly what's wrong
        body = resp.text.strip()[:400].replace("\n", " ")
        return f"HTTP {resp.status_code}: {body}", None

    # Attempt 1: full payload (temperature + max_tokens [+ response_format]).
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    reason, text = _call(payload, t)
    if text is not None:
        _set_error(None)
        return text
    # The model hit its token budget with no visible text (reasoning models do this) —
    # retry once with a much larger budget so the actual answer can surface.
    if reason == "LENGTH_EMPTY":
        retry = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max(max_tokens, 512),
            "stream": False,
        }
        if json_mode:
            retry["response_format"] = {"type": "json_object"}
        reason2, text2 = _call(retry, t)
        if text2 is not None:
            _set_error(None)
            return text2
        reason = reason2 or reason
    # Attempt 2: minimal payload — some free endpoints reject extra fields (400/404).
    if reason and reason.startswith("HTTP 4"):
        reason2, text2 = _call({"model": model, "messages": messages}, t)
        if text2 is not None:
            _set_error(None)
            return text2
        reason = reason2 or reason
    _set_error(f"{_active_provider or 'provider'} · {model} · {reason}")
    return None


# ---------------------------------------------------------------- caching

def _cache_path() -> str:
    return os.environ.get("AI_CACHE_PATH", CACHE_PATH)


def _load_cache() -> dict:
    try:
        with open(_cache_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict):
    try:
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path())
    except Exception:
        pass


def generate_cached(system: str, prompt: str, model: str | None = None,
                    max_tokens: int = 800, temperature: float = 0.4,
                    json_mode: bool = False) -> str | None:
    """Disk-cached generate — AI output is stored so re-renders and restarts cost nothing.

    `json_mode` is intentionally left out of the cache key: a JSON-mode reply and a plain
    reply are interchangeable to callers (both are parsed by `parse_json_text`), so keeping
    one key preserves cache hits instead of invalidating every existing entry.
    """
    key = hashlib.sha1(
        f"{model or ''}|{system}|{prompt}|{max_tokens}|{temperature}".encode("utf-8")
    ).hexdigest()
    with _cache_lock:
        cache = _load_cache()
        if key in cache:
            return cache[key]
    text = generate(system, prompt, model=model, max_tokens=max_tokens,
                    temperature=temperature, json_mode=json_mode)
    if not text:
        return None
    with _cache_lock:
        cache = _load_cache()
        cache[key] = text
        if len(cache) > MAX_CACHE:
            for k in list(cache)[:len(cache) - MAX_CACHE]:
                cache.pop(k, None)
        _save_cache(cache)
    return text


def clear_cache() -> int:
    with _cache_lock:
        try:
            n = len(_load_cache())
            os.remove(_cache_path())
            return n
        except OSError:
            return 0


def parse_json_text(text: str) -> dict:
    """Leniently parse an LLM's JSON response (strips code fences, grabs the braces)."""
    if not text:
        return {}
    if _JSON_BLOCK.search(text):
        text = _JSON_BLOCK.search(text).group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def batch_json(system: str, prompt: str, model: str | None = None,
               max_tokens: int = 1200) -> dict:
    """A single cached call that asks the model for a JSON object; returns {} on failure."""
    text = generate_cached(system, prompt, model=model, max_tokens=max_tokens,
                           temperature=0.3, json_mode=True)
    return parse_json_text(text)


def _chunks(items: list, size: int = 10):
    for i in range(0, len(items), size):
        yield items[i:i + size]


_CV_SIG = "BSc Maths & Statistics (Warwick) · Python/Pandas/NumPy/Sklearn · R GLMs · SQL · " \
          "Excel/VBA · financial maths & probability · SOA Exam P candidate · targeting " \
          "actuarial / quant-risk / data / BI analyst roles · fresh graduate"


def _candidate_brief(cv_text: str | None) -> str:
    """The candidate description fed to the fit scorer.

    Uses the real CV text so edits to cv_profile.txt actually change what the model sees;
    falls back to the built-in summary only when no CV text is available.
    """
    text = (cv_text or "").strip()
    return text[:2000] if text else _CV_SIG


def cv_signature(cv_text: str | None) -> str:
    """Stable short fingerprint of the CV text.

    Used both in cache keys and in the DB (fit_cv_sig) so AI fit scores are only recomputed
    when the CV actually changes.
    """
    return hashlib.sha1((cv_text or "").encode("utf-8")).hexdigest()[:12]


def sanitize_fit_results(results) -> dict:
    """Keep only AI fit entries that carry a usable numeric score (defensive vs bad JSON)."""
    clean = {}
    if not isinstance(results, dict):
        return clean
    for key, payload in results.items():
        if not isinstance(payload, dict):
            continue
        try:
            float(payload.get("fit_score"))
        except (TypeError, ValueError):
            continue
        clean[str(key)] = payload
    return clean


def fit_score_batch(items: list, cv_text: str | None = None, model: str | None = None,
                    batch_size: int = 10) -> dict:
    """AI fit scores for a list of {id, title, description}. Returns {id: {fit_score,
    matched_skills, missing_skills, actionable_improvements, reason}}.

    Results are persisted to the DB by the caller (fit_source='ai' + CV signature) so they
    never re-run until the CV changes. The real CV text is sent to the model (so a CV edit
    changes the scores) and is also part of the cache key.
    """
    out = {}
    sig = cv_signature(cv_text)
    system = ("You are a meticulous ATS recruiter. Score how well this exact candidate fits each "
              "job on 0-100. Be fair and specific, and respect that the candidate is a fresh grad "
              "(cap manager/VP/senior roles lower).\n"
              f"Candidate CV:\n{_candidate_brief(cv_text)}\nCV-signature: {sig}")
    for chunk in _chunks(items, batch_size):
        rows = [{
            "id": it["id"],
            "title": (it.get("title") or "")[:200],
            "description": (it.get("description") or "")[:1500],
        } for it in chunk]
        prompt = ("Return ONLY a JSON object mapping each id to: "
                  "{\"fit_score\": int 0-100, \"matched_skills\": [..], "
                  "\"missing_skills\": [..], \"actionable_improvements\": [2-3 strings], "
                  "\"reason\": \"one short sentence\"}. Jobs:\n" + json.dumps(rows, ensure_ascii=False))
        out.update(batch_json(system, prompt, model=model, max_tokens=2400))
    return out


def classify_batch(items: list, statuses: list | None = None, model: str | None = None,
                   batch_size: int = 10) -> dict:
    """AI email classification with memory of the thread's known state.

    items: [{id, sender, subject, snippet, existing:{company_name, role_title, current_status,
    job_description_snippet}}]. Returns {id: {is_valid, company, role, status, reason}}.

    The caller persists the verdict and marks ai_classified=1 so it never re-runs.
    """
    out = {}
    allowed = statuses or ["Saved / Planning", "Applied", "Assessment / OA", "Interview",
                           "Offer", "Rejected", "Ghosted"]
    system = (
        "You classify job-application emails for a personal tracker. Decide for EACH email: "
        "is it a real application event, and if so its company, role and status.\n"
        "STATUS DEFINITIONS:\n"
        "- Applied: the candidate applied and it was confirmed/received/submitted; also "
        "acknowledgements still under review.\n"
        "- Assessment / OA: an online test/assessment/coding challenge was sent (HackerRank, "
        "Codility, HireVue, TestGorilla, SHL, aptitude/technical test, screening questions).\n"
        "- Interview: an interview/call/chat is invited, scheduled or confirmed; shortlisted "
        "for an interview; hiring manager wants to speak.\n"
        "- Offer: an offer is made or an offer letter/verbal offer is mentioned positively.\n"
        "- Rejected: not selected, unsuccessful, not moving forward, position filled, no "
        "longer under consideration, silent-closure language.\n"
        "- Ghosted: only when the email itself says the employer stopped responding / the "
        "role closed without a decision (otherwise leave blank).\n"
        "- Saved / Planning: the candidate bookmarked/added the role but has not applied.\n"
        "VALID means the candidate applied (confirmation/submission/acknowledgement) OR an "
        "employer replied about their application. INVALID means job alerts, 'jobs you may "
        "like', recommended/similar jobs, digests, newsletters, career advice, events, salary "
        "reports, or unrelated marketing — even if they mention 'application' or 'interview'.\n"
        "RULES: (1) A marketing footer ('more jobs for you') does NOT make a genuine "
        "confirmation invalid. (2) If the email names a company and role, extract the exact "
        "employer (never a job board/ATS such as LinkedIn, JobStreet, Workday, myHR, Hiredly) "
        "and the exact job title. (3) 'known' is the existing thread memory: keep its company "
        "and role unless this email clearly changes them, and only advance the status when the "
        "email is a later reply on the same application; never downgrade a later, more "
        "advanced status. (4) Prefer the most advanced definite status in the email. "
        "(5) Be decisive: if it is clearly a real application event, mark valid."
    )
    for chunk in _chunks(items, batch_size):
        rows = [{
            "id": it["id"],
            "sender": (it.get("sender") or "")[:200],
            "subject": (it.get("subject") or "")[:200],
            "snippet": (it.get("snippet") or "")[:1500],
            "known": it.get("existing") or {},
        } for it in chunk]
        prompt = ("Return ONLY a JSON object mapping each id to: "
                  "{\"is_valid\": true/false, \"company\": \"\", \"role\": \"\", "
                  f"\"status\": one of {json.dumps(allowed)}, \"reason\": \"short\"}}. "
                  "Use \"\" for company/role/status when unknown or not applicable. "
                  f"Emails:\n{json.dumps(rows, ensure_ascii=False)}")
        out.update(batch_json(system, prompt, model=model, max_tokens=2400))
    return out


def digest(payload: object) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()