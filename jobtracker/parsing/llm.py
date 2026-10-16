"""Optional Claude fallback for the emails the rules cannot classify confidently.

Off by default. When enabled it is deliberately cheap and bounded:

* **rules first** — only messages whose rule verdict is ambiguous (``ParsedMessage.ambiguous``)
  and that were freshly downloaded are ever sent; clear-cut mail never leaves the machine
* **cheapest model** — Claude Haiku 5.5 by default (configurable), low effort, thinking off,
  ~300 output tokens of structured JSON
* **one call per email, ever** — results are cached in ``llm_calls`` by Gmail message id, so
  re-syncs and force-full re-parses replay the cached answer for free
* **trimmed input** — subject, sender and the first ~1,500 chars of the sanitised body
* **cached system prompt** — the instructions are a stable prefix marked for prompt caching
* **hard monthly budget** — spend is tracked per call; at the cap the fallback switches off

Typical cost: ~700 input + ~80 output tokens ≈ $0.0001 per ambiguous email on Haiku 5.5.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from ..domain import APPLIED, ASSESSMENT, INTERVIEW, OFFER, REJECTED
from .pipeline import ParsedMessage, finalize
from .text import sanitize_body

log = logging.getLogger(__name__)

# USD per million tokens: (input, output). Cache reads bill at 0.1x input, 5-minute cache
# writes at 1.25x input.
PRICING = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
MIN_CONFIDENCE = 0.6
BODY_CHARS = 1500

SYSTEM_PROMPT = """You classify single emails from a job seeker's Gmail inbox for a personal \
job-application tracker. The job seeker is applying for analyst roles (data, BI, actuarial, \
risk, insurance pricing) mainly in Malaysia and Southeast Asia.

Decide whether the email is a real event in one of the job seeker's OWN applications, and \
if so which pipeline status it evidences.

Count as an application event:
- confirmation that an application was received, submitted or viewed by the employer
- an invitation to an online assessment, coding test, psychometric test or video interview
- an interview invitation, scheduling message or interview confirmation
- a rejection or "not moving forward" message about an application
- a job offer or offer letter
- a generic status update about a specific application (still under review, etc.)

NOT application events (is_job_application_event = false):
- job alerts, recommended jobs, "jobs you may like", digests, newsletters
- career advice, salary guides, webinars, career fairs, recruiter marketing
- account housekeeping: verify email, reset password, one-time codes, profile reminders
- "invitation to apply" / "apply now" adverts for roles the person has not applied to
- unrelated commerce that happens to use words like "application" or "offer"

Status values:
- "Applied": the application exists but nothing has happened yet
- "Assessment / OA": an online assessment, test or screening task was sent
- "Interview": an interview of any kind was offered, scheduled or confirmed
- "Offer": an offer of employment was made
- "Rejected": the application was declined or the position closed for them
- "none": not an application event

Extraction:
- company: the EMPLOYER hiring for the role. Never a job board or ATS vendor (LinkedIn, \
JobStreet, Hiredly, Prosple, Indeed, Workday, SuccessFactors, Greenhouse, Lever, iCIMS, \
Taleo). Use "" when the employer is not named.
- role: the job title exactly as written, without decorations such as "Application for" or \
"at Company". Use "" when no title is named.
- confidence: your probability (0 to 1) that is_job_application_event and status are right.

