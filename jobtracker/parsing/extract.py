"""Field extraction: employer, role title, posting link and source platform.

Recruiting mail rarely names the employer cleanly — the sender is often a job board or an
ATS tenant ("UOB Workday", "no-reply@myworkday.com"). Extraction therefore runs several
independent strategies (subject templates, body phrasing, sender display name, sender
domain, ATS tenant label) and a quality gate that refuses to store a platform name, a
pronoun or a sentence fragment as the employer or role.
"""
from __future__ import annotations

import re

from .patterns import (
    ATS_DOMAIN_SUFFIXES,
    COMPANY_LEAD_STOPWORDS,
    COMPANY_MARKER_RE,
    COMPANY_MARKERS,
    COMPANY_ROLE_PATTERNS,
    COMPANY_STOPWORDS,
    DISPLAY_NAME_BLOCKLIST,
    DOMAIN_RE,
    GENERIC_SUBDOMAIN_LABELS,
    JUNK_ROLE_PREFIX,
    PLATFORM_COMPANY_RE,
    PLATFORM_DOMAINS,
    ROLE_WORD_RE,
    SENDER_NAME_NOISE,
    SUBJECT_PATTERNS,
    URL_CONTEXT_HINT,
    URL_EXCLUDE,
    URL_JOB_HINT,
    URL_PLATFORM_HINT,
)
from .text import sanitize_body, strip_tracking_params

_URL_CONTEXT = [re.compile(p) for p in URL_CONTEXT_HINT]
_SENTENCE_ROLE = re.compile(
    r"\byour (?:job )?application\b|\bapplication (?:was|has been|is)\b|\bwas sent\b|"
    r"^[A-Z][a-z]+(?: [A-Z][a-z]+)?, (?:your|you|we|thanks?|thank you)\b", re.IGNORECASE)
# A company capture that ran on into the next sentence: "Acme Sdn Bhd. The employer will…"
_NEXT_SENTENCE = re.compile(
    r"\.\s+(?:The|We|You|Your|Our|Please|Thank|Thanks|If|This|They|It|Good|Best)\b.*$")
_LINK = re.compile(r"https?://[^\s\"'<>\)\]>]+")

# ------------------------------------------------------------------ quality gates


def is_platform_company(company: str) -> bool:
    """True for platform / placeholder names that are never the real employer."""
    c = (company or "").strip().lower()
    if not c or c in ("nan", "none", "unknown", "other", "direct ats"):
        return True
    return bool(PLATFORM_COMPANY_RE.search(c))


def looks_like_role(role: str) -> bool:
    """True when a string is plausibly a job title, not a sentence fragment or an ID."""
    role = (role or "").strip()
    if len(role) < 3 or len(role) > 110:
        return False
    if not re.search(r"[A-Za-z]{2}", role):
        return False
    # Notification sentences, not titles: "Aina, your application was sent to X".
    if _SENTENCE_ROLE.search(role):
        return False
    return not JUNK_ROLE_PREFIX.match(role)


def plausible_company(text: str) -> bool:
    if not text:
        return False
    return not (ROLE_WORD_RE.search(text) and not COMPANY_MARKER_RE.search(text))


def is_junk_company_name(company: str) -> bool:
    cleaned = clean_company_name(company)
    if not cleaned:
        return True
    if cleaned.lower().strip(".,") in ("unknown", "none", "nan", "other", "direct ats"):
        return True
    return is_platform_company(cleaned)


# ------------------------------------------------------------------ cleaners


def clean_company_name(name: str) -> str:
    name = (name or "").strip()
    if "@" in name:  # an email address / domain is never an employer name
        return ""
    name = re.sub(r"[\r\n].*$", "", name)
    name = _NEXT_SENTENCE.sub("", name)
    name = re.sub(
        r"\s+(?:unfortunately|regrettably|however|but|therefore|so|please|you|your|"
        r"our|if|we|they)\b.*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*[-–—]\s*$", "", name).strip()
    name = re.sub(r"^(?:the|a|an|and|or|but|so|however)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", name).strip(" .,-|·")
    name = re.sub(r"\s+(?:via|from)\s+.*$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s+(?:has|have|was|is|were|will|we(?:'ve| have)?)\s+.*$", "", name,
                  flags=re.IGNORECASE).strip()
    words = name.split()
    while words and words[-1].lower().strip(".,") in SENDER_NAME_NOISE:
        words.pop()
    cleaned = " ".join(words).strip()
    if cleaned.lower().strip(".,") in COMPANY_STOPWORDS:
        return ""
    first = words[0].lower().strip(".,") if words else ""
    if first in COMPANY_LEAD_STOPWORDS:
        return ""
    if not re.search(r"[A-Za-z]{2}", cleaned):
        return ""
    if re.fullmatch(r"[\w-]+\.[a-z]{2,}", cleaned.lower()):
        cleaned = cleaned.split(".")[0]
    return cleaned


