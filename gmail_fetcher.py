import base64
import html as html_lib
import os
import random
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ------------------------------------------------------------------ search query
#
# Gmail's q syntax is a conjunction of OR-groups. To avoid missing real application
# mail we match on THREE independent signals:
#   1. sender domains  — the job boards / ATS platforms applications are sent through
#   2. subject terms   — application/interview/assessment wording
#   3. body phrases    — so employer-hosted ATS domains we cannot enumerate still match
# Then we subtract the known marketing templates. Spam is INCLUDED (`in:anywhere
# -in:trash`) because Gmail occasionally misroutes real confirmations there; the
# deterministic + AI filters still remove genuine noise.
SEARCH_SENDER_DOMAINS = [
    # job boards / professional networks
    "linkedin.com", "jobstreet.com", "hiredly.com", "prosple.com", "wobb.com",
    "jobstore.com", "jobsdb.com", "jobstreet-mail.com", "indeed.com", "glassdoor.com",
    # applicant tracking / recruiting platforms (incl. Workday/SuccessFactors tenants)
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkday.com",
    "myworkdaysite.com", "smartrecruiters.com", "icims.com", "taleo.net", "jobvite.com",
    "successfactors.com", "sapsf.com", "workable.com", "bamboohr.com", "applytojob.com",
    "recruitee.com", "jobscore.com", "teamtailor.com", "breezy.hr", "rolp.co",
    "oraclecloud.com", "mycareerx.com", "hrmos.com", "pageuppeople.com", "avature.net",
    "phenompeople.com", "eightfold.ai", "cornerstoneondemand.com", "brassring.com",
    "kenexa.com", "njoyn.com", "erecruit.com", "zohorecruit.com",
    # assessments / interviewing vendors
    "hirevue.com", "hackerrank.com", "codility.com", "testgorilla.com", "shl.com",
    "criteria.com", "pymetrics.com", "codingame.com", "hackerearth.com", "devskiller.com",
    "imocha.io", "vidcruiter.com", "sparkhire.com", "berke.com", "wonderlic.com",
    "predictiveindex.com", "thomas.co", "codility-mail.com",
]

SEARCH_SUBJECT_TERMS = [
    "application", "applied", "applying", "interview", "assessment", "offer",
    "regret", "unsuccessful", "shortlist", "shortlisted", "candidate", "resume",
    "cv", "talent", "recruit", "hiring", "vacancy", "position", "next steps",
    "thank you for", "not moving forward", "application update", "candidate home",
]

SEARCH_BODY_PHRASES = [
    "thank you for applying", "thanks for applying",
    "your application has been received", "we received your application",
    "application has been submitted", "your application was submitted",
    "invitation to interview", "invite you to an interview",
    "online assessment", "complete the assessment",
    "we regret to inform", "not moving forward",
    "application update", "candidate home", "shortlisted for",
]

SEARCH_EXCLUDE_SUBJECTS = [
    "job alert", "jobs you may like", "recommended jobs", "job recommendations",
    "weekly digest", "daily digest", "quora", "newsletter", "career advice",
    "jobs picked for you", "jobs for you", "new jobs for you", "career fair",
    "webinar", "salary report", "salary guide",
]


def _gmail_or(values) -> str:
    return " OR ".join(f'"{v}"' if " " in v else v for v in values)


# Two independent searches are unioned by search_messages(). Running senders and
# text-signals separately keeps every query comfortably inside Gmail's search-length
# limit, and neither can crowd the other out of the result cap.
_SEARCH_SCOPE = "in:anywhere -in:trash"
_SEARCH_EXCLUDE = f"-subject:({_gmail_or(SEARCH_EXCLUDE_SUBJECTS)})"
SEARCH_QUERIES = [
    f"{_SEARCH_SCOPE} (from:({_gmail_or(SEARCH_SENDER_DOMAINS)})) {_SEARCH_EXCLUDE}",
    f"{_SEARCH_SCOPE} (subject:({_gmail_or(SEARCH_SUBJECT_TERMS)}) "
    f"OR {_gmail_or(SEARCH_BODY_PHRASES)}) {_SEARCH_EXCLUDE}",
]
# Human-readable view of the full search (tests/docs); search_messages uses SEARCH_QUERIES.
SEARCH_QUERY = "\n".join(SEARCH_QUERIES)

