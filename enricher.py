import html
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 4
MAX_BYTES = 2_500_000

BLOCKED_STATUSES = {401, 403, 429, 999, 418}

_session = requests.Session()
_session.headers.update(HEADERS)
_retry = Retry(total=1, connect=1, read=0, backoff_factor=0.3,
               status_forcelist=(500, 502, 503, 504), allowed_methods=frozenset(["GET"]))
_adapter = HTTPAdapter(max_retries=_retry, pool_connections=16, pool_maxsize=16)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

# Never fetch loopback / private / link-local hosts (email links are untrusted input).
_PRIVATE_HOST_RE = re.compile(
    r"^(?:localhost|0\.|127\.|10\.|169\.254\.|192\.168\.|"
    r"172\.(?:1[6-9]|2\d|3[01])\.|\[?::1\]?$)",
    re.IGNORECASE,
)

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

_JSONLD_TAG = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _is_public_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip()
    if not host or "." not in host:
        return False
    return not _PRIVATE_HOST_RE.match(host)


def _clean_text(value: str) -> str:
    text = _HTML_TAG_RE.sub(" ", value or "")
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _meta_content(tags: list, wanted: str) -> str:
    """Content of the first <meta> whose property/name equals `wanted` (either attr order)."""
    for tag in tags:
        attrs = {m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or "")
                 for m in _META_ATTR_RE.finditer(tag)}
        prop = (attrs.get("property") or attrs.get("name") or "").lower()
        if prop == wanted:
            return attrs.get("content", "")
    return ""


def fetch_page(url: str):
    if not _is_public_url(url):
        return None, "blocked (non-public url)", url
    try:
        response = _session.get(url, timeout=(3.05, TIMEOUT), allow_redirects=True, stream=True)
    except requests.exceptions.Timeout:
        return None, "timeout", url
    except requests.exceptions.SSLError:
        return None, "ssl error", url
    except requests.exceptions.RequestException as exc:
        return None, f"request failed ({type(exc).__name__})", url
    except Exception as exc:
        return None, f"unexpected ({type(exc).__name__})", url

    try:
        if response.status_code in BLOCKED_STATUSES:
            return None, f"blocked ({response.status_code})", url
        if response.status_code != 200:
            return None, f"http {response.status_code}", url
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return None, f"non-html ({content_type.split(';')[0]})", url
        raw = response.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(raw) > MAX_BYTES:
            return None, "page too large", url
        encoding = response.encoding or response.apparent_encoding or "utf-8"
        return raw.decode(encoding, errors="replace"), None, response.url
    except requests.exceptions.RequestException as exc:
        return None, f"read failed ({type(exc).__name__})", url
    except Exception as exc:
        return None, f"unexpected ({type(exc).__name__})", url
    finally:
        response.close()


def extract_og(html_text: str) -> dict:
    result = {}
    if not html_text:
        return result
    tags = _META_TAG_RE.findall(html_text)
    title = _clean_text(_meta_content(tags, "og:title"))
    description = _clean_text(_meta_content(tags, "og:description"))
    if title:
        result["og_title"] = title
    else:
        match = _TITLE_RE.search(html_text)
        if match:
            result["title"] = _clean_text(match.group(1))[:150]
    if description:
        result["og_description"] = description
    return result


# ---------------------------------------------------------------- JSON-LD (schema.org JobPosting)