def clean_role_name(role: str) -> str:
    role = (role or "").strip(" .,-|·\"'")
    # Email-subject decorations ("Interview invitation - X", "Application received: X").
    role = re.sub(
        r"(?i)^(?:interview|application|assessment|offer|invitation|update|notice)"
        r"[^,|:–—-]{0,40}\s*[-–—:]\s*", "", role)
    # "Thank you for applying <role>" -> <role> (but not "... for applying TO <company>").
    role = re.sub(
        r"(?i)^thank(?:s| you)(?: so much)? for (?:applying|your application|your interest)"
        r"\s+(?!to\b|at\b|with\b|for\b|in\b)", "", role)
    role = re.sub(r"(?i)^(?:urgent|immediate|hiring now|new)\s*[!:\-–—]\s*", "", role)
    role = re.sub(r"(?i)^(?:candidate )?application (?:is |was )?sent\b.*$", "", role)
    role = re.sub(r"(?i)^your job application has been received\b.*$", "", role)
    role = re.sub(r"\s+at\s+[^,]+$", "", role, flags=re.IGNORECASE).strip()
    role = re.sub(r"(?i)\s+(?:role|position|job|opening|vacancy)$", "", role).strip()
    if "|" in role or "–" in role:
        role = re.sub(r"\s*[-–|]\s*[^|]*$", "", role).strip()
    return re.sub(r"\s+", " ", role).strip(" .,-|·")


def prettify_domain(domain: str) -> str:
    cleaned = re.sub(r"^(?:www|mail|careers?|jobs?|hr|talent|recruit\w*)[.-]", "", domain)
    return cleaned.replace("-", " ").replace("_", " ").title()


def strip_platform_suffix(company: str) -> str:
    """'UOB Workday' -> 'UOB'; 'Workday rgare' -> 'rgare' (leaves 'Myworkday' intact)."""
    cleaned = re.sub(r"(?i)^workday\s+", "", (company or "").strip())
    cleaned = re.sub(r"(?i)\s+workday.*$", "", cleaned)
    cleaned = re.sub(
        r"(?i)\s+(careers?|talent (?:acquisition|team)|recruit(?:ment|ing)?|hiring(?: team)?)$",
        "", cleaned).strip(" .,-")
    return cleaned


# ------------------------------------------------------------------ subject / sender


def parse_subject(subject: str) -> tuple[str, str]:
    for pattern in SUBJECT_PATTERNS:
        match = pattern.search(subject or "")
        if match:
            groups = match.groupdict()
            return clean_company_name(groups.get("company", "")), clean_role_name(groups.get("role", ""))
    return "", ""


def company_from_bracket(subject: str) -> str:
    match = re.search(r"\[([\w .,&'-]{2,50})\]", subject or "")
    return clean_company_name(match.group(1)) if match else ""


def company_from_display_name(sender: str) -> str:
    match = re.match(r'^(?:"([^"]*)"|([^<]+?))\s*<[^>]+>\s*$', (sender or "").strip())
    if not match:
        return ""
    display = (match.group(1) or match.group(2) or "").strip()
    if display.lower().strip() in DISPLAY_NAME_BLOCKLIST:
        return ""
    display = re.split(r"\s+(?:via|from|at)\s+", display, flags=re.IGNORECASE)[0]
    display = re.split(r"\s*[|·]\s*", display)[0]
    display = re.sub(r"\s*[-–—]\s*.*$", "", display).strip()
    words = display.split()
    had_company_marker = any(w.lower().strip(".,") in COMPANY_MARKERS for w in words)
    while words and words[-1].lower().strip(".,") in SENDER_NAME_NOISE:
        words.pop()
    cleaned = " ".join(words).strip(" .,-")
    if len(cleaned) < 2:
        return ""
    if re.search(r"\b(?:no|donot)\s?-?reply\b", cleaned, re.IGNORECASE):
        return ""
    # "Jane Tan <jane@acme.com>" is a recruiter's personal name, not the employer.
    if len(words) == 2 and all(w.istitle() for w in words) and not had_company_marker:
        return ""
    return cleaned