When an email both thanks the person for applying and declines them, the status is \
"Rejected". When it confirms an application and invites them to a test in the same message, \
the status is "Assessment / OA". Judge only this email; quoted older messages do not count."""


@dataclass(frozen=True)
class LLMResult:
    is_event: bool
    status: str
    company: str
    role: str
    confidence: float


def _schema():
    from pydantic import BaseModel, Field

    class EmailVerdict(BaseModel):
        is_job_application_event: bool
        status: Literal["Applied", "Assessment / OA", "Interview", "Offer", "Rejected", "none"]
        company: str = Field(description="employer name or empty string")
        role: str = Field(description="job title or empty string")
        confidence: float

    return EmailVerdict


def cost_usd(model: str, usage) -> float:
    rate_in, rate_out = PRICING.get(model, PRICING["claude-haiku-5-5"])
    fresh = getattr(usage, "input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    return (fresh * rate_in + cache_read * rate_in * 0.1 + cache_write * rate_in * 1.25
            + output * rate_out) / 1_000_000


class LLMAssist:
    def __init__(self, settings, repo, client=None):
        self.settings = settings
        self.repo = repo
        self._client = client
        self.calls_this_run = 0
        self.disabled_reason = ""

    # ------------------------------------------------------------------ gating
    def available(self) -> bool:
        if not self.settings.llm_enabled:
            self.disabled_reason = "disabled in settings"
            return False
        if self._client is None:
            try:
                import anthropic

                self._client = anthropic.Anthropic(max_retries=2, timeout=30.0)
            except Exception as exc:  # package missing or no credentials
                self.disabled_reason = f"anthropic client unavailable ({type(exc).__name__})"
                return False
        return True

    def budget_left(self) -> float:
        spent = self.repo.llm_spend()["cost_usd"]
        return max(0.0, float(self.settings.llm_monthly_budget_usd) - spent)

    # ------------------------------------------------------------------ main entry
    def review(self, msg: ParsedMessage, body: str, allow_new_call: bool = True) -> ParsedMessage:
        """Return ``msg`` corrected by Claude when the verdict is ambiguous; else unchanged."""
        if not msg.ambiguous or not msg.message_id:
            return msg
        cached = self.repo.llm_cached(msg.message_id)
        if cached and cached.get("result_json"):
            import json

            return self._apply(msg, LLMResult(**json.loads(cached["result_json"])))
        if not allow_new_call or not self.available():
            return msg
        if self.calls_this_run >= self.settings.llm_max_calls_per_sync:
            self.disabled_reason = "per-sync call cap reached"
            return msg
        if self.budget_left() <= 0:
            self.disabled_reason = "monthly budget reached"
            return msg
        result = self._call(msg, body)
        return self._apply(msg, result) if result else msg

    def _call(self, msg: ParsedMessage, body: str) -> LLMResult | None:
        import anthropic

        model = self.settings.llm_model
        excerpt = sanitize_body(body)[:BODY_CHARS] if body else ""
        user = (f"From: {msg.sender}\nSubject: {msg.subject}\nDate: {msg.message_date}\n\n"
                f"{excerpt or '(body not downloaded)'}")
        self.calls_this_run += 1
        try:
            response = self._client.messages.parse(
                model=model,
                max_tokens=300,
                system=[{"type": "text", "text": SYSTEM_PROMPT,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                thinking={"type": "disabled"},
                output_config={"effort": "low"},
                output_format=_schema(),
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError,
                anthropic.APIStatusError) as exc:
            log.warning("LLM fallback failed for %s: %s", msg.message_id, type(exc).__name__)
            self.repo.record_llm_call(msg.message_id, model, 0, 0, 0.0, None,
                                      error=type(exc).__name__)
            return None
        spend = cost_usd(model, response.usage)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            self.repo.record_llm_call(msg.message_id, model, response.usage.input_tokens,
                                      response.usage.output_tokens, spend, None,
                                      error=str(response.stop_reason))
            return None
        parsed = response.parsed_output
        result = LLMResult(
            is_event=bool(parsed.is_job_application_event),
            status="" if parsed.status == "none" else parsed.status,
            company=(parsed.company or "").strip()[:120],
            role=(parsed.role or "").strip()[:120],
            confidence=max(0.0, min(1.0, float(parsed.confidence))),
        )
        self.repo.record_llm_call(msg.message_id, model, response.usage.input_tokens,
                                  response.usage.output_tokens, spend, result.__dict__)
        return result

    # ------------------------------------------------------------------ merge
    @staticmethod
    def _apply(msg: ParsedMessage, result: LLMResult) -> ParsedMessage:
        if result.confidence < MIN_CONFIDENCE:
            return msg
        msg.is_valid = result.is_event
        msg.parsed_by = "llm"
        msg.confidence = result.confidence
        msg.filter_reason = f"llm: {'application event' if result.is_event else 'not job mail'}"
        if result.is_event:
            if result.status in (APPLIED, ASSESSMENT, INTERVIEW, OFFER, REJECTED):
                msg.detected_status = result.status
            if result.company and (msg.company_name in ("", "Unknown")):
                msg.company_name = result.company
            if result.role and not msg.role_title:
                msg.role_title = result.role
        return finalize(msg)