# Only pull mail from this date onwards (the user started applying in 2026). Override with GMAIL_AFTER.
GMAIL_AFTER = os.environ.get("GMAIL_AFTER", "2026/01/01")

STATE_PATH = "sync_state.json"

# Total messages pulled from one sync (paginated 500 at a time). Large enough that a first
# full scan cannot silently truncate the mailbox; override with GMAIL_MAX_RESULTS.
MAX_SEARCH_RESULTS = max(100, int(os.environ.get("GMAIL_MAX_RESULTS", "2000")))

BATCH_SIZE = 50
QUOTA_UNITS_PER_MSG_GET = 5  # users.messages.get = 5 quota units
# Gmail default per-user budget: 250 quota units/second.
UNITS_PER_SEC = float(os.environ.get("GMAIL_UNITS_PER_SEC", "250"))
_legacy = os.environ.get("GMAIL_QUOTA_UNITS_PER_MIN")
if _legacy:
    UNITS_PER_SEC = float(_legacy) / 60.0
PARALLEL_BATCHES = max(1, int(os.environ.get("GMAIL_PARALLEL_BATCHES", "3")))
# Gmail executes batch sub-requests serially server-side (~10s per 50-batch), so
# 3 parallel lanes on independent HTTP connections cut wall time ~3-5x. Disable
# via GMAIL_PARALLEL_BATCHES=1 on networks with TLS-intercepting proxies.


class AdaptivePacer:
    """Thread-safe shared rate limiter.

    Spacing derives from the Gmail per-user quota (batch of 50 messages.get
    costs 250 units). Starts at the quota-safe interval, decays ~5% per clean
    request, and backs off instantly on any 403/429/5xx — replacing the old
    fixed 1.5s pacing + minute-long exponential retry loops.
    """

    def __init__(self, interval: float, floor: float, ceiling: float = 8.0):
        self.interval = interval
        self.floor = floor
        self.ceiling = ceiling
        self._last = time.monotonic() - interval  # first request fires immediately
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.monotonic()
            slot = max(self._last, now - 0.001) + self.interval
            delay = max(0.0, slot - now)
            self._last = slot
        if delay > 0:
            time.sleep(delay)

    def success(self):
        with self._lock:
            self.interval = max(self.floor, self.interval * 0.95)

    def throttle(self):
        with self._lock:
            self.interval = min(self.ceiling, max(self.interval * 2.0, 1.2))


_BASE_INTERVAL = QUOTA_UNITS_PER_MSG_GET * BATCH_SIZE / max(UNITS_PER_SEC, 1.0)
_pacer = AdaptivePacer(
    interval=max(_BASE_INTERVAL, 0.35),
    floor=max(_BASE_INTERVAL * 0.75, 0.25),
)


def build_service(creds):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _clone_service(service):
    """Build an independent service (own HTTP connection) for parallel batches."""
    try:
        creds = getattr(getattr(service, "_http", None), "credentials", None)
        if creds is None:
            return None
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception:
        return None


def execute(request, max_retries: int = 4):
    delay = 0.8
    for attempt in range(max_retries + 1):
        _pacer.wait()
        try:
            response = request.execute()
            _pacer.success()
            return response
        except HttpError as exc:
            status = exc.resp.status
            if status in (403, 429, 500, 503) and attempt < max_retries:
                _pacer.throttle()
                time.sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 2, 5)
                continue
            raise
        except (ssl.SSLError, ConnectionError, TimeoutError, OSError) as exc:
            # Transient TLS/network blips (ISP/VPN/proxy). Retry a couple of times with a
            # short backoff, then give up quickly so a flaky network never hangs the whole sync.
            if attempt < 2:
                time.sleep(min(1.5 + attempt, 3) + random.uniform(0, 0.3))
                continue
            raise
    raise RuntimeError("unreachable")


