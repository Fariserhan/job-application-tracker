"""Gmail client, enrichment (incl. SSRF guard), the Claude fallback, repair, config, CLI.
All network access is faked."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from jobtracker import enrich
from jobtracker.config import Settings, load_settings
from jobtracker.gmail.client import GmailClient, after_date, extract_body, parse_metadata
from jobtracker.parsing import parse_message
from jobtracker.parsing.llm import PRICING, LLMAssist, cost_usd
from tests.conftest import meta

# --------------------------------------------------------------------------- gmail


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_extract_body_collects_text_and_hidden_links():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": b64("Thanks for applying.")}},
        {"mimeType": "text/html", "body": {"data": b64(
            '<p>Hi</p><a href="https://jobs.example.com/view/123?utm_source=x">View job</a>')}},
    ]}
    body = extract_body(payload)
    assert "Thanks for applying." in body
    assert "https://jobs.example.com/view/123" in body


def test_extract_body_falls_back_to_html_text():
    payload = {"mimeType": "text/html", "body": {"data": b64("<div>Interview <b>invite</b></div>")}}
    assert extract_body(payload).startswith("Interview invite")


def test_parse_metadata():
    msg = {"id": "m", "threadId": "t", "internalDate": "1772445600000", "snippet": "a &amp; b",
           "payload": {"headers": [{"name": "From", "value": "x@y.com"},
                                   {"name": "Subject", "value": "Hello"}]}}
    out = parse_metadata(msg)
    assert out["subject"] == "Hello" and out["snippet"] == "a & b"
    assert out["gmail_link"].endswith("#all/t") and out["internal_date"] == 1772445600000


def test_after_date_uses_floor_and_overlap():
    assert after_date("2026/01/01", None) == "2026/01/01"
    # watermark 2026-03-10 -> two-day overlap -> 2026/03/08
    assert after_date("2026/01/01", 1773100800000) == "2026/03/08"
    assert after_date("2026/06/01", 1773100800000) == "2026/06/01"


class FakeRequest:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class FakeService:
    """Two pages for the first query, one for the second, with a duplicate id."""

    def __init__(self):
        self.pages = {}

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults, pageToken=None):
        sender_query = "from:(" in q
        if sender_query and not pageToken:
            return FakeRequest({"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "p2"})
        if sender_query:
            return FakeRequest({"messages": [{"id": "c"}]})
        return FakeRequest({"messages": [{"id": "b"}, {"id": "d"}]})


def test_search_paginates_and_deduplicates():
    client = GmailClient(FakeService(), parallel=1)
    client.pacer.interval = client.pacer.floor = 0
    assert client.search("2026/01/01", cap=100) == ["a", "b", "c", "d"]
    assert client.search("2026/01/01", cap=2) == ["a", "b"]


# --------------------------------------------------------------------------- enrichment

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://localhost:8080", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/x",
    "http://user:pass@example.com/", "https://intranet/",
])
def test_ssrf_guard_blocks_private_targets(url):
    assert not enrich.is_public_url(url)


def test_ssrf_guard_checks_resolved_addresses(monkeypatch):
    monkeypatch.setattr(enrich.socket, "getaddrinfo",
                        lambda host, port: [(None, None, None, None, ("127.0.0.1", 0))])
    assert not enrich.is_public_url("https://evil-rebind.example.com/job")
    monkeypatch.setattr(enrich.socket, "getaddrinfo",
                        lambda host, port: [(None, None, None, None, ("93.184.216.34", 0))])
    assert enrich.is_public_url("https://jobs.example.com/job")


class FakeResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = status in (301, 302)
        self.encoding = "utf-8"
        self.raw = SimpleNamespace(read=lambda n, decode_content=True: body)

    def close(self):
        pass


def test_redirect_into_private_network_is_blocked(monkeypatch):
    monkeypatch.setattr(enrich, "_host_is_public", lambda host: host != "internal.example")
    session = SimpleNamespace(get=lambda url, **kw: FakeResponse(
        302, {"location": "http://internal.example/secret"}))
    page, error, final = enrich.fetch_page("https://jobs.example.com/x", session)
    assert page is None and error.startswith("blocked") and "internal" in final


def test_jsonld_job_posting_extraction():
    page = """<html><head><meta property="og:title" content="Data Analyst - Acme">
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
      "title":"Data Analyst","description":"<p>SQL, Power BI and Python.</p>",
      "hiringOrganization":{"@type":"Organization","name":"Acme Sdn Bhd"},
      "jobLocation":{"@type":"Place","address":{"addressLocality":"Kuala Lumpur",
        "addressCountry":"MY"}},
      "employmentType":"FULL_TIME","datePosted":"2026-03-01T00:00:00",
      "baseSalary":{"@type":"MonetaryAmount","currency":"MYR","value":{"@type":"QuantitativeValue",
        "minValue":4500,"maxValue":6000,"unitText":"MONTH"}}}</script></head></html>"""
    ld = enrich.extract_jsonld(page)
    assert ld["company"] == "Acme Sdn Bhd"
    assert ld["location"] == "Kuala Lumpur, MY"
    assert ld["salary"] == "4500–6000 MYR / month"
    assert ld["posted_date"] == "2026-03-01"
    assert ld["employment_type"] == "Full Time"
    assert enrich.extract_og(page)["og_title"] == "Data Analyst - Acme"


def test_enrichment_is_stored_without_overwriting_longer_text(repo):
    repo.apply_message(parse_message(meta(), "Thank you for applying. " * 30))
    repo.apply_enrichment({"t1": {"ok": True, "title": "Data Analyst", "description": "short",
                                  "location": "Kuala Lumpur", "salary": "RM5k"}})
    row = repo.get("t1")
    assert row["location"] == "Kuala Lumpur" and row["salary_text"] == "RM5k"
    assert len(row["job_description_snippet"]) > 100


# --------------------------------------------------------------------------- Claude fallback

class FakeMessages:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.calls = []
        self.parsed = parsed
        self.stop_reason = stop_reason

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=600, output_tokens=60, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(parsed_output=self.parsed, usage=usage,
                               stop_reason=self.stop_reason)


def verdict(**kw):
    base = dict(is_job_application_event=True, status="Interview", company="Orchid Labs",
                role="BI Analyst", confidence=0.9)
    base.update(kw)
    return SimpleNamespace(**base)


def ambiguous_message():
    msg = parse_message(meta(subject="Next steps", snippet="Let's find a time to chat.",
                             **{"from": "Jane Tan <jane@orchidlabs.io>"}))
    msg.confidence = min(msg.confidence, 0.55)  # the only kind of verdict the fallback sees
    assert msg.ambiguous
    return msg


def test_llm_disabled_by_default_makes_no_calls(repo, settings):
    fake = SimpleNamespace(messages=FakeMessages(verdict()))
    assist = LLMAssist(settings, repo, client=fake)
    msg = ambiguous_message()
    assert assist.review(msg, "body").parsed_by == "rules"
    assert fake.messages.calls == []


def test_llm_reviews_ambiguous_mail_once_then_uses_cache(repo, settings):
    on = settings.with_updates(llm_enabled=True)
    fake = SimpleNamespace(messages=FakeMessages(verdict()))
    assist = LLMAssist(on, repo, client=fake)
    msg = assist.review(ambiguous_message(), "Let's find a time to chat about the BI Analyst role.")
    assert (msg.parsed_by, msg.is_valid, msg.detected_status) == ("llm", True, "Interview")
    assert msg.company_name == "Orchid Labs"
    call = fake.messages.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"] == {"effort": "low"}
    # second review (e.g. a force re-sync) replays the cache — no new API call
    again = assist.review(ambiguous_message(), "")
    assert again.parsed_by == "llm" and len(fake.messages.calls) == 1
    spend = repo.llm_spend()
    assert spend["calls"] == 1 and spend["cost_usd"] == pytest.approx(
        (600 * PRICING["claude-haiku-5-5"][0] + 60 * PRICING["claude-haiku-5-5"][1]) / 1e6)


def test_llm_respects_monthly_budget(repo, settings):
    on = settings.with_updates(llm_enabled=True, llm_monthly_budget_usd=0.0)
    fake = SimpleNamespace(messages=FakeMessages(verdict()))
    assist = LLMAssist(on, repo, client=fake)
    assert assist.review(ambiguous_message(), "x").parsed_by == "rules"
    assert fake.messages.calls == [] and "budget" in assist.disabled_reason


def test_llm_low_confidence_or_refusal_is_ignored(repo, settings):
    on = settings.with_updates(llm_enabled=True)
    low = LLMAssist(on, repo, client=SimpleNamespace(messages=FakeMessages(verdict(confidence=0.3))))
    assert low.review(ambiguous_message(), "x").parsed_by == "rules"
    repo2_msg = ambiguous_message()
    repo2_msg.message_id = "m-refused"
    refused = LLMAssist(on, repo, client=SimpleNamespace(
        messages=FakeMessages(None, stop_reason="refusal")))
    assert refused.review(repo2_msg, "x").parsed_by == "rules"


def test_clear_cut_mail_never_reaches_the_llm(repo, settings):
    on = settings.with_updates(llm_enabled=True)
    fake = SimpleNamespace(messages=FakeMessages(verdict()))
    msg = parse_message(meta(), "Thank you for applying. We have received your application.")
    assert not msg.ambiguous
    LLMAssist(on, repo, client=fake).review(msg, "x")
    assert fake.messages.calls == []


def test_cost_accounts_for_cache_reads_and_writes():
    usage = SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=1000,
                            cache_creation_input_tokens=0)
    assert cost_usd("claude-haiku-5-5", usage) == pytest.approx(
        (100 * 0.10 + 1000 * 0.01 + 50 * 0.50) / 1e6)


# --------------------------------------------------------------------------- repair

def test_repair_flags_account_notices_and_recovers_missed_applications(repo, settings):
    from jobtracker.repair import run

    repo.apply_message(parse_message(meta(), "Thank you for applying. Application received."))
    with repo.connect() as conn:
        conn.execute("UPDATE applications SET latest_subject='Verify your candidate account' "
                     "WHERE thread_id='t1'")
    noise = parse_message(meta(id="m9", thread_id="t9", subject="Newsletter",
                               **{"from": "news@shop.com"}))
    repo.apply_message(noise)
    with repo.connect() as conn:
        conn.execute("UPDATE applications SET latest_subject='Your application has been received "
                     "- Data Analyst at Orchid Labs' WHERE thread_id='t9'")
    plan, _ = run(settings, apply=False)
    by_id = {p["thread_id"]: p for p in plan}
    assert by_id["t1"]["is_valid"] is False
    assert by_id["t9"]["is_valid"] is True
    _, counts = run(settings, apply=True)
    assert counts["invalidated"] == 1 and counts["recovered"] == 1
    assert repo.get("t9")["is_valid"] == 1
    assert any("pre-repair" in p.name for p in settings.backups_dir.iterdir())


# --------------------------------------------------------------------------- config & CLI

def test_settings_round_trip_and_env_override(tmp_path, monkeypatch):
    s = Settings(data_dir=tmp_path).with_updates(ghost_after_days=30, llm_enabled=True)
    s.save()
    loaded = load_settings(tmp_path)
    assert loaded.ghost_after_days == 30 and loaded.llm_enabled is True
    monkeypatch.setenv("JOBTRACKER_GHOST_AFTER_DAYS", "14")
    assert load_settings(tmp_path).ghost_after_days == 14
    assert json.loads((tmp_path / "settings.json").read_text())["llm_model"] == "claude-haiku-5-5"


def test_cli_demo_build_and_check(tmp_path, capsys):
    from jobtracker.cli import main

    target = tmp_path / "demo"
    assert main(["demo", "--dir", str(target)]) == 0
    assert main(["--data-dir", str(target), "build"]) == 0
    assert main(["--data-dir", str(target), "check"]) == 0
    assert main(["--data-dir", str(target), "show"]) == 0
    out = capsys.readouterr().out
    assert "Employer identified" in out and "applications" in out


# --------------------------------------------------------------------------- batching

class FakeBatch:
    def __init__(self, service):
        self.service = service
        self.items = []

    def add(self, request, request_id, callback):
        self.items.append((request, request_id, callback))

    def execute(self):
        for _request, rid, callback in self.items:
            if rid in self.service.drop:
                continue  # the batch silently loses this message
            callback(rid, {"id": rid, "threadId": f"t-{rid}", "internalDate": "0",
                           "payload": {"headers": [{"name": "Subject", "value": rid}]}}, None)


class BatchService:
    def __init__(self, drop=()):
        self.drop = set(drop)
        self.singles: list[str] = []

    def new_batch_http_request(self):
        return FakeBatch(self)

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, **kw):
        service = self

        class Single:
            def execute(self_inner):
                service.singles.append(id)
                return {"id": id, "threadId": f"t-{id}", "internalDate": "0", "payload": {}}
        return Single()


def test_metadata_batches_and_refetches_dropped_messages():
    service = BatchService(drop={"m7"})
    client = GmailClient(service, parallel=1)
    client.pacer.interval = client.pacer.floor = 0
    ids = [f"m{i}" for i in range(120)]
    metas = client.metadata(ids)
    assert [m["id"] for m in metas] == ids          # order preserved, nothing lost
    assert service.singles == ["m7"]                # only the dropped one fetched singly


# --------------------------------------------------------------------------- enrichment fetch

def test_enrich_reads_a_posting_through_a_safe_redirect(monkeypatch):
    monkeypatch.setattr(enrich, "_host_is_public", lambda host: True)
    page = (b'<html><head><meta property="og:title" content="BI Analyst - Orchid Labs">'
            b'<meta name="og:description" content="Own the Power BI estate."></head></html>')
    responses = {
        "https://lnk.example.com/j": FakeResponse(302, {"location": "/jobs/42"}),
        "https://lnk.example.com/jobs/42": FakeResponse(200, {"content-type": "text/html"}, page),
    }
    session = SimpleNamespace(get=lambda url, **kw: responses[url])
    out = enrich.enrich("https://lnk.example.com/j", session)
    assert out["ok"] and out["title"] == "BI Analyst - Orchid Labs"
    assert out["url"].endswith("/jobs/42")


def test_enrich_reports_non_html_and_errors(monkeypatch):
    monkeypatch.setattr(enrich, "_host_is_public", lambda host: True)
    pdf = SimpleNamespace(get=lambda url, **kw: FakeResponse(200, {"content-type": "application/pdf"}))
    assert enrich.enrich("https://x.example.com/a.pdf", pdf)["reason"].startswith("non-html")
    forbidden = SimpleNamespace(get=lambda url, **kw: FakeResponse(403))
    assert enrich.enrich("https://x.example.com/", forbidden)["reason"] == "blocked (403)"


# --------------------------------------------------------------------------- report

def test_findings_report_is_labelled_and_hedged(demo):
    from jobtracker.report import build_report

    text = build_report(demo, demo=True)
    assert text.startswith("# Job search analysis — synthetic demo data")
    assert "Demo dataset." in text and "95% CI" in text and "Kaplan–Meier" in text
    assert "applications** to" in text
    for banned in ("loss ratio", "ibnr", "fit score"):
        assert banned not in text.lower()


def test_report_on_empty_warehouse(settings):
    from jobtracker.report import build_report
    from jobtracker.store import Repository
    from jobtracker.warehouse import build

    Repository(settings).init()
    build(settings)
    assert "No applications yet" in build_report(settings)


def test_cli_data_dir_accepted_before_or_after_subcommand(tmp_path, monkeypatch):
    from jobtracker import cli

    seen = []

    def stop(data_dir):
        seen.append(data_dir)
        raise SystemExit

    monkeypatch.setattr(cli, "load_settings", stop)
    for argv in (["export", "--data-dir", "A"], ["--data-dir", "B", "export"], ["export"]):
        with pytest.raises(SystemExit):
            cli.main(argv)
    assert seen == ["A", "B", None]
