"""JD Lookup — finds the live posting for an applied job and extracts its description.

Strategy: one search per unique role (cheap — LinkedIn guest + JobStreet v5),
then fuzzy-match every tracked (role, company) pair against the candidate pool
so ~100 applications cost only a handful of network queries.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

import discovery

_MATCH_THRESHOLD = 0.52

_SUFFIX_RE = re.compile(
    r"\b(berhad|bhd|sdn|sdn bhd|ltd|limited|inc|llc|group|holdings|pte|pte ltd|"
    r"sdn\.? bhd\.|plc|corp|corporation)\b"
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def company_score(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    # strip corporate suffixes that inflate mismatch
    a = _SUFFIX_RE.sub(" ", a)
    b = _SUFFIX_RE.sub(" ", b)
    return SequenceMatcher(None, " ".join(a.split()), " ".join(b.split())).ratio()


def role_score(a: str, b: str) -> float:
    ta = set(_norm(a).split()) - {"the", "a", "an", "of", "and", "for", "at", "in"}
    tb = set(_norm(b).split()) - {"the", "a", "an", "of", "and", "for", "at", "in"}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def match_score(role_a: str, company_a: str, job: dict) -> float:
    return 0.6 * company_score(company_a, job.get("company", "")) + \
        0.4 * role_score(role_a, job.get("title", ""))


def fetch_candidates(role: str) -> list:
    """Search live listings for one role string (LinkedIn guest + JobStreet v5)."""
    cands = []
    for fn in (discovery.fetch_jobstreet, discovery.fetch_linkedin):
        try:
            cands.extend(fn(role, "Malaysia"))
        except Exception:
            continue
    time.sleep(0.2)
    return cands


def _usable_url(url: str) -> bool:
    u = (url or "").strip()
    if not u or len(u) < 25 or "/job/None" in u:
        return False
    if u.rstrip("/").endswith(("/job", "/jobs")):
        return False
    return True


def lookup(role: str, company: str, pool: list = None) -> dict | None:
    """Match (role, company) against a candidate pool (fetched if not given).

    Only candidates with a real posting URL are linkable, and a weak company match is
    never accepted (prevents repointing a job to a different company's listing). A hit is
    returned when it carries a usable URL and/or a description — URL-only matches are what
    repair broken posting links.
    """
    if pool is None:
        pool = fetch_candidates(role)
    best, best_score, best_company = None, 0.0, 0.0
    for job in pool:
        if not _usable_url(job.get("url", "")):
            continue
        cs = company_score(company, job.get("company", ""))
        rs = role_score(role, job.get("title", ""))
        s = 0.6 * cs + 0.4 * rs
        if s > best_score:
            best, best_score, best_company = job, s, cs
    if best is None or best_score < _MATCH_THRESHOLD or best_company < 0.45:
        return None
    description = (best.get("description") or "").strip()
    url = (best.get("url") or "").strip()
    if not description and not url:
        return None
    return {
        "url": url,
        "description": description,
        "source": best.get("source", ""),
        "title": best.get("title", ""),
        "company": best.get("company", ""),
        "match_score": round(best_score, 2),
    }


def lookup_batch(pairs: list, max_workers: int = 6, on_progress=None) -> dict:
    """pairs: [(thread_id, role, company)] → {thread_id: lookup result or None}.

    Groups by role text to minimise network queries.
    """
    by_role = {}
    for tid, role, company in pairs:
        key = " ".join(_norm(role).split()[:6])
        by_role.setdefault(key, {"role": role, "items": []})["items"].append((tid, company))

    pools = {}
    roles = list(by_role.values())
    done = 0

    def _fetch(role_entry):
        return fetch_candidates(role_entry["role"])

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch, role_entry): role_entry["role"] for role_entry in roles}
        for future in as_completed(futures):
            role = futures[future]
            done += 1
            try:
                pools[role] = future.result(timeout=60)
            except Exception:
                pools[role] = []
            if on_progress:
                try:
                    on_progress(done, len(roles))
                except Exception:
                    pass

    results = {}
    for role_entry in roles:
        pool = pools.get(role_entry["role"], [])
        for tid, company in role_entry["items"]:
            try:
                results[tid] = lookup(role_entry["role"], company, pool=pool)
            except Exception:
                results[tid] = None
    return results
