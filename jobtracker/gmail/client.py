"""Quota-aware Gmail client.

* searches are paginated and unioned across the two queries in ``query.py``
* metadata / body fetches go through 50-message batch requests, several batches in flight
  on independent HTTP connections, all sharing one adaptive pacer that starts at the
  quota-safe rate, speeds up on clean responses and backs off instantly on 403/429/5xx
* any message a batch drops is retried individually, so a flaky network loses nothing

``MailSource`` is the interface the sync engine needs; the demo mailbox implements it too,
which is how the public demo exercises the real sync / parse / store path end to end.
"""
from __future__ import annotations

import base64
import html as html_lib
import logging
import random
import re
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Protocol

from ..parsing.text import strip_html
from .query import SEARCH_QUERIES

log = logging.getLogger(__name__)

BATCH_SIZE = 50
UNITS_PER_MESSAGE_GET = 5        # users.messages.get costs 5 quota units
UNITS_PER_SECOND = 250.0         # Gmail's default per-user budget
PARALLEL_BATCHES = 3
RETRYABLE = (403, 429, 500, 503)
MAX_BODY_CHARS = 200_000


class MailSource(Protocol):
    def profile(self) -> dict: ...
    def search(self, after: str, cap: int) -> list[str]: ...
    def metadata(self, message_ids: list[str]) -> list[dict]: ...
    def bodies(self, message_ids: list[str]) -> dict[str, str]: ...


class AdaptivePacer:
    """Thread-safe shared rate limiter tuned to the Gmail per-user quota."""

    def __init__(self, interval: float, floor: float, ceiling: float = 8.0):
        self.interval = interval
        self.floor = floor
        self.ceiling = ceiling
        self._last = time.monotonic() - interval
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(self._last, now - 0.001) + self.interval
            delay = max(0.0, slot - now)
            self._last = slot
        if delay > 0:
            time.sleep(delay)

    def success(self) -> None:
        with self._lock:
            self.interval = max(self.floor, self.interval * 0.95)

    def throttle(self) -> None:
        with self._lock:
            self.interval = min(self.ceiling, max(self.interval * 2.0, 1.2))


def _base_interval() -> float:
    return UNITS_PER_MESSAGE_GET * BATCH_SIZE / UNITS_PER_SECOND


class GmailClient:
    def __init__(self, service, credentials=None, parallel: int = PARALLEL_BATCHES):
        self.service = service
        self.credentials = credentials
        self.parallel = max(1, parallel)
        base = _base_interval()
        self.pacer = AdaptivePacer(interval=max(base, 0.35), floor=max(base * 0.75, 0.25))

    @classmethod
    def from_settings(cls, settings, interactive: bool = True) -> GmailClient:
        from googleapiclient.discovery import build

        from .auth import get_credentials

        creds = get_credentials(settings.secret_path("credentials.json"),
                                settings.secret_path("token.json"), interactive=interactive)
        return cls(build("gmail", "v1", credentials=creds, cache_discovery=False), creds)

    # ------------------------------------------------------------------ core
    def _execute(self, request, max_retries: int = 4):
        from googleapiclient.errors import HttpError

        delay = 0.8
        for attempt in range(max_retries + 1):
            self.pacer.wait()
            try:
                response = request.execute()
                self.pacer.success()
                return response
            except HttpError as exc:
                if exc.resp.status in RETRYABLE and attempt < max_retries:
                    self.pacer.throttle()
                    time.sleep(delay + random.uniform(0, 0.5))
                    delay = min(delay * 2, 5)
                    continue
                raise
            except (ssl.SSLError, ConnectionError, TimeoutError, OSError):
                # Transient TLS / network blips: retry briefly, then fail fast.
                if attempt < 2:
                    time.sleep(min(1.5 + attempt, 3) + random.uniform(0, 0.3))
                    continue
                raise
        raise RuntimeError("unreachable")

    def _clone(self):
        if self.credentials is None:
            return None
        try:
            from googleapiclient.discovery import build

            return build("gmail", "v1", credentials=self.credentials, cache_discovery=False)
        except Exception:
            return None

    # ------------------------------------------------------------------ MailSource
    def profile(self) -> dict:
        return self._execute(self.service.users().getProfile(userId="me"))

    def search(self, after: str, cap: int) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for base_query in SEARCH_QUERIES:
            query = f"{base_query} after:{after}"
            token = None
            while True:
                response = self._execute(self.service.users().messages().list(
                    userId="me", q=query, maxResults=500, pageToken=token))
                for message in response.get("messages", []):
                    if message["id"] not in seen:
                        seen.add(message["id"])
                        ordered.append(message["id"])
                token = response.get("nextPageToken")
                if not token or len(ordered) >= cap:
                    break
            if len(ordered) >= cap:
                break
        return ordered[:cap]

    def metadata(self, message_ids: list[str]) -> list[dict]:
        def build_request(service, mid):
            return service.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "Subject", "Date"])

        results = self._batch_fetch(build_request, message_ids)
        return [parse_metadata(results[mid]) for mid in message_ids if mid in results]

    def bodies(self, message_ids: list[str]) -> dict[str, str]:
        if not message_ids:
            return {}

        def build_request(service, mid):
            return service.users().messages().get(userId="me", id=mid, format="full")

        results = self._batch_fetch(build_request, message_ids)
        return {mid: (extract_body(res.get("payload", {})) or res.get("snippet", ""))[:MAX_BODY_CHARS]
                for mid, res in results.items()}

    # ------------------------------------------------------------------ batching
    def _batch_fetch(self, build_request, message_ids: list[str]) -> dict:
        results: dict = {}
        chunks = [message_ids[i:i + BATCH_SIZE] for i in range(0, len(message_ids), BATCH_SIZE)]
        if not chunks:
            return results
        services = [self.service]
        if len(chunks) > 1 and self.parallel > 1:
            clones = [self._clone() for _ in range(min(self.parallel, len(chunks)) - 1)]
            services += [c for c in clones if c is not None]
        if len(services) == 1:
            for chunk in chunks:
                self._fetch_chunk(self.service, build_request, chunk, results)
            return results
        failed = []
        with ThreadPoolExecutor(max_workers=len(services)) as pool:
            futures = {pool.submit(self._fetch_chunk, services[i % len(services)], build_request,
                                   chunk, results): chunk for i, chunk in enumerate(chunks)}
            for future, chunk in futures.items():
                try:
                    future.result()
                except Exception as exc:
                    log.warning("batch worker failed (%s); retrying chunk sequentially",
                                type(exc).__name__)
                    failed.append(chunk)
        for chunk in failed:
            self._fetch_chunk(self.service, build_request, chunk, results)
        return results

    def _fetch_chunk(self, service, build_request, chunk: list[str], results: dict,
                     depth: int = 0) -> None:
        from googleapiclient.errors import HttpError

        batch = service.new_batch_http_request()

        def callback(request_id, response, exception):
            if exception is None:
                results[request_id] = response

        for mid in chunk:
            batch.add(build_request(service, mid), request_id=mid, callback=callback)
        try:
            self.pacer.wait()
            batch.execute()
            self.pacer.success()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status in RETRYABLE and depth < 2:
                self.pacer.throttle()
                time.sleep(min(2 ** depth, 4) + random.uniform(0, 0.5))
                return self._fetch_chunk(service, build_request, chunk, results, depth + 1)
            log.warning("batch failed (%s); falling back to per-message fetch", status)
        except (ssl.SSLError, ConnectionError, TimeoutError, OSError) as exc:
            if depth < 2:
                time.sleep(1.5 + depth)
                return self._fetch_chunk(service, build_request, chunk, results, depth + 1)
            log.warning("batch connection error (%s)", type(exc).__name__)
        for mid in chunk:
            if mid not in results:
                try:
                    results[mid] = self._execute(build_request(service, mid))
                except Exception as exc:
                    status = getattr(getattr(exc, "resp", None), "status", "?")
                    log.warning("failed to fetch %s: %s", mid, status)