def _jsonld_blocks(html: str) -> list:
    """All JobPosting objects embedded in <script type="application/ld+json"> blocks."""
    found = []

    def walk(value):
        if isinstance(value, dict):
            typ = value.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if "JobPosting" in types:
                found.append(value)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for match in _JSONLD_TAG.finditer(html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        try:
            walk(data)
        except Exception:
            continue
    return found


def _ld_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("@value", "name", "text", "value"):
            got = _ld_text(value.get(key))
            if got:
                return got
        return ""
    if isinstance(value, list):
        return " ".join(_ld_text(v) for v in value)
    return str(value)


def _ld_salary(job: dict) -> str:
    """Human-readable salary from schema.org MonetaryAmount (direct or nested value)."""
    base = job.get("baseSalary") or {}
    if not isinstance(base, dict):
        return ""
    currency = _ld_text(base.get("currency"))

    def _num(v):
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, list):
            v = v[0] if v else None
        return v

    minv = _num(base.get("minValue"))
    maxv = _num(base.get("maxValue"))
    value = base.get("value")
    if isinstance(value, dict):
        if minv is None:
            minv = _num(value.get("minValue"))
        if maxv is None:
            maxv = _num(value.get("maxValue"))
        inner = value.get("value")
        value = inner if isinstance(inner, (int, float)) else None
    if minv is not None and maxv is not None:
        return f"{minv}–{maxv} {currency}".strip()
    if isinstance(value, (int, float)):
        return f"{value} {currency}".strip()
    return ""


def extract_jsonld(html: str) -> dict:
    """Structured job facts from a page's schema.org JobPosting JSON-LD (used heavily by
    Workday / SuccessFactors / most ATS pages, which ship no OpenGraph tags)."""
    if not html:
        return {}
    best = None
    for job in _jsonld_blocks(html):
        desc = _ld_text(job.get("description"))
        if best is None or len(desc) > len(best.get("description", "")):
            best = job
    if best is None:
        return {}

    clean = _clean_text

    locations = best.get("jobLocation") or []
    loc_texts = []
    if isinstance(locations, dict):
        locations = [locations]
    for loc in locations:
        addr = loc.get("address") or {}
        city = _ld_text(addr.get("addressLocality") if isinstance(addr, dict) else "")
        region = _ld_text(addr.get("addressRegion") if isinstance(addr, dict) else "")
        country = _ld_text(addr.get("addressCountry") if isinstance(addr, dict) else "")
        if isinstance(country, dict):
            country = _ld_text(country.get("name"))
        if city:
            loc_texts.append(" ".join(x for x in (city, region, country) if x))
        else:
            n = _ld_text(loc.get("name"))
            if n:
                loc_texts.append(n)

    org = best.get("hiringOrganization") or {}
    if isinstance(org, dict):
        org = org.get("name")
    emp = best.get("employmentType") or ""
    if isinstance(emp, list):
        emp = " / ".join(str(e) for e in emp)
    return {
        "title": clean(_ld_text(best.get("title"))),
        "company": clean(_ld_text(org)),
        "description": clean(best.get("description"))[:4000],
        "location": clean("; ".join(loc_texts))[:200],
        "salary": _ld_salary(best),
        "posted_date": clean(_ld_text(best.get("datePosted")))[:20],
        "employment_type": clean(str(emp))[:120],
    }


def enrich(url: str) -> dict:
    page_html, error, final_url = fetch_page(url)
    if page_html is None:
        return {"ok": False, "reason": error, "og_title": "", "og_description": ""}
    tags = extract_og(page_html)
    ld = extract_jsonld(page_html)
    title = (tags.get("og_title") or ld.get("title") or tags.get("title", "")).strip()
    og_desc = tags.get("og_description", "").strip()
    ld_desc = ld.get("description", "").strip()
    # prefer the richer description (JSON-LD is usually the full JD; og is a short summary)
    description = ld_desc if len(ld_desc) >= len(og_desc) else og_desc
    if not title and not description:
        return {"ok": False, "reason": "no og/jsonld tags found", "og_title": "", "og_description": ""}
    result = {
        "ok": True,
        "reason": "",
        "og_title": title,
        "og_description": description,
        "url": final_url or url,
    }
    for key in ("company", "location", "salary", "posted_date", "employment_type"):
        val = (ld.get(key) or "").strip()
        if val:
            result[key] = val
    return result


def enrich_many(jobs: dict, max_workers: int = 16) -> dict:
    results = {}
    if not jobs:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich, url): thread_id for thread_id, url in jobs.items()}
        for future in as_completed(futures):
            thread_id = futures[future]
            try:
                results[thread_id] = future.result(timeout=TIMEOUT + 1)
            except Exception as exc:
                results[thread_id] = {"ok": False, "reason": f"thread error ({exc})", "og_title": "", "og_description": ""}
    return results