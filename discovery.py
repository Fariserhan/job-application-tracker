"""Live Malaysian Job Market Discovery â€” keyless public feeds (LinkedIn, JobStreet, Hiredly, Remotive)."""
import hashlib
import html as html_lib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import matcher
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 4
MAX_WORKERS = 16
PER_PROVIDER_RESULTS = 20

TARGET_LOCATIONS = ["Malaysia", "Kuala Lumpur", "Selangor"]

_MY_TEXT = re.compile(r"malaysia|kuala lumpur|\bkl\b|selangor|petaling|subang|damansara|cyberjaya|putrajaya|penang|johor", re.IGNORECASE)
_REMOTE_TEXT = re.compile(r"remote|worldwide|anywhere|flexible", re.IGNORECASE)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# requests.Session is not guaranteed thread-safe â€” one session per worker thread.
_local = threading.local()


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _local.session = session
    return session


def _clean(text: str) -> str:
    text = _TAG_RE.sub("", text or "")
    text = html_lib.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def job_key(source: str, url: str, title: str = "") -> str:
    raw = f"{source}|{url or title}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- providers

def _linkedin_cards(html: str) -> list:
    """Slice the guest-search HTML into whole job cards.

    Cards are `<div class="... base-search-card ... base-search-card--link job-search-card">`
    containers that also hold the title/location/link/urn. Naive splitting on the substring
    `base-search-card` is WRONG: `base-search-card__title` contains the same substring, which
    would tear each card in half and separate the posting link from the title.
    """
    markers = [m.start() for m in re.finditer(r"base-search-card--link", html)]
    if not markers:
        markers = [m.start() for m in re.finditer(
            r'<div[^>]*class="[^"]*base-search-card[^"]*"[^>]*>', html)]
    if not markers:
        return []
    return [html[markers[i]:markers[i + 1]] for i in range(len(markers) - 1)] + [html[markers[-1]:]]


def fetch_linkedin(role: str, location: str) -> list:
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    time.sleep(0.35)  # stagger bursts so the guest endpoint does not 429
    resp = _session().get(url, params={"keywords": role, "location": location, "start": 0},
                        timeout=TIMEOUT)
    if resp.status_code != 200:
        time.sleep(1.5)
        resp = _session().get(url, params={"keywords": role, "location": location, "start": 0},
                            timeout=TIMEOUT)
        if resp.status_code != 200:
            return []
    html = resp.text
    jobs = []
    for card in _linkedin_cards(html)[:PER_PROVIDER_RESULTS]:
        title_m = re.search(r"base-search-card__title[^>]*>\s*(.*?)\s*</h3>", card, re.DOTALL)
        if not title_m:
            continue
        title = _clean(title_m.group(1))
        company_m = re.search(r"base-search-card__subtitle.*?>(?:\s*<[^>]+>\s*)?([^<>]+?)\s*</(?:a|h4)>", card, re.DOTALL)
        company = _clean(company_m.group(1)) if company_m else "Unknown"
        loc_m = re.search(r"job-search-card__location[^>]*>\s*(.*?)\s*</span>", card, re.DOTALL)
        location_txt = _clean(loc_m.group(1)) if loc_m else location
        link_m = re.search(r"href=\"(https://[^\"']*?/jobs/view/[^\"']+?)\"", card)
        urn_m = re.search(r"data-entity-urn=\"urn:li:jobPosting:(\d+)\"", card)
        if link_m:
            link = link_m.group(1).split("?")[0].replace("&amp;", "&")
        elif urn_m:
            link = f"https://www.linkedin.com/jobs/view/{urn_m.group(1)}"
        else:
            link = ""
        link = link.replace("my.linkedin.com", "www.linkedin.com")
        date_m = re.search(r"datetime=\"(\d{4}-\d{2}-\d{2})", card)
        if not title:
            continue
        jobs.append({
            "title": title,
            "company": company or "Unknown",
            "location": location_txt,
            "source": "LinkedIn",
            "url": link,
            "description": title,
            "posted_date": date_m.group(1) if date_m else "",
        })
    return jobs


def fetch_jobstreet(role: str, location: str) -> list:
    time.sleep(0.2)
    url = "https://www.jobstreet.com.my/api/jobsearch/v5/search"
    try:
        resp = _session().get(url, params={
            "siteKey": "MY-MY",
            "keyword": role,
            "pageSize": PER_PROVIDER_RESULTS,
            "sortMode": "relevance",
        }, timeout=TIMEOUT)
        if resp.status_code != 200:
            return []
        data = resp.json().get("data", [])
    except (requests.exceptions.RequestException, ValueError):
        return []
    jobs = []
    for item in data:
        locations = item.get("locations") or []
        if not any((loc.get("countryCode") == "MY") or _MY_TEXT.search(loc.get("label") or "")
                   for loc in locations):
            continue
        teaser = _clean(item.get("teaser") or "")
        bullets = " ".join(_clean(b) for b in (item.get("bulletPoints") or [])[:3])
        loc_label = locations[0].get("label", "") if locations else location
        jobs.append({
            "title": _clean(item.get("title") or ""),
            "company": _clean(item.get("companyName") or (item.get("advertiser") or {}).get("description") or "Unknown"),
            "location": loc_label or "Malaysia",
            "source": "JobStreet",
            "url": f"https://www.jobstreet.com.my/job/{item.get('id', '')}",
            "description": f"{item.get('title', '')}. {teaser} {bullets}".strip(),
            "posted_date": (item.get("listingDate") or "")[:10],
        })
    return jobs