def get_profile(service):
    return execute(service.users().getProfile(userId="me"))


def search_messages(service, after_epoch: Optional[int] = None, max_results: Optional[int] = None):
    """All matching message ids across the sender + text-signal searches (deduplicated).

    `max_results` caps the union (default MAX_SEARCH_RESULTS). When the cap is hit the
    caller can warn that the mailbox was truncated. `after_epoch` is only used to derive
    the after: date; callers should pass a *watermark minus overlap* so transient
    per-message failures can never be skipped past.
    """
    cap = int(max_results or MAX_SEARCH_RESULTS)
    after_dates = [GMAIL_AFTER]
    if after_epoch:
        epoch = float(after_epoch)
        if epoch > 1e12:  # internalDate is milliseconds; fromtimestamp wants seconds
            epoch /= 1000.0
        after_date = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y/%m/%d")
        after_dates.append(after_date)

    seen = set()
    ordered = []
    for base_query in SEARCH_QUERIES:
        query = f"{base_query} after:{max(after_dates)}"
        page_token = None
        while True:
            response = execute(
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=500, pageToken=page_token)
            )
            for message in response.get("messages", []):
                mid = message["id"]
                if mid not in seen:
                    seen.add(mid)
                    ordered.append(mid)
            page_token = response.get("nextPageToken")
            if not page_token or len(ordered) >= cap:
                break
        if len(ordered) >= cap:
            break
    return ordered[:cap]


def _batch_fetch(service, build_request, message_ids: list) -> dict:
    """Fetch in 50/batch chunks, up to PARALLEL_BATCHES chunks concurrently.

    The shared AdaptivePacer keeps every request inside the Gmail per-user
    quota; parallelism hides network latency instead of burning quota.
    """
    results = {}
    chunks = [message_ids[i:i + BATCH_SIZE] for i in range(0, len(message_ids), BATCH_SIZE)]
    if not chunks:
        return results
    if len(chunks) == 1 or PARALLEL_BATCHES == 1:
        for chunk in chunks:
            _fetch_chunk(service, build_request, chunk, results)
        return results

    clones = []
    for _ in range(min(PARALLEL_BATCHES, len(chunks))):
        clone = _clone_service(service)
        if clone is None:
            clones = []
            break
        clones.append(clone)

    if not clones:
        for chunk in chunks:
            _fetch_chunk(service, build_request, chunk, results)
        return results

    failed = []
    with ThreadPoolExecutor(max_workers=len(clones)) as pool:
        futures = {
            pool.submit(_fetch_chunk, clones[i % len(clones)], build_request, chunk, results): chunk
            for i, chunk in enumerate(chunks)
        }
        for future, chunk in futures.items():
            try:
                future.result()
            except Exception as exc:
                print(f"[batch] worker failed ({type(exc).__name__}), retrying chunk sequentially")
                failed.append(chunk)
    for chunk in failed:
        _fetch_chunk(service, build_request, chunk, results)
    return results


def _fetch_chunk(service, build_request, chunk: list, results: dict, depth: int = 0):
    batch = service.new_batch_http_request()

    def callback(request_id, response, exception):
        if exception is None:
            results[request_id] = response

    for message_id in chunk:
        batch.add(build_request(message_id), request_id=message_id, callback=callback)
    try:
        _pacer.wait()
        batch.execute()
        _pacer.success()
    except HttpError as exc:
        status = getattr(exc.resp, "status", None)
        if status in (403, 429, 500, 503) and depth < 2:
            _pacer.throttle()
            time.sleep(min(2 ** depth, 4) + random.uniform(0, 0.5))
            return _fetch_chunk(service, build_request, chunk, results, depth + 1)
        print(f"[batch] chunk failed ({status}), falling back to sequential fetch")
    except (ssl.SSLError, ConnectionError, TimeoutError, OSError) as exc:
        if depth < 2:
            time.sleep(1.5 + depth)
            return _fetch_chunk(service, build_request, chunk, results, depth + 1)
        print(f"[batch] connection error ({type(exc).__name__})")

    for message_id in chunk:
        if message_id not in results:
            try:
                results[message_id] = execute(build_request(message_id))
            except Exception as exc:
                status = getattr(getattr(exc, "resp", None), "status", "?")
                print(f"[batch] failed to fetch {message_id}: {status}")


