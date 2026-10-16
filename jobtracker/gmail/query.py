"""The Gmail search: what counts as candidate job mail before any parsing happens.

Gmail's ``q`` syntax is a conjunction of OR-groups. To avoid missing real application mail
the search matches on three independent signals:

1. sender domains — job boards / ATS platforms applications are sent through
2. subject terms  — application / interview / assessment wording
3. body phrases   — so employer-hosted ATS domains we cannot enumerate still match

then subtracts known marketing templates. Spam is included (``in:anywhere -in:trash``)
because Gmail occasionally misroutes real confirmations there; the parser removes noise.
"""
from __future__ import annotations

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


# Two independent searches, unioned: each stays well inside Gmail's query-length limit and
# neither can crowd the other out of the result cap.
_SCOPE = "in:anywhere -in:trash"
_EXCLUDE = f"-subject:({_gmail_or(SEARCH_EXCLUDE_SUBJECTS)})"
SEARCH_QUERIES = [
    f"{_SCOPE} (from:({_gmail_or(SEARCH_SENDER_DOMAINS)})) {_EXCLUDE}",
    f"{_SCOPE} (subject:({_gmail_or(SEARCH_SUBJECT_TERMS)}) "
    f"OR {_gmail_or(SEARCH_BODY_PHRASES)}) {_EXCLUDE}",
]