def company_from_domain(sender: str) -> str:
    domain_match = DOMAIN_RE.search(sender or "")
    if not domain_match:
        return ""
    full_domain = domain_match.group(1).lower()
    for suffix in ATS_DOMAIN_SUFFIXES:
        if full_domain.endswith("." + suffix) or full_domain == suffix:
            tenant = full_domain[: -(len(suffix) + 1)] if full_domain != suffix else ""
            labels = [lbl for lbl in tenant.split(".") if lbl and lbl not in GENERIC_SUBDOMAIN_LABELS]
            if labels:
                return prettify_domain(labels[-1])
            break
    bare = full_domain.split(".")[0]
    if bare and bare not in GENERIC_SUBDOMAIN_LABELS and not re.search(
        r"no-?reply|donotreply|notification|alerts?|updates?|careers?|jobs?|recruit|talent|hiring|mail",
        full_domain,
    ):
        return prettify_domain(bare)
    return ""


def company_from_sender(sender: str, subject: str = "") -> str:
    return (company_from_bracket(subject)
            or company_from_display_name(sender)
            or company_from_domain(sender))


def company_fallback(sender: str) -> str:
    domain_match = DOMAIN_RE.search(sender or "")
    if not domain_match:
        return "Unknown"
    labels = domain_match.group(1).lower().split(".")
    while len(labels) > 1 and (len(labels[-1]) <= 3 or labels[-1] in ("com", "net", "org", "io", "dev")):
        labels.pop()
    domain = ".".join(labels)
    cleaned = re.sub(
        r"^(?:www|mail|no-?reply|donotreply|notifications?|alerts?|updates?|careers?|jobs?|"
        r"recruit\w*|talent|hiring|hr|team|message)[.-]", "", domain)
    if not cleaned or cleaned in ("gmail", "outlook", "yahoo", "hotmail", "icloud", "proton"):
        return "Unknown"
    return prettify_domain(cleaned)