def get_metadata_batch(service, message_ids: list) -> list:
    def build_request(mid):
        return service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        )

    results = _batch_fetch(service, build_request, message_ids)
    return [_parse_metadata(results[mid]) for mid in message_ids if mid in results]


def get_bodies_batch(service, message_ids: list) -> dict:
    if not message_ids:
        return {}

    def build_request(mid):
        return service.users().messages().get(userId="me", id=mid, format="full")

    results = _batch_fetch(service, build_request, message_ids)
    return {
        mid: ((_extract_body(res.get("payload", {})) or res.get("snippet", ""))[:200_000])
        for mid, res in results.items()
    }


def get_metadata(service, message_id: str) -> dict:
    message = execute(
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata",
             metadataHeaders=["From", "Subject", "Date"])
    )
    return _parse_metadata(message)


def _parse_metadata(message: dict) -> dict:
    payload = message.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "")
               for h in payload.get("headers", []) or []}
    thread_id = message.get("threadId", "")
    try:
        internal_date = int(message.get("internalDate", 0) or 0)
    except (TypeError, ValueError):
        internal_date = 0
    return {
        "id": message.get("id", ""),
        "thread_id": thread_id,
        "internal_date": internal_date,
        "snippet": message.get("snippet", ""),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date_header": headers.get("date", ""),
        "gmail_link": f"https://mail.google.com/mail/u/0/#all/{thread_id}" if thread_id else "",
    }


def get_plain_body(service, message_id: str) -> str:
    message = execute(service.users().messages().get(userId="me", id=message_id, format="full"))
    body_text = _extract_body(message.get("payload", {}))
    return body_text or message.get("snippet", "")


def _hrefs_from_html(raw_html: str, limit: int = 30) -> list:
    urls = []
    for match in re.finditer(r'<a[^>]+href=["\'](https?://[^"\']+)["\']', raw_html, re.IGNORECASE):
        url = html_lib.unescape(match.group(1))
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _decode_body_data(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _collect_body_parts(payload: dict, plain: list, html_parts: list):
    mime_type = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")
    if data:
        decoded = _decode_body_data(data)
        if decoded:
            if mime_type == "text/plain":
                plain.append(decoded)
            elif mime_type == "text/html":
                html_parts.append(decoded)
    for part in payload.get("parts") or []:
        _collect_body_parts(part, plain, html_parts)


def _extract_body(payload: dict, prefer_html: bool = False) -> str:
    """Full body text with every HTML link appended.

    Collects ALL text parts instead of returning the first non-empty one: Gmail's
    multipart/alternative usually ships a thin text/plain stub next to the real HTML body,
    and job platforms put the posting link in an HTML button with no visible URL. Appending
    the hrefs means the parser can always see the link (noise links are scored out later by
    parser.URL_EXCLUDE).
    """
    plain, html_parts = [], []
    _collect_body_parts(payload, plain, html_parts)
    if prefer_html:
        if html_parts:
            return html_parts[0]
        return "\n".join(plain)

    text = "\n".join(plain).strip()
    if not text and html_parts:
        text = _strip_html("\n".join(html_parts))
    hrefs = []
    for part in html_parts:
        hrefs.extend(_hrefs_from_html(part))
    if hrefs:
        text = (text + "  " + " ".join(dict.fromkeys(hrefs))).strip()
    return text


def _strip_html(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
