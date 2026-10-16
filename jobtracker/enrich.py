"""Job-posting enrichment: title, description, location, salary from the posting link.

Reads OpenGraph tags and schema.org ``JobPosting`` JSON-LD (Workday, SuccessFactors and
most ATS pages ship JSON-LD but no OpenGraph).

Email links are untrusted input, so fetching is SSRF-guarded properly: every hop of a
redirect chain is re-validated, and the hostname is *resolved* and every address checked
(``ipaddress.is_global``). A regex on the hostname alone is bypassed by DNS names that
point at 127.0.0.1 or the cloud metadata address.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = (3.05, 5)
MAX_BYTES = 2_500_000
MAX_REDIRECTS = 4
BLOCKED_STATUSES = {401, 403, 429, 999, 418}

_META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_JSONLD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                     re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _host_is_public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        return False
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def is_public_url(url: str, resolve: bool = True) -> bool:
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").strip().rstrip(".")
    if not host or "." not in host or host.lower() == "localhost":
        return False
    try:  # literal IPs are checked directly
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    return _host_is_public(host) if resolve else True


def _clean(value: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", value or ""))).strip()


def fetch_page(url: str, session=None) -> tuple[str | None, str, str]:
    """(html | None, error, final_url). Follows redirects manually, validating each hop."""
    import requests

    session = session or requests.Session()
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(current):
            return None, "blocked (non-public url)", current
        try:
            response = session.get(current, timeout=TIMEOUT, allow_redirects=False, stream=True,
                                   headers={"User-Agent": USER_AGENT,
                                            "Accept": "text/html,application/xhtml+xml"})
        except requests.exceptions.Timeout:
            return None, "timeout", current
        except requests.exceptions.RequestException as exc:
            return None, f"request failed ({type(exc).__name__})", current
        try:
            if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location:
                    return None, "redirect without location", current
                current = urllib.parse.urljoin(current, location)
                continue
            if response.status_code in BLOCKED_STATUSES:
                return None, f"blocked ({response.status_code})", current
            if response.status_code != 200:
                return None, f"http {response.status_code}", current
            ctype = response.headers.get("content-type", "")
            if "text/html" not in ctype and "text/plain" not in ctype:
                return None, f"non-html ({ctype.split(';')[0]})", current
            raw = response.raw.read(MAX_BYTES + 1, decode_content=True)
            if len(raw) > MAX_BYTES:
                return None, "page too large", current
            encoding = response.encoding or "utf-8"
            return raw.decode(encoding, errors="replace"), "", current
        except Exception as exc:
            return None, f"read failed ({type(exc).__name__})", current
        finally:
            response.close()
    return None, "too many redirects", current


def _meta_content(tags: list[str], wanted: str) -> str:
    for tag in tags:
        attrs = {m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or "")
                 for m in _META_ATTR.finditer(tag)}
        if (attrs.get("property") or attrs.get("name") or "").lower() == wanted:
            return attrs.get("content", "")
    return ""


def extract_og(page: str) -> dict:
    out: dict[str, str] = {}
    if not page:
        return out
    tags = _META_TAG.findall(page)
    title = _clean(_meta_content(tags, "og:title"))
    desc = _clean(_meta_content(tags, "og:description"))
    if title:
        out["og_title"] = title
    else:
        match = _TITLE.search(page)
        if match:
            out["title"] = _clean(match.group(1))[:150]
    if desc:
        out["og_description"] = desc
    return out


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


def _job_postings(page: str) -> list[dict]:
    found: list[dict] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            if "JobPosting" in (kind if isinstance(kind, list) else [kind]):
                found.append(value)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    for match in _JSONLD.finditer(page or ""):
        try:
            walk(json.loads(match.group(1).strip()))
        except (ValueError, RecursionError):
            continue
    return found


def _salary(job: dict) -> str:
    base = job.get("baseSalary") or {}
    if not isinstance(base, dict):
        return ""
    currency = _ld_text(base.get("currency"))

    def num(v):
        if isinstance(v, dict):
            v = v.get("value")
        if isinstance(v, list):
            v = v[0] if v else None
        return v

    value = base.get("value")
    low, high = num(base.get("minValue")), num(base.get("maxValue"))
    if isinstance(value, dict):
        low = low if low is not None else num(value.get("minValue"))
        high = high if high is not None else num(value.get("maxValue"))
        unit = value.get("unitText") or ""
        value = value.get("value") if isinstance(value.get("value"), (int, float)) else None
    else:
        unit = ""
    suffix = f" {currency}".rstrip() + (f" / {unit.lower()}" if unit else "")
    if low is not None and high is not None:
        return f"{low}–{high}{suffix}".strip()
    if isinstance(value, (int, float)):
        return f"{value}{suffix}".strip()
    return ""


def extract_jsonld(page: str) -> dict:
    postings = _job_postings(page)
    if not postings:
        return {}
    best = max(postings, key=lambda j: len(_ld_text(j.get("description"))))
    locations = best.get("jobLocation") or []
    if isinstance(locations, dict):
        locations = [locations]
    loc_texts = []
    for loc in locations if isinstance(locations, list) else []:
        addr = loc.get("address") if isinstance(loc, dict) else None
        if isinstance(addr, dict):
            parts = [_ld_text(addr.get(k)) for k in ("addressLocality", "addressRegion",
                                                     "addressCountry")]
            text = ", ".join(p for p in parts if p)
            if text:
                loc_texts.append(text)
    org = best.get("hiringOrganization") or {}
    emp = best.get("employmentType") or ""
    if isinstance(emp, list):
        emp = " / ".join(str(e) for e in emp)
    return {
        "title": _clean(_ld_text(best.get("title"))),
        "company": _clean(_ld_text(org.get("name") if isinstance(org, dict) else org)),
        "description": _clean(_ld_text(best.get("description")))[:4000],
        "location": _clean("; ".join(loc_texts))[:200],
        "salary": _salary(best),
        "posted_date": _clean(_ld_text(best.get("datePosted")))[:10],
        "employment_type": _clean(str(emp)).replace("_", " ").title()[:120],
    }


def enrich(url: str, session=None) -> dict:
    page, error, final_url = fetch_page(url, session)
    if page is None:
        return {"ok": False, "reason": error}
    og = extract_og(page)
    ld = extract_jsonld(page)
    title = (og.get("og_title") or ld.get("title") or og.get("title") or "").strip()
    og_desc = og.get("og_description", "").strip()
    ld_desc = ld.get("description", "").strip()
    description = ld_desc if len(ld_desc) >= len(og_desc) else og_desc
    if not title and not description:
        return {"ok": False, "reason": "no og/jsonld tags found"}
    result = {"ok": True, "reason": "", "title": title, "description": description,
              "url": final_url or url}
    for key in ("company", "location", "salary", "posted_date", "employment_type"):
        if ld.get(key):
            result[key] = ld[key]
    return result


def enrich_many(jobs: dict[str, str], max_workers: int = 8) -> dict[str, dict]:
    import requests

    results: dict[str, dict] = {}
    if not jobs:
        return results
    session = requests.Session()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(enrich, url, session): tid for tid, url in jobs.items()}
        for future in as_completed(futures):
            tid = futures[future]
            try:
                results[tid] = future.result()
            except Exception as exc:  # never let one page fail the batch
                results[tid] = {"ok": False, "reason": f"error ({type(exc).__name__})"}
    return results