def role_from_subject(subject: str) -> str:
    patterns = [
        r"(?i)^thank(?:s| you)(?: so much)? for (?:applying|your application|your interest)"
        r"\s+(?!to\b|at\b|with\b|for\b|in\b)([^|·—]{3,100})",
        r"(?i)^your (?:recent )?application for\s+(?!the\s+role\b)([^|·—]{3,100})",
        r"(?:application (?:for|to|received)[:\s-]+)([^|·—-]{3,80})",
        r"(?:for the role of[:\s-]+|for the position of[:\s-]+|role[:\s-]+)([^|·—()-]{3,80})",
        # Require real punctuation after "your application" — "Your application was
        # successfully submitted" must NOT become a role.
        r"(?:your application\s*[:\-–—]\s*)([^|·—()-]{3,80})",
        r"(?:position|role)[:\s]+([^|·—()-]{3,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, subject or "")
        if match:
            role = clean_role_name(match.group(1))
            if len(role) >= 3:
                return role
    return ""


def role_from_url_slug(url: str) -> str:
    match = re.search(r"/jobs?/view/([a-z0-9][a-z0-9-]{5,})", (url or "").lower())
    if not match:
        return ""
    slug = re.sub(r"-\d{6,}.*$|-[a-z]{2}-[a-z]{2}.*$|\.[a-z]{2,3}$", "", match.group(1))
    role = re.sub(r"-+", " ", slug).strip()
    return role.title() if len(role) >= 6 else ""


# ------------------------------------------------------------------ free text


def extract_company_role(text: str) -> tuple[str, str]:
    """Best (company, role) candidates found anywhere in free text (either may be '')."""
    company, role = "", ""
    if not text:
        return company, role
    for pattern in COMPANY_ROLE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groupdict()
            cand_company = clean_company_name(groups.get("company") or "")
            cand_role = clean_role_name(groups.get("role") or "")
            if (not company and cand_company and len(cand_company) <= 80
                    and not is_platform_company(cand_company) and plausible_company(cand_company)):
                company = cand_company
            if not role and cand_role and looks_like_role(cand_role):
                role = cand_role
            if company and role:
                return company, role
    return company, role


def role_from_application_snippet(snippet: str, company: str = "") -> str:
    """Job title from confirmation snippets that don't say "position of X".

    Shapes handled: "Your application was sent to <Company> <Role> <Company> <Location>",
    "You've applied to <Role> (…)", and a short snippet that is just the job title.
    """
    text = sanitize_body(snippet or "")[:300].strip()
    if not text:
        return ""
    if company and company.lower() in text.lower():
        match = re.search(r"(?i)application was sent to\s+" + re.escape(company) + r"\s+(.+)", text)
        if match:
            rest = re.split(re.escape(company), match.group(1), flags=re.IGNORECASE)[0]
            rest = re.split(r"(?i)\b(?:Federal Territory|Kuala Lumpur|Selangor|Malaysia|Remote|"
                            r"Job type|Full[- ]time)\b", rest)[0]
            role = clean_role_name(rest)
            if role and looks_like_role(role):
                return role[:110]
    match = re.search(r"(?i)you(?:'ve| have) applied (?:to|for)\s+(.+?)(?:\s*[\(|]|$)", text)
    if match:
        role = clean_role_name(match.group(1))
        if role and looks_like_role(role):
            return role[:110]
    if len(text) <= 110 and "\n" not in text:
        candidate = clean_role_name(text)
        if company and candidate:
            first = company.split()[0].lower() if company.split() else ""
            if first and candidate.lower().startswith(first) and " - " in candidate:
                candidate = clean_role_name(candidate.split(" - ", 1)[1])
        if candidate and ROLE_WORD_RE.search(candidate) and looks_like_role(candidate):
            return candidate[:110]
    return ""


def refine_company_role(company: str, role: str, subject: str) -> tuple[str, str]:
    """Replace platform-sender company names with the employer named in the role/subject."""
    company = (company or "").strip()
    stripped = strip_platform_suffix(company)
    if stripped and stripped.lower() != company.lower() and not is_platform_company(stripped):
        company = clean_company_name(stripped)
    best_company, best_role = "", ""
    for text in (role or "", subject or ""):
        if not text:
            continue
        for pattern in COMPANY_ROLE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            groups = match.groupdict()
            cand_company = clean_company_name(groups.get("company") or "")
            cand_role = clean_role_name(groups.get("role") or "")
            if (cand_company and not best_company and not is_platform_company(cand_company)
                    and plausible_company(cand_company)):
                best_company = cand_company
            if cand_role and not best_role and looks_like_role(cand_role):
                best_role = cand_role
    if best_company and is_platform_company(company):
        company = best_company
    if best_role:
        role = best_role
    return company, role


# ------------------------------------------------------------------ links & platform


def extract_job_url(body_text: str) -> str:
    """The most posting-like link in a body: context words, platform hosts and job paths
    score up; unsubscribe / social / tracking / image links score far down."""
    if not body_text:
        return ""
    best_score, best_url = 0, ""
    for match in _LINK.finditer(body_text):
        cleaned = strip_tracking_params(match.group(0))
        context = body_text[max(0, match.start() - 120): match.start()].lower()
        low = cleaned.lower()
        score = 0
        if any(p.search(context) for p in _URL_CONTEXT):
            score += 4
        if URL_PLATFORM_HINT.search(low):
            score += 3
        if URL_JOB_HINT.search(low):
            score += 2
        if URL_EXCLUDE.search(low):
            score -= 10
        if score > best_score:
            best_score, best_url = score, cleaned
    return best_url.rstrip(".,;:)!?") if best_score > 0 else ""


def detect_platform(sender: str, urls: str = "") -> str:
    domain_match = DOMAIN_RE.search(sender or "")
    if domain_match:
        domain = domain_match.group(1).lower()
        for platform, suffixes in PLATFORM_DOMAINS:
            if any(suffix in domain for suffix in suffixes):
                return platform
        if any(domain.endswith("." + s) or domain.endswith(s) for s in ATS_DOMAIN_SUFFIXES):
            return "Direct ATS"
    if urls and URL_PLATFORM_HINT.search(urls.lower()):
        for platform, host in (("LinkedIn", r"linkedin\.com/jobs"), ("JobStreet", r"jobstreet\."),
                               ("Hiredly", r"hiredly\."), ("Prosple", r"prosple\.")):
            if re.search(host, urls, re.IGNORECASE):
                return platform
        return "Direct ATS"
    return "Other"