# ---------------------------------------------------------------------------- parsing


def after_date(default_after: str, watermark_ms: int | None, overlap_days: int = 2) -> str:
    """Gmail ``after:`` date: the later of the configured floor and the watermark, minus an
    overlap so a message whose fetch failed last run is always seen again."""
    dates = [default_after]
    if watermark_ms:
        epoch = float(watermark_ms) / 1000.0 - overlap_days * 86400
        dates.append(datetime.fromtimestamp(max(epoch, 0), tz=UTC).strftime("%Y/%m/%d"))
    return max(dates)


def parse_metadata(message: dict) -> dict:
    payload = message.get("payload") or {}
    headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}
    thread_id = message.get("threadId", "")
    try:
        internal_date = int(message.get("internalDate", 0) or 0)
    except (TypeError, ValueError):
        internal_date = 0
    return {
        "id": message.get("id", ""),
        "thread_id": thread_id,
        "internal_date": internal_date,
        "snippet": html_lib.unescape(message.get("snippet", "")),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date_header": headers.get("date", ""),
        "gmail_link": f"https://mail.google.com/mail/u/0/#all/{thread_id}" if thread_id else "",
    }


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _collect_parts(payload: dict, plain: list[str], html_parts: list[str]) -> None:
    data = (payload.get("body") or {}).get("data")
    if data:
        decoded = _decode(data)
        if decoded:
            mime = payload.get("mimeType", "")
            if mime == "text/plain":
                plain.append(decoded)
            elif mime == "text/html":
                html_parts.append(decoded)
    for part in payload.get("parts") or []:
        _collect_parts(part, plain, html_parts)


def _hrefs(raw_html: str, limit: int = 30) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r'<a[^>]+href=["\'](https?://[^"\']+)["\']', raw_html, re.IGNORECASE):
        url = html_lib.unescape(match.group(1))
        if url not in urls:
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def extract_body(payload: dict) -> str:
    """All text parts plus every HTML link.

    Gmail's multipart/alternative often ships a thin text/plain stub next to the real HTML,
    and job platforms hide the posting link behind an HTML button. Appending the hrefs means
    the parser always sees the link (noise links are scored out later).
    """
    plain: list[str] = []
    html_parts: list[str] = []
    _collect_parts(payload, plain, html_parts)
    text = "\n".join(plain).strip()
    if not text and html_parts:
        text = strip_html("\n".join(html_parts))
    links: list[str] = []
    for part in html_parts:
        links.extend(_hrefs(part))
    if links:
        text = (text + "  " + " ".join(dict.fromkeys(links))).strip()
    return text