def fetch_hiredly(role: str, location: str) -> list:
    time.sleep(0.2)
    url = "https://www.hiredly.com.my/api/v1/jobs"
    try:
        resp = _session().get(url, params={"keyword": role, "country": "my", "limit": PER_PROVIDER_RESULTS},
                            timeout=TIMEOUT, allow_redirects=False)
        if resp.status_code != 200 or not resp.text.strip().startswith(("{", "[")):
            return []
        data = resp.json()
        items = data.get("jobs", data.get("results", data if isinstance(data, list) else []))
        jobs = []
        for item in items[:PER_PROVIDER_RESULTS]:
            if not isinstance(item, dict):
                continue
            jobs.append({
                "title": _clean(item.get("title") or item.get("name") or ""),
                "company": _clean(item.get("company", {}).get("name") if isinstance(item.get("company"), dict)
                                  else item.get("company_name") or "Unknown"),
                "location": _clean(item.get("location") or "Malaysia") or "Malaysia",
                "source": "Hiredly",
                "url": item.get("url") or item.get("job_url") or "",
                "description": _clean(item.get("description") or item.get("snippet") or item.get("title") or ""),
                "posted_date": (item.get("posted_at") or item.get("created_at") or "")[:10],
            })
        return [j for j in jobs if j["title"]]
    except requests.exceptions.RequestException:
        return []
    except ValueError:
        return []


def fetch_remotive(role: str, location: str) -> list:
    time.sleep(0.2)
    url = "https://remotive.com/api/remote-jobs"
    try:
        resp = _session().get(url, params={"search": role, "limit": PER_PROVIDER_RESULTS},
                              timeout=TIMEOUT)
        if resp.status_code != 200:
            return []
        data = resp.json().get("jobs", [])
    except (requests.exceptions.RequestException, ValueError):
        return []
    jobs = []
    for item in data:
        loc = item.get("candidate_required_location") or "Remote"
        if not (_MY_TEXT.search(loc) or _REMOTE_TEXT.search(loc)):
            continue
        desc = _clean(re.sub(r"(?s)<[^>]+>", " ", item.get("description") or ""))[:600]
        jobs.append({
            "title": _clean(item.get("title") or ""),
            "company": _clean(item.get("company_name") or "Unknown"),
            "location": loc,
            "source": "Remotive",
            "url": item.get("url") or "",
            "description": desc or item.get("title", ""),
            "posted_date": (item.get("publication_date") or "")[:10],
        })
    return jobs


PROVIDERS = [
    ("LinkedIn", fetch_linkedin),
    ("JobStreet", fetch_jobstreet),
    ("Hiredly", fetch_hiredly),
    ("Remotive", fetch_remotive),
]


# ---------------------------------------------------------------- scan

def _og_enrich_job(job: dict) -> dict:
    """Fetch the job page's OpenGraph tags to fill in a real JD for GitHub-card
    style listings (LinkedIn cards ship without descriptions)."""
    try:
        import enricher
        desc = job.get("description") or ""
        if len(desc) > 140 or not job.get("url"):
            return job
        payload = enricher.enrich(job["url"])
        if payload.get("ok"):
            title = (payload.get("og_title") or "").strip()
            body = (payload.get("og_description") or "").strip()
            combined = f"{title} â€” {body}".strip(" â€”")
            if len(combined) > len(desc):
                job["description"] = combined[:900]
            analysis = matcher.analyze(job["title"], job["description"])
            job["fit_score"] = analysis["fit_score"]
            job["matched_skills"] = analysis["matched_skills"]
            job["missing_skills"] = analysis["missing_skills"]
    except Exception:
        pass
    return job


def scan(roles=None, locations=None, on_progress=None, enrich_og: bool = True) -> dict:
    roles = roles or ["Actuarial Analyst", "Risk Analyst", "Data Analyst", "Business Intelligence Analyst"]
    locations = locations or TARGET_LOCATIONS
    tasks = []
    for source_name, fn in PROVIDERS:
        for role in roles:
            for loc in locations:
                tasks.append((source_name, role, loc, fn))

    jobs = []
    source_status = {name: 0 for name, _ in PROVIDERS}
    errors = {}
    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fn, role, loc): (source_name, role, loc)
                   for source_name, role, loc, fn in tasks}
        for future in as_completed(futures):
            source_name, role, loc = futures[future]
            done += 1
            try:
                results = future.result(timeout=TIMEOUT + 2)
            except Exception as exc:
                errors[source_name] = str(exc)[:60]
                results = []
            source_status[source_name] += len(results)
            for job in results:
                job["search_role"] = role
                jobs.append(job)
            if on_progress:
                try:
                    on_progress(done, len(tasks), f"{source_name}: {role} / {loc}")
                except Exception:
                    pass

    deduped = {}
    for job in jobs:
        key = job_key(job["source"], job["url"], job["title"])
        if key in deduped:
            continue
        job["job_key"] = key
        analysis = matcher.analyze(job["title"], job["description"])
        job["fit_score"] = analysis["fit_score"]
        job["matched_skills"] = analysis["matched_skills"]
        job["missing_skills"] = analysis["missing_skills"]
        deduped[key] = job

    ranked = sorted(deduped.values(), key=lambda j: j["fit_score"], reverse=True)

    if enrich_og:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            ranked = list(pool.map(_og_enrich_job, ranked))
        ranked.sort(key=lambda j: j["fit_score"], reverse=True)

    return {
        "jobs": ranked,
        "elapsed": time.time() - started,
        "source_counts": {k: v for k, v in source_status.items()},
        "errors": errors,
    }


if __name__ == "__main__":
    result = scan()
    print(f"found {len(result['jobs'])} jobs in {result['elapsed']:.1f}s | sources: {result['source_counts']} | errors: {result['errors']}")
    for job in result["jobs"][:10]:
        print(f"  {job['fit_score']:5.1f}% | {job['source']:<10} | {job['title'][:45]:<45} | {job['company'][:25]}")
