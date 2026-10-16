"""A synthetic Gmail mailbox for the public demo, screenshots and end-to-end tests.

It generates ~6 months of a fictional analyst's job search — confirmations from LinkedIn,
JobStreet, Hiredly, Prosple, Indeed and employer ATS sites, assessment invitations,
interviews, rejections, an offer — mixed with the noise a real inbox carries (job alerts,
digests, account verification, marketing). Every message then goes through the *real*
sync engine and parser, so the demo exercises the production path, not a mock-up.

All companies and people are fictional. Output is deterministic for a given seed and day.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

COMPANIES = [
    # (name, email domain, industry hint)
    ("Bayu Analytics", "bayuanalytics.com.my", "tech"),
    ("Cahaya Bank", "cahayabank.com.my", "bank"),
    ("Delima Takaful", "delimatakaful.com", "insurance"),
    ("Embun Assurance", "embunassurance.com", "insurance"),
    ("Fajar Re", "fajar-re.com", "insurance"),
    ("Gemilang Life Insurance", "gemilanglife.com.my", "insurance"),
    ("Harimau Capital", "harimaucapital.com", "bank"),
    ("Intan Data Labs", "intandatalabs.io", "tech"),
    ("Jentayu Consulting", "jentayuconsulting.com", "consulting"),
    ("Kenanga Health Plans", "kenangahealth.com.my", "insurance"),
    ("Lestari Energy", "lestarienergy.com.my", "energy"),
    ("Merbok Retail Group", "merbokretail.com", "retail"),
    ("Nusa Digital", "nusadigital.my", "tech"),
    ("Orkid Securities", "orkidsecurities.com", "bank"),
    ("Pelangi Logistics", "pelangilogistics.com", "retail"),
    ("Qaseh Actuarial Partners", "qasehactuarial.com", "actuarial"),
    ("Rimba Fintech", "rimbafintech.io", "tech"),
    ("Selasih General Insurance", "selasihgi.com.my", "insurance"),
    ("Teratai Advisory", "terataiadvisory.com", "consulting"),
    ("Ufuk Telco", "ufuktelco.com.my", "tech"),
    ("Wira Reinsurance", "wirare.com", "insurance"),
    ("Zamrud Holdings", "zamrudholdings.com.my", "bank"),
    ("Anggerik Pharma", "anggerikpharma.com", "retail"),
    ("Bistari Payments", "bistaripay.com", "tech"),
    ("Cempaka Wealth", "cempakawealth.com.my", "bank"),
    ("Dahlia Mutual", "dahliamutual.com", "insurance"),
]

ROLES = [
    ("Data Analyst", 10), ("Business Intelligence Analyst", 7), ("Actuarial Analyst", 7),
    ("Pricing Analyst", 5), ("Risk Analyst", 5), ("Reporting Analyst", 4),
    ("Graduate Data Analyst", 4), ("Junior Data Scientist", 3), ("Analytics Executive", 3),
    ("Reserving Analyst", 3), ("Insights Analyst", 3), ("Data Analyst Intern", 2),
    ("Management Trainee - Analytics", 2), ("Credit Risk Analyst", 2),
    ("Business Analyst", 3), ("Senior Data Analyst", 1),
]

# platform -> (share of applications, P(any employer response), sender, subject template)
PLATFORMS = {
    "LinkedIn": (0.30, 0.16),
    "JobStreet": (0.27, 0.27),
    "Direct ATS": (0.17, 0.46),
    "Hiredly": (0.10, 0.25),
    "Prosple": (0.08, 0.30),
    "Indeed": (0.08, 0.14),
}

FIRST_NAMES = ["Aina", "Daniel", "Mei Ling", "Hafiz", "Priya", "Jason", "Nurul", "Kevin"]


@dataclass
class DemoMessage:
    id: str
    thread_id: str
    when: datetime
    sender: str
    subject: str
    snippet: str
    body: str

    def meta(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "internal_date": int(self.when.timestamp() * 1000),
            "snippet": self.snippet,
            "from": self.sender,
            "subject": self.subject,
            "date_header": self.when.strftime("%a, %d %b %Y %H:%M:%S +0800"),
            "gmail_link": "",
        }


def _weighted(rng: random.Random, pairs):
    total = sum(w for _, w in pairs)
    pick = rng.uniform(0, total)
    for value, weight in pairs:
        pick -= weight
        if pick <= 0:
            return value
    return pairs[-1][0]


def _slug(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


class DemoMailbox:
    """Implements the ``MailSource`` protocol over generated messages."""

    def __init__(self, seed: int = 7, days: int = 180, today: date | None = None,
                 applications: int = 165):
        self.rng = random.Random(seed)
        # Calendar draws use their own stream, so the story (who replies, who offers) is the
        # same whatever day the demo is built on.
        self.date_rng = random.Random(seed * 7919)
        self.today = today or date.today()
        self.days = days
        self.messages: list[DemoMessage] = []
        self._n = 0
        self._generate(applications)
        self.messages.sort(key=lambda m: m.when)
        self._by_id = {m.id: m for m in self.messages}

    # ------------------------------------------------------------------ MailSource
    def profile(self) -> dict:
        return {"emailAddress": "demo.candidate@example.com", "historyId": "demo"}

    def search(self, after: str, cap: int) -> list[str]:
        floor = datetime.strptime(after, "%Y/%m/%d")
        return [m.id for m in self.messages if m.when >= floor][:cap]

    def metadata(self, message_ids: list[str]) -> list[dict]:
        return [self._by_id[i].meta() for i in message_ids if i in self._by_id]

    def bodies(self, message_ids: list[str]) -> dict[str, str]:
        return {i: self._by_id[i].body for i in message_ids if i in self._by_id}

    # ------------------------------------------------------------------ generation
    def _id(self) -> str:
        self._n += 1
        return f"demo{self._n:05d}"

    def _at(self, day: date, hour_lo: int = 8, hour_hi: int = 19) -> datetime:
        return datetime.combine(day, time(self.rng.randint(hour_lo, hour_hi),
                                          self.rng.randint(0, 59)))

    def _add(self, thread: str, when: datetime, sender: str, subject: str, body: str) -> None:
        if when.date() > self.today:
            return  # the future has not happened yet
        snippet = " ".join(body.split())[:180]
        self.messages.append(DemoMessage(self._id(), thread, when, sender, subject, snippet, body))

    def _application_days(self, count: int) -> list[date]:
        start = self.today - timedelta(days=self.days)
        days: list[date] = []
        while len(days) < count:
            offset = int(self.date_rng.betavariate(1.25, 1.0) * self.days)
            day = start + timedelta(days=offset)
            # weekday bias, with the occasional weekend session
            if day.weekday() >= 5 and self.date_rng.random() < 0.7:
                continue
            days.append(day)
        return sorted(days)

    def _generate(self, count: int) -> None:
        platform_pairs = [(p, share) for p, (share, _) in PLATFORMS.items()]
        for i, applied in enumerate(self._application_days(count)):
            company, domain, _ = self.rng.choice(COMPANIES)
            role = _weighted(self.rng, ROLES)
            platform = _weighted(self.rng, platform_pairs)
            thread = f"demo-thread-{i:04d}"
            self._confirmation(thread, applied, company, domain, role, platform)
            self._outcome(thread, applied, company, domain, role, platform)
        self._noise()

    def _confirmation(self, thread, day, company, domain, role, platform):
        when = self._at(day)
        if platform == "LinkedIn":
            self._add(thread, when, "LinkedIn <jobs-noreply@linkedin.com>",
                      f"{_first_name(self.rng)}, your application was sent to {company}",
                      f"Your application was sent to {company} {role} {company} "
                      "Kuala Lumpur, Federal Territory of Kuala Lumpur, Malaysia (On-site) "
                      f"Applied on {day:%B %d, %Y}. "
                      f"View job https://www.linkedin.com/jobs/view/{_slug(role)}-{4100000000 + self._n}")
        elif platform == "JobStreet":
            self._add(thread, when, "JobStreet <noreply@s.jobstreet.com>",
                      f"Your application for {role} was successfully submitted to {company}",
                      f"Hi there, your application for {role} was successfully submitted to "
                      f"{company}. The employer will be in touch if your application is "
                      f"shortlisted. View your application https://my.jobstreet.com/job/{70000000 + self._n}")
        elif platform == "Direct ATS":
            tenant = _slug(company)[:12]
            self._add(thread, when, f"{company} Careers <{tenant}@myworkday.com>",
                      f"Thank you for applying to {company} for {role}",
                      f"Dear Candidate, thank you for your application for the {role} position "
                      f"at {company}. We have received your application and our talent "
                      "acquisition team is reviewing it. "
                      f"Candidate Home https://{tenant}.wd3.myworkdayjobs.com/en-US/careers/job/{_slug(role)}")
        elif platform == "Hiredly":
            self._add(thread, when, "Hiredly <hello@hiredly.com>",
                      f"Application Received - {role} at {company}",
                      f"Your application for {role} at {company} has been received. "
                      f"Track your application https://my.hiredly.com/jobs/{_slug(role)}-{self._n}")
        elif platform == "Prosple":
            self._add(thread, when, "Prosple <noreply@prosple.com>",
                      f"Your application to {company} - {role}",
                      f"Thanks for applying! Your application to {company} for the {role} role "
                      "has been submitted.")
        else:  # Indeed
            self._add(thread, when, "Indeed Apply <indeedapply@indeed.com>",
                      f"Your application for {role} at {company}",
                      f"Application submitted. We have received your application for {role} "
                      f"at {company}. Good luck!")

    def _outcome(self, thread, applied, company, domain, role, platform):
        rng = self.rng
        recruiter = f"{company} Talent Acquisition <careers@{domain}>"
        p_response = PLATFORMS[platform][1]
        if rng.random() > p_response:
            return  # silence — the ghosting rule will close it
        day = applied + timedelta(days=int(rng.gammavariate(2.2, 5.5)) + 1)
        roll = rng.random()
        # A third of responses are straight rejections; the rest move forward.
        if roll < 0.38:
            self._rejection(thread, day, company, role, recruiter)
            return
        if roll < 0.72:
            self._add(thread, self._at(day), recruiter, f"Online Assessment Invitation - {role}",
                      f"Dear Candidate, thank you for your interest in the {role} role at "
                      f"{company}. As the next step, we invite you to complete the online "
                      "assessment on HackerRank within 5 days. "
                      "Start the assessment https://www.hackerrank.com/tests/abc123")
            day = day + timedelta(days=int(rng.gammavariate(2.0, 4.0)) + 2)
            if rng.random() < 0.55:
                self._rejection(thread, day, company, role, recruiter)
                return
        self._add(thread, self._at(day), recruiter, f"Interview Invitation: {role} at {company}",
                  f"Dear Candidate, we were impressed by your application and we would like to "
                  f"invite you to an interview for the {role} position. Please share your "
                  "availability for a 45-minute video interview with the hiring manager next week.")
        day = day + timedelta(days=int(rng.gammavariate(2.0, 5.0)) + 3)
        outcome = rng.random()
        if outcome < 0.24:
            self._add(thread, self._at(day), recruiter, f"Offer of Employment - {role}",
                      f"Dear Candidate, congratulations! We are pleased to offer you the position "
                      f"of {role} at {company}. Please find the offer letter attached.")
        elif outcome < 0.75:
            self._rejection(thread, day, company, role, recruiter)
        elif outcome < 0.9:
            self._add(thread, self._at(day), recruiter, f"Final Interview - {role}",
                      f"Thank you for meeting the team. We would like to invite you to a final "
                      f"interview with the Head of Analytics for the {role} position.")

    def _rejection(self, thread, day, company, role, recruiter):
        self._add(thread, self._at(day), recruiter,
                  f"Update on your application for {role} at {company}",
                  f"Dear Candidate, thank you for applying for the {role} position at {company}. "
                  "After careful consideration, we regret to inform you that we have decided to "
                  "move forward with other candidates whose experience more closely matches the "
                  "role. We wish you every success.")

    def _noise(self) -> None:
        rng = self.rng
        start = self.today - timedelta(days=self.days)
        templates = [
            ("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
             "Job Alert: {n} new Data Analyst jobs in Kuala Lumpur",
             "Jobs you may be interested in. Data Analyst - {company}. Apply now."),
            ("JobStreet <noreply@s.jobstreet.com>", "Recommended jobs for you",
             "Based on your profile, here are jobs picked for you this week."),
            ("LinkedIn <notifications-noreply@linkedin.com>",
             "You appeared in {n} searches this week",
             "See who's looking at your profile and grow your network."),
            ("Hiredly <hello@hiredly.com>", "Your weekly digest: companies hiring now",
             "Top companies are hiring. Check out these opportunities and career resources."),
            ("{company} Careers <noreply@myworkday.com>", "Verify your candidate account",
             "Please verify your email address to activate your candidate account."),
            ("Kopi Express <promo@kopiexpress.my>", "Special offer: up to 30% off this weekend",
             "Your exclusive discount is waiting. Terms and conditions apply."),
            ("Glassdoor <noreply@glassdoor.com>", "Salary insights for Data Analyst",
             "Salary guide 2026: see what data analysts earn in Malaysia."),
            ("Prosple <noreply@prosple.com>", "Career fair this Saturday",
             "Join our virtual career fair and meet graduate employers."),
            ("Quora Digest <digest-noreply@quora.com>", "What skills does a data analyst need?",
             "Top stories for you from the Quora digest."),
        ]
        for _ in range(int(self.days * 1.4)):
            sender, subject, body = rng.choice(templates)
            company = rng.choice(COMPANIES)[0]
            day = start + timedelta(days=rng.randint(0, self.days))
            fill = {"n": rng.randint(5, 60), "company": company}
            self._add(f"demo-noise-{self._n:05d}", self._at(day, 6, 22),
                      sender.format(**fill), subject.format(**fill), body.format(**fill))


def _first_name(rng: random.Random) -> str:
    return rng.choice(FIRST_NAMES)
