import html as html_lib
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

STATUSES = ["Saved / Planning", "Applied", "Assessment / OA", "Interview", "Offer", "Rejected", "Ghosted"]
STATUS_RANK = {s: i for i, s in enumerate(STATUSES)}

PLATFORMS = ["LinkedIn", "JobStreet", "Hiredly", "Prosple", "Direct ATS", "Other"]

ATS_DOMAIN_SUFFIXES = [
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkday.com",
    "myworkdaysite.com", "smartrecruiters.com", "icims.com", "taleo.net", "jobvite.com",
    "successfactors.com", "workable.com", "bamboohr.com", "applytojob.com",
    "recruitee.com", "jobscore.com", "teamtailor.com", "breezy.hr", "rolp.co",
]

PLATFORM_DOMAINS = [
    ("LinkedIn", ("linkedin.com", "licdn.com")),
    ("JobStreet", ("jobstreet.com",)),
    ("Hiredly", ("hiredly.com", "wobb.com")),
    ("Prosple", ("prosple.com", "gradaustralia.com")),
]

STATUS_KEYWORDS = [
    ("Offer", [
        r"\boffer (?:letter|of employment|for the)\b",
        r"\bpleased to offer\b",
        r"\bwe(?:'d| would| are) (?:like to|pleased to|delighted to) offer(?: you)?\b",
        r"\bextend(?:ing|ed)? (?:an?|this|the) offer\b",
        r"\bjob offer\b",
        r"\bverbal offer\b",
        r"\boffer (?:package|details|to join|to you)\b",
        r"\bcongratulations\b[^.\n]{0,80}\b(?:offer|position|role|selected|successful)\b",
    ]),
    ("Interview", [
        r"\binvitation to (?:an? )?interview\b",
        r"\binterview invitation\b",
        r"\b(?:we(?:'d| would)? ?(?:like|love|be delighted|be pleased) to|"
        r"we(?:'re| are) (?:pleased|delighted|happy|excited) to|"
        r"i(?:'d| would)? ?(?:like|love) to) invite you\b",
        # Guard against hypotheticals ("we may/will/could invite you", "if we invite you").
        r"(?<!\bmay )(?<!\bmight )(?<!\bcould )(?<!\bwill )(?<!\bwould )(?<!\bif we )"
        r"\binvite you (?:to|for) (?:an? )?"
        r"(?:interview|call|chat|conversation|meeting|catch[- ]?up)\b",
        r"(?<!\bmay )(?<!\bmight )(?<!\bcould )(?<!\bwill )(?<!\bwould )"
        r"\b(?:schedule|arrange|book|set up|confirm)[^.\n]{0,40}\binterview\b",
        r"\binterview[^.\n]{0,30}\b(?:scheduled|confirmed|arranged|invitation)\b",
        r"\b(?:shortlisted|selected)\b\s*,?\s*\bfor (?:an? )?interview\b",
        r"\b(?:phone|video|virtual|panel|final|first|second|initial)[ -]?(?:round )?interview\b",
        r"\bhiring manager (?:would like|wants|will|is keen)\b",
        r"\bnext (?:round|stage)[^.\n]{0,30}\b(?:interview|call|conversation|discussion)\b",
        r"\bdiscovery call\b",
        r"\bphone screen\b",
        r"\binterview (?:with|at)\b[^.\n]{0,60}\b(?:team|manager|panel)\b",
    ]),
    ("Assessment / OA", [
        r"\bonline assessment\b",
        r"\bassessment (?:invitation|link|platform|task|centre|center|round)\b",
        r"\b(?:complete|take|start|attempt|finish)[^.\n]{0,40}\b(?:assessment|test|challenge|exercise|task)\b",
        r"\b(?:hacker\s?rank|codesignal|codility|hirevue|testgorilla|\bshl\b|criteria corp|"
        r"pymetrics|codingame|hackerearth|devskiller|imocha|vidcruiter|spark hire|berke|"
        r"wonderlic|predictive index|thomas international|\bcut[- ]?e\b)\b",
        r"\btechnical (?:assessment|test|challenge)\b",
        r"\baptitude test\b",
        r"\bscreen(?:ing)? call\b",
        r"\bscreen(?:ing)? questions\b",
        r"\bpending tasks?\b",
        r"\b(?:pre[- ]?employment|background|reference) (?:check|screening)\b",
    ]),
    ("Rejected", [
        r"\bnot moving forward\b",
        r"\bunsuccessful\b",
        r"\bnot successful\b",
        r"\b(?:pursue|chose|choose|selected|moving forward with|gone with|advancing) "
        r"(?:other|another) candidates?\b",
        r"\bdecided to (?:pursue|move forward with|progress|advance) (?:other|another)\b",
        r"\bregret to inform\b",
        r"\bwe regret\b",
        r"\bwent with (?:another|other) candidate",
        r"\bposition (?:has been|is|was) filled\b",
        r"\bnot (?:be )?(?:moving|able to move) forward\b",
        r"\bnot (?:been )?shortlisted\b",
        r"\bno longer under consideration\b",
        r"\bapplication [^.;]{0,90}not successful\b",
        r"\bunable to (?:move|proceed|progress|advance)\b",
        r"\bnot met (?:the|all) (?:criteria|requirements|shortlisting)\b",
        r"\bwill not be progress(?:ing)?\b",
        r"application will not progress",
        r"(?:unlikely|unfortunately|regrettably)[^.;]{0,80}\bprogress\b",
        # "unfortunately" only counts with explicit rejection context (confirmations also
        # say "unfortunately we receive a high volume of applications").
        r"\bunfortunately[^.;]{0,120}\b(?:not (?:selected|successful|be moving)|"
        r"other candidates?|unsuccessful|we (?:have )?decided|position (?:has been|is) filled|"
        r"unable to|we (?:will|won't|cannot|can't))\b",
        r"\bafter careful (?:consideration|review)[^.;]{0,120}\b(?:chosen|selected|decided|"
        r"other candidates?)\b",
    ]),
    ("Applied", [
        r"\bthank(?:s| you)(?: so much)? for (?:your )?(?:recent |job )?(?:application|applying|interest)\b",
        r"\bapplication (?:has been|was|is) (?:successfully )?(?:received|submitted|sent)\b",
        r"\bwe(?:'ve| have) received your application\b",
        r"\byour application (?:was|has been) (?:successfully )?(?:received|submitted|sent)\b",
        r"\bconfirmation of your (?:job )?application\b",
        r"\bapplication submitted\b",
        r"\bapplication update for\b",
        r"\byour application (?:to|for)\b",
        r"\backnowledg\w* (?:the )?receipt\b",
        r"\bcandidate home\b",
        r"\bresume (?:has been|was) (?:approved|received|shortlisted)\b",
        r"\breviewing your (?:resume|application|candidacy)\b",
    ]),
]

SUBJECT_PATTERNS = [
    re.compile(r"Thank you for applying to (?P<company>.*?) for (?P<role>.*)", re.IGNORECASE),
    re.compile(r"Your application to (?P<company>.*?) - (?P<role>.*)", re.IGNORECASE),
    re.compile(r"Application (?:Received|Confirmation) - (?P<role>.*?) at (?P<company>.*)", re.IGNORECASE),
    re.compile(r"(?P<company>.*?) - (?:Application|Interview) for (?P<role>.*)"),
    re.compile(r"Your (?:application|job application|interview)(?: status)?(?: update)? for (?P<role>.*?) at (?P<company>.*)", re.IGNORECASE),
    re.compile(r"Update (?:on|regarding|about) your application (?:for|to) (?P<role>.*?) at (?P<company>.*)", re.IGNORECASE),
    re.compile(r"(?:Application|Interview) (?:update|invitation)[:\-] (?P<role>.*?) at (?P<company>.*)", re.IGNORECASE),
]

GARBAGE_KEYWORDS = [
    r"job alert", r"jobs? you may (?:be interested|like)", r"recommended jobs?",
    r"job recommendations?", r"new jobs? (?:in|for|near|matching)",
    r"based on your (?:profile|skills|search|activity)", r"weekly job picks?",
    r"your (?:daily|weekly) (?:digest|briefing|mix|roundup)", r"\bdigest\b",
    r"newsletter", r"top stories?", r"stories (?:for|this) you",
    r"tips for your (?:interview|job search|career)", r"interview tips",
    r"career advice", r"career insights?", r"salary insights?",
    r"you have \d+ new (?:messages?|notifications?|invitations?)",
    r"check out these (?:jobs|companies|opportunities)",
    r"\d+ (?:new )?jobs? (?:posted|added|available)",
    r"more jobs? like", r"discover jobs?", r"explore (?:jobs|roles|opportunities)",
    r"hiring now in", r"trending jobs?", r"weekly job digest",
    r"career resources?", r"resume tips?", r"salary guide",
    # Social-network / platform housekeeping noise
    r"viewed your profile", r"appeared in \d+ searches?", r"noticed you",
    r"open to work", r"puzzle", r"verify your (?:email|device|identity|phone|new device)",
    r"shared a (?:post|photo|job|video)", r"and others (?:shared|posted)",
    r"terms (?:of|and)", r"profile (?:photo|name) was", r"follow\b",
    # Recruiter-platform housekeeping & unrelated marketing
    r"job[’']s expiring", r"expired on",
    r"still accepting applications",
    # Platform resume/account housekeeping (Hiredly, Prosple, etc.)
    r"resume (?:has been|was) approved", r"reviewing your resume",
    r"let'?s stay connected", r"we(?:'re| are) growing",
    r"have you finished your job applications?",
    r"create (?:your )?(?:hiredly|prosple|jobstreet) account",
    r"complete your (?:profile|account) to",
    # Job-board / recruiter marketing templates (very common false-positive sources)
    r"jobs? (?:picked|selected|suggested) for you", r"new jobs? (?:for you|in|matching)",
    r"jobs? added recently", r"we found \d+ (?:new )?jobs?",
    r"companies (?:are )?hiring", r"we(?:'re| are) hiring", r"\bnow hiring\b",
    r"open (?:roles|positions) (?:at|for)", r"meet the team", r"grab these jobs",
    r"career (?:fair|webinar|event|expo)", r"virtual career", r"hiring event",
    r"salary (?:report|guide|insights?)", r"resume (?:review|tips?)", r"interview tips",
    r"career (?:advice|coaching|resources?)", r"job search (?:tips|strategies)",
    r"talent (?:insights?|acquisition newsletter)", r"recruitment (?:trends?|newsletter)",
    r"top \d+ (?:jobs|companies|employers)",
    # Unrelated commercial email that keyword-trips recruiter words
    r"\brefund\b", r"trade[- ]?in", r"special offer", r"\bdiscount\b",
    r"brokerage", r"monthly subscription", r"up to \d+% off",
    # Non-job correspondence that trips "application/offer" words
    r"accommodation|campus (?:accommodation|tour|room)",
]

GARBAGE_SENDER_DOMAINS = {"quora", "quora-mail", "digest"}

# Stage 1 — hard blacklist: sponsored / marketing / automated digests.
# Matches against SUBJECT + SENDER only (not body) to avoid false positives.
SPONSOR_BLACKLIST = [
    r"sponsored",
    r"job alerts?",
    r"jobs? you may like",
    r"recommended for you",
    r"recommended jobs?",
    r"daily digest",
    r"weekly digest",
    r"top picks for you",
    r"similar jobs",
    r"join our talent network",
    r"join our talent (?:pool|community)",
    r"new opportunities matching",
    r"jobs? (?:picked|selected|suggested) for you",
    r"jobs for you",
    r"new jobs? (?:for you|in|near|matching)",
    r"more jobs? like",
    r"\bapply to\b",
    r"apply now to\b",
    r"invitation to apply",
    r"quora",
    # Account / identity housekeeping — a task, never an application event
    r"verify your (?:email|account|candidate|device|details|identity)",
    r"verification (?:code|email|link|process)",
    r"confirm your (?:email|account|identity)",
    r"confirm your identity",
    r"activate your account",
    r"reset your password",
    r"one[- ]?time (?:code|password|passcode)",
    r"candidate account",
    r"new device",
    # Platform digests / candidate-portal task notices (duplicates of real rows)
    r"new activity in jobs you applied for",
    r"activity in jobs you applied for",
    r"task awaits",
    r"pending tasks?",
    r"workfeed",
]

# Stage 2 — positive affirmation: a message is ONLY valid if it confirms an
# action already taken (an application) or is a direct recruitment response.
AFFIRMATION_PATTERNS = [
    # --- Explicit confirmations (STRONG — these win over marketing tails in the body) ---
    r"thank(?:s| you)(?: so much)? for (?:your )?(?:recent |job )?(?:application|applying|interest|considering)\b",
    r"application (?:was|has been|is|had been) (?:successfully )?(?:received|submitted|sent)",
    r"application [^.,;!?]{0,120}\b(?:received|submitted)\b",
    r"application [^.,;!?]{0,120}\bwas sent\b",
    r"your application (?:was|has been) sent",
    r"application received",
    r"application submitted",
    r"your application to",
    r"we(?:'ve| have)? received your application",
    r"we have received your application",
    r"acknowledg(?:e|ing|ement of) (?:the )?receipt of your application",
    r"we (?:would like to |hereby |are pleased to )?acknowledge (?:receipt of )?your application",
    r"in receipt of your application",
    r"your application (?:has been|was|is) (?:acknowledged|noted|logged|registered)",
    r"confirm(?:ing|ation of) your (?:job )?application",
    r"\bhas viewed your application\b",
    r"viewed your application for",
    r"an update to your (?:job )?application",
    r"application update[:\-]",
    r"application update for",
    r"candidate home",
    r"resume (?:has been|was) (?:approved|received|shortlisted|reviewed)",
    r"we (?:are|will be|'ll be) reviewing your (?:resume|application|candidacy)",
    r"you have pending tasks?",
    r"(?:a|your) task awaits",
    r"questions for external candidates",
    r"review candidate",
    r"thank you for your recent job application",
    r"job application for",
    r"we(?:'ll| will) be in touch[^.\n]{0,40}(?:about|regarding|with) your application",
    r"your application is (?:being|currently|under) (?:reviewed|considered|assessed|review)",
    r"application is under consideration",
    r"under consideration for (?:the |this )?(?:role|position)",
    # --- Direct recruitment responses / status changes (WEAK — garbage veto still applies) ---
    r"invitation to interview",
    r"interview invitation",
    r"invite you (?:to|for) (?:an? )?(?:interview|call|chat|conversation|meeting)",
    r"online assessment",
    r"complete (?:this|the|your) (?:assessment|test)",
    r"assessment invitation",
    r"\boa invitation\b",
    r"invitation (?:for|to) (?:the |an |your )?(?:online )?assessment",
    r"regret to inform",
    r"not moving forward",
    r"unsuccessful",
    r"other candidates",
    r"not (?:been )?shortlisted",
    r"application (?:was )?not successful",
    r"job with [^\n]{0,90}has closed",
    r"\bshortlisted\b",
    r"\bnext (?:round|stage)[^.\n]{0,30}\b(?:process|application|interview|assessment|selection)\b",
    r"\bassessment (?:invitation|link|platform|task)\b",
    r"\bhiring manager (?:would like|wants|will)\b",
    r"\brecruiter (?:will|to|would) (?:reach|contact|be in touch)\b",
    r"\b(?:pre[- ]?employment|background|reference) (?:check|screening)\b",
    # --- Contextual "offer" — never a bare match (avoids promos like "special offer: RM15.90") ---
    r"\boffer letter\b",
    r"pleased to offer",
    r"offer of employment",
    r"\bjob offer\b",
    r"we(?:'d| would| are) (?:like to|pleased to|delighted to) offer(?: you)?\b",
    r"extend(?:ed|ing)? (?:an |this |the )?offer",
    r"offer to join",
    r"congratulations[^\n]{0,80}\boffer\b",
    r"\boffer\b[^\n]{0,60}\b(?:position|role|employment|join us|onboard)\b",
]

STRONG_AFFIRMATION_PATTERNS = [
    # Application-specific "thank you" — NOT the bare "thanks for subscribing/purchasing".
    r"thank(?:s| you)(?: so much)? for[^.\n]{0,60}\b(?:appl(?:ying|ication|y|ied)|"
    r"your (?:recent )?(?:application|interest)|your interest|considering (?:me|my))\b",
    r"application (?:was|has been|is|had been) (?:successfully )?(?:received|submitted|sent)",
    r"application [^.,;!?]{0,120}\b(?:received|submitted)\b",
    r"application [^.,;!?]{0,120}\bwas sent\b",
    r"application received",
    r"application submitted",
    r"your application (?:was|has been) sent",
    r"confirm(?:ing|ation of) your (?:job )?application",
    r"\bhas viewed your application\b",
    r"viewed your application for",
    r"an update to your (?:job )?application",
    r"application update[:\-]",
    r"application update for",
    r"thank you for your recent job application",
    r"we(?:'ve| have) received your application",
    r"acknowledg(?:e|ing|ement of) (?:the )?receipt of your application",
    r"we (?:would like to |hereby |are pleased to )?acknowledge (?:receipt of )?your application",
    r"in receipt of your application",
    r"your application (?:was|has been) (?:successfully )?(?:received|submitted|sent)",
]

BLACKLIST_SENDER_DOMAINS = {"quora.com", "quora-mail.bounce", "digest"}
BLACKLIST_SENDER_HINTS = ("jobalert", "job-alert", "newsletter", "digest@", "promotions")

# Non-job commercial/correspondence terms that veto even strong confirmations
# (an accommodation portal also writes "your application has been received").
HARD_GARBAGE = [
    r"\brefund\b", r"trade[- ]?in", r"special offer", r"\bdiscount\b",
    r"brokerage", r"monthly subscription", r"up to \d+% off",
    r"accommodation",
]

# Loose-tier senders: known job platforms / ATS whose subjects may be bare job titles
LOOSE_PLATFORM_SENDERS = (
    "jobstreet", "myworkday", "workday", "myhr", "hiredly", "prosple", "wobb",
    "icims", "greenhouse", "lever.co", "ashby", "smartrecruiters", "workable",
    "taleo", "jobvite", "oraclecloud", "recruitee", "teamtailor", "successfactors",
    "sapsf", "applytojob", "jobscore", "talentify", "zoho", "mycareerx", "hrmos",
    "pageuppeople", "avature", "phenompeople", "eightfold", "cornerstoneondemand",
    "brassring", "kenexa", "njoyn", "erecruit", "breezy", "rolp.co",
    "hirevue", "hackerrank", "codility", "testgorilla", "shl.com", "criteria.com",
    "pymetrics", "codingame", "hackerearth", "devskiller", "imocha", "vidcruiter",
    "sparkhire", "berke", "wonderlic", "predictiveindex", "thomas.co",
)
LOOSE_SUBJECT_RE = re.compile(
    r"application|applied|candidacy|\bresume\b|\bat\s+[^.!?]{2,60}\s*$", re.IGNORECASE)


def _is_platform_sender(sender: str) -> bool:
    s = (sender or "").lower()
    return any(p in s for p in LOOSE_PLATFORM_SENDERS)


# ------------------------------------------------------------------ categorisers

JOB_TYPE_RULES = [
    ("Internship", r"\bintern(?:ship)?\b|industrial (?:train\w+|attach\w+)|practicum|work placement"),
    ("Graduate Programme / Trainee", r"management train\w+|graduate train\w+|graduate (?:programme|program)|\bprotege\b|proteg[eé]|\bgees\b|g\.r\.e\.a\.t\.|employability attach\w+|apprentice|\btrainee\b"),
    ("Contract / Fixed-Term", r"\(\s*\d+\s*[-–]?\s*months?\s*\)|\d+\s*[-–]?\s*months?\s+contract|contract basis|\bcontract\b|fixed[- ]term|\btemporary\b"),
    ("Part-Time / Casual", r"part[- ]time\b|\bcasual\b|freelance\b"),
]
DEFAULT_JOB_TYPE = "Full-Time"

SENIORITY_RULES = [
    ("Internship", r"\bintern(?:ship)?\b|industrial (?:train\w+|attach\w+)"),
    ("Graduate / Entry", r"management train\w+|graduate train\w+|fresh graduate|entry[- ]level|graduate (?:programme|program)|\bprotege\b|apprentice|\bgraduate\b"),
    ("Manager / VP+", r"\bmanager\b|\bavp\b|\bvp\d*\b|vice president|\bdirector\b|\bhead of\b|\bprincipal\b|\bchief\b"),
    ("Senior / Lead", r"\bsenior\b|\bsr\.?\b|\blead\b|\bspecialist\b"),
    ("Executive / Junior", r"\bexecutive\b|\bjunior\b|\btrainee\b|\bofficer\b|\bassistant\b|"
                           r"\bcoordinator\b|\brepresentative\b|\badmin(?:istrator)?\b"),
    ("Analyst / Associate", r"\banalyst\b|\bassociate\b|\bengineer\b|\bdeveloper\b|"
                            r"\bscientist\b|\bconsultant\b|\badvis(?:e)?r\b|\bdesigner\b|"
                            r"\barchitect\b"),
]
DEFAULT_SENIORITY = "Not Specified"

INDUSTRY_RULES = [
    ("Insurance", r"prudential|\baia\b|allianz|etiqa|msig|zurich|manulife|sun life|"
                  r"great eastern|generali|takaful|reinsur\w+|insurance|assurance|"
                  r"swiss re|munich re|\brga\b|axa\b|chubb|tokio marine|sompo|\bmnrb\b|"
                  r"health plans?|healthcare|underwrit\w*|bancassurance|"
                  r"loss adjust\w*"),
    ("Banking & Finance", r"\buob\b|\bcimb\b|maybank|\brhb\b|hong leong|public bank|"
                          r"bank islam|bank rakyat|bank negara|\bepf\b|kwsp|affin|ambank|"
                          r"standard chartered|hsbc|citibank|ocbc|\brbc\b|investment bank|"
                          r"asset management|\bsecurities\b|\bbank\b|\bbanking\b|"
                          r"\bkyc\b|\baml\b|treasury|\bcredit\b|\bloan\b|\bmortgage\b|"
                          r"wealth management|\bbroking\b"),
    ("Consulting & Advisory", r"deloitte|\bey\b|ernst & young|\bpwc\b|kpmg|mckinsey|"
                              r"\bbcg\b|\bbain\b|accenture|control risks|consulting|"
                              r"advisory|ipsos|market research|\bwns\b|outsourc\w*|\bbpo\b"),
    ("Actuarial & Risk Analytics", r"actuari\w+|milliman|wtw\b|willis towers|\baon\b|"
                                   r"mercer|nicholas actuarial|taylor fry|"
                                   r"risk (?:analyst|analytics|specialist|manager|modell?er|"
                                   r"management|data|underwriting)|\bcredit risk\b|"
                                   r"\bmarket risk\b|operational risk|\bvar\b"),
    ("Tech & Digital", r"shopee|tiktok|bytedance|\bgrab\b|foodpanda|touch 'n go|tng digital|"
                       r"lazada|google|microsoft|airasia|\btech\b|software|e-?commerce|"
                       r"fintech|cryocord|footfallcam|like bug|\btdcx\b|\bdigital\b|"
                       r"\bdata scientist\b"),
    ("Energy & Utilities", r"petronas|tenaga|petro\w+|exxon|\bgas\b|energy|utilities"),
    ("Government / GLC", r"akpk|perkeso|khazanah|ministry|statutory|government|\bglc\b|"
                         r"\bmara\b|\bkwsp\b|\bepf\b"),
    ("FMCG / Manufacturing / Retail", r"haleon|unilever|nestle|f&n\b|mattel|hartalega|"
                                      r"textile|manufactur\w+|\bretail\b|\bfmcg\b|\bdiy\b|"
                                      r"dfi retail|consumer goods|starhub|merchandis\w*|"
                                      r"commodit\w*|logistics|shipping|cma cgm|dreyfus"),
    ("Recruitment / Staffing", r"pasona|agensi pekerjaan|staffing|recruitment"),
    ("Other", None),
]
DEFAULT_INDUSTRY = "Other"


def classify_job_type(text: str) -> str:
    t = (text or "").lower()
    for label, pattern in JOB_TYPE_RULES:
        if re.search(pattern, t):
            return label
    return DEFAULT_JOB_TYPE


def classify_seniority(text: str) -> str:
    t = (text or "").lower()
    for label, pattern in SENIORITY_RULES:
        if re.search(pattern, t):
            return label
    return DEFAULT_SENIORITY


def classify_industry(text: str) -> str:
    t = (text or "").lower()
    for label, pattern in INDUSTRY_RULES:
        if pattern and re.search(pattern, t):
            return label
    return DEFAULT_INDUSTRY

URL_EXCLUDE = re.compile(
    r"unsubscribe|mailto:|privacy|terms|blog|help|support|faq|preferences|"
    r"facebook\.com|twitter\.com|x\.com|instagram\.com|youtube\.com|tiktok\.com|"
    r"threads\.net|whatsapp\.com|wa\.me|t\.me|"
    r"linkedin\.com/(?:in|company|school|feed)/|linkedin\.com/help|"
    r"jobstreet\.com\.my/company|hiredly\.com/company|"
    r"calendar\.google|zoom\.us|teams\.microsoft|meet\.google|calendly\.com|"
    r"docs\.google|drive\.google|dropbox\.com|google\.com/maps|maps\.app|goo\.gl/maps|"
    r"bit\.ly|tinyurl\.com|rb\.gy|shorturl\.at|"
    r"play\.google|apps\.apple|cdn\.|fonts\.|static\.|"
    r"silverpop|url\.jobstreet\.com/ss|/ss/c/|e2ma\.net|seekcdn\.com|"
    r"\.(?:png|jpe?g|gif|webp|svg|ico)(?:[?#]|$)",
    re.IGNORECASE,
)

DOMAIN_RE = re.compile(r"@((?:[\w-]+\.)+[A-Za-z]{2,})")

TRACKING_PARAMS_RE = re.compile(
    r"[?&](?:utm_[\w-]+|gclid|fbclid|mkt_tok|mc_[a-z]+|ref(?:errer)?|email(_|template|source))[=][^&\s]*",
    re.IGNORECASE,
)


def strip_tracking_params(url: str) -> str:
    cleaned = TRACKING_PARAMS_RE.sub("", url)
    # If the *first* query param was the tracking one, the next '&' must become '?'
    # ('.../job?utm_source=x&job=5' -> '.../job&job=5' -> '.../job?job=5').
    if "?" in url and "?" not in cleaned:
        cleaned = cleaned.replace("&", "?", 1)
    return cleaned.rstrip("?&")

URL_JOB_HINT = re.compile(
    r"view|job|apply|application|position|careers?|posting|role|vacancy|opening|requisition",
    re.IGNORECASE,
)

URL_PLATFORM_HINT = re.compile(
    r"|".join(
        [re.escape(d) for d in ATS_DOMAIN_SUFFIXES]
        + ["linkedin\\.com/jobs", "jobstreet\\.", "hiredly\\.", "prosple\\."]
    ),
    re.IGNORECASE,
)

URL_CONTEXT_HINT = [
    r"view (?:your )?application",
    r"job posting",
    r"view (?:this |the |your )?(?:job|role|position|posting)",
    r"job description",
    r"position details",
    r"apply (?:now|online|here)",
    r"click (?:here )?to (?:view|apply|see)",
    r"go to (?:the )?application",
    r"update your application",
    r"application (?:status|portal)",
    r"track (?:your )?application",
    r"track status",
    r"candidate home",
    r"access your (?:application|account)",
    r"complete your application",
]

SIGNATURE_MARKERS = [
    r"--\s*$",
    r"unsubscribe",
    r"privacy (?:policy|statement)",
    r"terms of (?:use|service)",
    r"you (?:are|have) receiv(?:ed|ing) this email",
    r"do not (?:reply|respond)",
    r"please do not (?:reply|respond)",
    r"this (?:email|message) (?:and any attachments )?(?:is|are) (?:intended|confidential)",
    r"intended (?:only )?(?:recipient|for the named)",
    r"view (?:this email )?in browser",
    r"manage (?:your )?(?:preferences|subscription|settings)",
    r"all rights reserved",
    r"sent from my",
    r"get outlook for",
    r"want to change how you receive these emails",
]


def parse_message(message: dict, body_text: str = "", allow_llm: bool = True) -> dict:
    subject = message.get("subject") or "(no subject)"
    sender = message.get("from", "")
    ts = message.get("internal_date", 0)
    date_iso = (
        # internalDate is epoch-ms; local date is what the user actually applied on.
        datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        if ts
        else _date_from_header(message.get("date_header", ""))
    )

    subj_company, subj_role = _parse_subject(subject)
    is_valid, filter_reason = classify_message(sender, subject, message.get("snippet", ""))
    result = {
        "thread_id": message["thread_id"],
        "company_name": subj_company or _company_from_sender(sender, subject),
        "role_title": subj_role or _extract_role(subject),
        "source_platform": _detect_platform(sender, "", ""),
        "application_date": date_iso,
        "current_status": _detect_status(subject + " " + message.get("snippet", "")),
        "last_updated": date_iso,
        "job_description_snippet": "",
        "job_url": "",
        "gmail_link": message.get("gmail_link", ""),
        "latest_subject": subject,
        "is_valid": is_valid,
        "filter_reason": filter_reason,
        "needs_body": False,
        "used_llm": False,
    }

    if body_text:
        sanitized = sanitize_body(body_text)
        result["job_url"] = extract_job_url(body_text)
        result["job_description_snippet"] = sanitized[:600]
        body_valid, body_reason = classify_message(sender, subject, sanitized)
        result["is_valid"] = body_valid
        result["filter_reason"] = body_reason
        if result["source_platform"] == "Other":
            result["source_platform"] = _detect_platform(sender, "", result["job_url"])
        # The body often names the real employer/role even when the subject is generic
        # ("Your application was successfully submitted"). Use the sanitised text (no URLs)
        # so a link can never be captured as a company name.
        body_company, body_role = extract_company_role(sanitized)
        if not body_role:
            body_role = role_from_application_snippet(
                sanitized, result.get("company_name") or body_company or "")
        if body_company and not _is_platform_company(body_company):
            if not result["company_name"] or _is_platform_company(result["company_name"]):
                result["company_name"] = body_company
        if body_role and (not result["role_title"] or not _looks_like_role(result["role_title"])
                          or result["role_title"].lower() == subject.lower()):
            result["role_title"] = body_role
        # Body status is stronger evidence than the subject, but never downgrade: keep the
        # higher-rank interpretation from subject+snippet when the body has none.
        body_status = _detect_status(sanitized)
        if body_status and STATUS_RANK[body_status] >= STATUS_RANK.get(result["current_status"], -1):
            result["current_status"] = body_status
    else:
        # Give affirmation-missed rows a second chance with the full body;
        # skip bodies for hard-blacklisted rows (saves quota).
        needs_body = not (result["company_name"] and result["role_title"]
                          and result["current_status"])
        second_chance = result["filter_reason"] == "no positive affirmation"
        result["needs_body"] = result["is_valid"] or needs_body or second_chance

    if not result["current_status"]:
        result["current_status"] = "Applied"
    if not result["company_name"]:
        result["company_name"] = _company_fallback(sender)
    if not result["role_title"]:
        # Only fall back to the subject when it actually reads like a job title.
        result["role_title"] = subject if _looks_like_role(subject) else ""
    refine_company_role(result)
    if not _looks_like_role(result["role_title"]) and result.get("job_url"):
        slug_role = _role_from_url_slug(result["job_url"])
        if slug_role:
            result["role_title"] = slug_role
    # Final quality gate: never store a pronoun/platform placeholders as the employer,
    # and never store a status phrase / job ID as the role.
    company = _clean_company_name(result["company_name"])
    if not company or _is_platform_company(company):
        fallback = _clean_company_name(_company_fallback(sender))
        company = fallback if (fallback and not _is_platform_company(fallback)) else "Unknown"
    result["company_name"] = company
    result["role_title"] = _clean_role_name(result["role_title"])
    if not _looks_like_role(result["role_title"]):
        result["role_title"] = ""
    cat_text = f"{result['role_title']} {subject} {message.get('snippet', '')}"
    result["job_type"] = classify_job_type(cat_text)
    result["seniority_level"] = classify_seniority(result["role_title"])
    result["industry"] = classify_industry(f"{result['company_name']} {result['role_title']}")
    return result


def _role_from_url_slug(url: str) -> str:
    match = re.search(r"/jobs?/view/([a-z0-9][a-z0-9-]{5,})", (url or "").lower())
    if not match:
        return ""
    slug = re.sub(r"-\d{6,}.*$|-[a-z]{2}-[a-z]{2}.*$|\.[a-z]{2,3}$", "", match.group(1))
    role = re.sub(r"-+", " ", slug).strip()
    return role.title() if len(role) >= 6 else ""


def extract_job_url(body_text: str) -> str:
    if not body_text:
        return ""
    scored = []
    for match in re.finditer(r"https?://[^\s\"'<>\)\]>]+", body_text):
        raw_url = match.group(0)
        cleaned = strip_tracking_params(raw_url)
        context = body_text[max(0, match.start() - 120):match.start()].lower()
        low = cleaned.lower()
        score = 0
        if URL_CONTEXT_HINT and any(re.search(p, context) for p in URL_CONTEXT_HINT):
            score += 4
        if URL_PLATFORM_HINT.search(low):
            score += 3
        if URL_JOB_HINT.search(low):
            score += 2
        if URL_EXCLUDE.search(low):
            score -= 10
        scored.append((score, cleaned))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored or scored[0][0] <= 0:
        return ""
    return scored[0][1].rstrip(".,;:)!?")


def _detect_platform(sender: str, ats_source: str = "", urls: str = "") -> str:
    domain_match = DOMAIN_RE.search(sender)
    if domain_match:
        domain = domain_match.group(1).lower()
        for platform, suffixes in PLATFORM_DOMAINS:
            for suffix in suffixes:
                if domain.endswith("." + suffix) or domain.endswith(suffix) or suffix in domain:
                    return platform
        if any(domain.endswith("." + s) or domain.endswith(s) for s in ATS_DOMAIN_SUFFIXES):
            return "Direct ATS"
    if ats_source and ats_source not in ("", "Other"):
        return "Direct ATS"
    if urls and URL_PLATFORM_HINT.search(urls.lower()):
        if re.search(r"linkedin\.com/jobs", urls, re.IGNORECASE):
            return "LinkedIn"
        if re.search(r"jobstreet\.", urls, re.IGNORECASE):
            return "JobStreet"
        if re.search(r"hiredly\.", urls, re.IGNORECASE):
            return "Hiredly"
        if re.search(r"prosple\.", urls, re.IGNORECASE):
            return "Prosple"
        return "Direct ATS"
    return "Other"


def classify_message(sender: str, subject: str, snippet: str = "") -> tuple:
    """Strict classification: blacklist → strong affirm → weak affirm → loose platform tier.

    Returns (is_valid, reason).
    """
    subj_sender = f"{sender} {subject}".lower()
    for pattern in SPONSOR_BLACKLIST:
        if re.search(pattern, subj_sender):
            return False, f"blacklist ({pattern})"

    for hint in BLACKLIST_SENDER_HINTS:
        if hint in subj_sender:
            return False, f"blacklist-sender ({hint})"

    text = f"{subject} {snippet}".lower()
    garbage = _is_garbage(sender, subject, snippet)
    hard = any(re.search(p, text) for p in HARD_GARBAGE)

    # Strong explicit confirmations win even if the body has a marketing tail
    # (JobStreet/Workday confirmation emails append "jobs you may like" sections).
    for pattern in STRONG_AFFIRMATION_PATTERNS:
        if re.search(pattern, text):
            if hard:
                return False, "noise (non-job correspondence)"
            return True, "affirmed"

    for pattern in AFFIRMATION_PATTERNS:
        if re.search(pattern, text):
            if not garbage:
                return True, "affirmed"
            return False, "affirmed-but-noisy"

    # Loose tier: known ATS/job-platform senders whose subject is a bare job title
    # ("Assistant Manager, BI at DFI Retail Group") or mentions the application.
    if not garbage and _is_platform_sender(sender) and LOOSE_SUBJECT_RE.search(text):
        return True, "platform application notice (loose)"

    if garbage:
        return False, "noise (career-advice/digest)"
    return False, "no positive affirmation"


def _is_garbage(sender: str, subject: str, snippet: str = "") -> bool:
    text = f"{subject} {snippet}".lower()
    if any(re.search(pattern, text) for pattern in GARBAGE_KEYWORDS):
        return True

    domain_match = DOMAIN_RE.search(sender)
    if domain_match:
        bare = domain_match.group(1).lower().split(".")[0]
        if bare in GARBAGE_SENDER_DOMAINS:
            return True
        if bare in ("linkedin", "indeed", "glassdoor", "ziprecruiter", "monster",
                    "careerbuilder", "jobcase", "naukri", "shine", "jobstreet",
                    "hiredly", "prosple", "wobb", "workday", "myworkday", "myhr",
                    "seek", "jobsdb", "foundit"):
            if any(re.search(pattern, text) for pattern in (
                r"alert", r"recommend", r"suggest", r"\bdigest\b",
                r"\bhiring\b", r"matching", r"notify", r"weekly", r"daily",
                r"follow\b", r"meet\b", r"puzzle", r"network", r"connect",
                r"new jobs?", r"more jobs?", r"top picks?", r"still accepting",
            )):
                return True
    return False


QUOTE_SPLIT_PATTERNS = [
    r"(?m)^On .{5,120}\bwrote:\s*$",
    r"(?m)^-{2,}\s*(?:Original|Forwarded) [Mm]essage\s*-{2,}",
    r"(?m)^_+\s*$",
    r"(?m)^From:\s.+$",
    r"(?m)^Sent from my\b",
]

SIGNATURE_SEPARATOR = r"(?m)^--\s*$"


def sanitize_body(body_text: str) -> str:
    """First-message text only: drop quoted replies, signatures and URLs, cap the length.

    Quoted previous messages used to leak old statuses into classification (a follow-up
    quoting an old rejection could read as Rejected) — cutting at the first quote marker
    keeps the verdict about the new message.
    """
    text = body_text or ""
    for pattern in QUOTE_SPLIT_PATTERNS:
        match = re.search(pattern, text)
        if match and match.start() > 0:
            text = text[:match.start()]
    for marker in [SIGNATURE_SEPARATOR] + SIGNATURE_MARKERS:
        match = re.search(marker, text, flags=re.IGNORECASE | re.MULTILINE)
        if match and match.start() > 0:
            text = text[: match.start()]
    text = "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith(">"))
    text = html_lib.unescape(text)
    text = re.sub(r"https?://\S+", " ", text)
    return " ".join(text.split()[:400])


def llm_extract(subject: str, body_snippet: str, sender: str):
    """Removed: the optional LLM extraction pass was dropped with the rest of the AI layer.

    Kept as an explicit no-op so any stale caller gets the documented "no result" behaviour
    instead of an AttributeError, and so nothing silently re-adds a network dependency to
    the parser. Extraction is fully deterministic.
    """
    return None


def _parse_subject(subject: str) -> tuple:
    for pattern in SUBJECT_PATTERNS:
        match = pattern.search(subject)
        if match:
            company = _clean_company_name(match.groupdict().get("company", ""))
            role = _clean_role_name(match.groupdict().get("role", ""))
            return company, role
    return "", ""


def _company_from_sender(sender: str, subject: str = "") -> str:
    return (_company_from_bracket(subject)
            or _company_from_display_name(sender)
            or _company_from_domain(sender))


def _company_from_bracket(subject: str) -> str:
    match = re.search(r"\[([\w .,&'-]{2,50})\]", subject)
    if match:
        return _clean_company_name(match.group(1))
    return ""


def _company_from_display_name(sender: str) -> str:
    match = re.match(r'^(?:"([^"]*)"|([^<]+?))\s*<[^>]+>\s*$', sender.strip())
    if not match:
        return ""
    display = (match.group(1) or match.group(2)).strip()
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
    if not cleaned or len(cleaned) < 2:
        return ""
    if re.search(r"\b(?:no|donot)\s?-?reply\b", cleaned, re.IGNORECASE):
        return ""
    if (len(words) == 2 and all(w.istitle() for w in words)
            and not had_company_marker):
        return ""
    return cleaned


def _company_from_domain(sender: str) -> str:
    domain_match = DOMAIN_RE.search(sender)
    if not domain_match:
        return ""
    full_domain = domain_match.group(1).lower()

    for suffix in ATS_DOMAIN_SUFFIXES:
        if full_domain.endswith("." + suffix) or full_domain == suffix:
            tenant = full_domain[: -(len(suffix) + 1)] if full_domain != suffix else ""
            labels = [l for l in tenant.split(".") if l]
            labels = [l for l in labels if l not in GENERIC_SUBDOMAIN_LABELS]
            if labels:
                return _prettify(labels[-1])
            break

    bare = full_domain.split(".")[0]
    if bare and bare not in GENERIC_SUBDOMAIN_LABELS and not re.search(
        r"no-?reply|donotreply|notification|alerts?|updates?|careers?|jobs?|recruit|talent|hiring|mail",
        full_domain,
    ):
        return _prettify(bare)
    return ""


def _extract_role(subject: str) -> str:
    patterns = [
        # "Thank you for applying <role>" (but not "... for applying TO <company>")
        r"(?i)^thank(?:s| you)(?: so much)? for (?:applying|your application|your interest)"
        r"\s+(?!to\b|at\b|with\b|for\b|in\b)([^|·—]{3,100})",
        # "Your recent application for <role>"
        r"(?i)^your (?:recent )?application for\s+(?!the\s+role\b)([^|·—]{3,100})",
        r"(?:application (?:for|to|received)[:\s-]+)([^|·—-]{3,80})",
        r"(?:for the role of[:\s-]+|for the position of[:\s-]+|role[:\s-]+)([^|·—()-]{3,80})",
        # Require real punctuation after "your application" — "Your application was
        # successfully submitted" must NOT become a role.
        r"(?:your application\s*[:\-–—]\s*)([^|·—()-]{3,80})",
        r"(?:position|role)[:\s]+([^|·—()-]{3,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, subject)
        if match:
            role = _clean_role_name(match.group(1))
            if len(role) >= 3:
                return role
    return ""


def _company_fallback(sender: str) -> str:
    domain_match = DOMAIN_RE.search(sender)
    if not domain_match:
        return "Unknown"
    labels = domain_match.group(1).lower().split(".")
    while len(labels) > 1 and (len(labels[-1]) <= 3 or labels[-1] in ("com", "net", "org", "io", "dev")):
        labels.pop()
    domain = ".".join(labels)
    cleaned = re.sub(
        r"^(?:www|mail|no-?reply|donotreply|notifications?|alerts?|updates?|careers?|jobs?|"
        r"recruit\w*|talent|hiring|hr|team|message)[.-]",
        "",
        domain,
    )
    if not cleaned or cleaned in ("gmail", "outlook", "yahoo", "hotmail", "icloud", "proton"):
        return "Unknown"
    return _prettify(cleaned)


def _detect_status(text: str) -> str:
    text_lower = text.lower()
    detected = []
    for status, patterns in STATUS_KEYWORDS:
        for pattern in patterns:
            if re.search(pattern, text_lower):
                detected.append((STATUS_RANK[status], status))
                break
    if detected:
        return max(detected)[1]
    return ""


def _clean_company_name(name: str) -> str:
    name = (name or "").strip()
    # An email address / domain is never an employer name.
    if "@" in name:
        return ""
    name = re.sub(r"[\r\n].*$", "", name)
    name = re.sub(
        r"\s+(?:unfortunately|regrettably|however|but|therefore|so|please|you|your|"
        r"our|if|we|they)\b.*$",
        "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*[-–—]\s*$", "", name).strip()
    name = re.sub(r"^(?:the|a|an|and|or|but|so|however)\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s*\((?:[^)]*)\)\s*$", "", name).strip(" .,-|·")
    name = re.sub(r"\s+(?:via|from)\s+.*$", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(
        r"\s+(?:has|have|was|is|were|will|we(?:'ve| have)?)\s+.*$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    words = name.split()
    while words and words[-1].lower().strip(".,") in SENDER_NAME_NOISE:
        words.pop()
    cleaned = " ".join(words).strip()
    # Never return a pronoun/verb/program name as an employer.
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


COMPANY_LEAD_STOPWORDS = {
    "joining", "join", "please", "thanks", "thank", "we", "we're", "you", "your", "our",
    "protege", "apprentice", "urgent", "immediate", "hi", "hello", "dear", "candidate",
    "application", "this", "the", "a", "an",
}


COMPANY_STOPWORDS = {
    "this", "that", "these", "those", "it", "its", "the", "a", "an", "and", "or", "but",
    "so", "however", "be", "is", "are", "was", "were", "been", "being", "has", "have",
    "had", "will", "would", "can", "could", "should", "may", "might", "must", "do",
    "does", "did", "our", "us", "we", "you", "your", "yours", "my", "me", "their",
    "they", "them", "please", "thank", "thanks", "unfortunately", "regrettably",
    "unsuccessful", "successful", "application", "candidate", "task", "pending", "job",
    "role", "position", "email", "recruiter", "hiring", "careers", "hr", "verify",
    "verification", "account", "submitted", "received", "sent", "closed", "expired",
    "urgent", "immediate", "important", "new", "update",
}


def _clean_role_name(role: str) -> str:
    role = (role or "").strip(" .,-|·\"'")
    # Strip email-subject decorations ("Interview invitation - X", "Application received: X").
    role = re.sub(
        r"(?i)^(?:interview|application|assessment|offer|invitation|update|notice)"
        r"[^,|:–—-]{0,40}\s*[-–—:]\s*", "", role)
    # "Thank you for applying <role>" -> <role> (but not "... for applying TO <company>").
    role = re.sub(
        r"(?i)^thank(?:s| you)(?: so much)? for (?:applying|your application|your interest)"
        r"\s+(?!to\b|at\b|with\b|for\b|in\b)", "", role)
    # Advert urgency prefixes ("Urgent! Risk Underwriting Specialist").
    role = re.sub(r"(?i)^(?:urgent|immediate|hiring now|new)\s*[!:\-–—]\s*", "", role)
    # Portal/subject boilerplate that is never a job title.
    role = re.sub(r"(?i)^(?:candidate )?application (?:is |was )?sent\b.*$", "", role)
    role = re.sub(r"(?i)^your job application has been received\b.*$", "", role)
    role = re.sub(r"\s+at\s+[^,]+$", "", role, flags=re.IGNORECASE).strip()
    role = re.sub(r"(?i)\s+(?:role|position|job|opening|vacancy)$", "", role).strip()
    role = re.sub(r"\s*[-–|]\s*[^|]*$", "", role).strip() if "|" in role or "–" in role else role
    return re.sub(r"\s+", " ", role).strip(" .,-|·")


def _prettify(domain: str) -> str:
    cleaned = re.sub(r"^(?:www|mail|careers?|jobs?|hr|talent|recruit\w*)[.-]", "", domain)
    return cleaned.replace("-", " ").replace("_", " ").title()


def _date_from_header(date_header: str) -> str:
    try:
        return parsedate_to_datetime(date_header).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")


SENDER_NAME_NOISE = [
    "careers", "career", "jobs", "job", "recruiting", "recruitment", "recruiter",
    "talent acquisition", "talent", "acquisition", "hiring team", "hiring", "team",
    "notifications", "notification", "no-reply", "noreply", "donotreply",
    "do-not-reply", "updates", "update", "mail", "hr", "people", "university",
    "status", "official", "assistant", "system", "alerts", "confirmations",
    "inbox", "automated", "emails",
]

COMPANY_MARKERS = [
    "corp", "corporation", "inc", "incorporated", "llc", "ltd", "limited",
    "gmbh", "bv", "plc", "co", "labs", "technologies", "tech", "software",
    "systems", "solutions", "group", "holdings", "ventures", "capital",
]

DISPLAY_NAME_BLOCKLIST = {
    "unknown", "noreply", "no-reply", "no reply", "donotreply", "do not reply",
    "me", "you", "postmaster", "mailer-daemon", "undisclosed-recipients",
    "notifications", "notify", "sender", "recruiter", "hiring team",
    # platform / ATS display names that are never the employer
    "workday", "myworkday", "jobstreet", "linkedin", "hiredly", "prosple", "wobb",
    "indeed", "glassdoor", "greenhouse", "lever", "ashby", "smartrecruiters",
    "icims", "taleo", "successfactors", "workable", "bamboohr", "oraclecloud",
    "myhr", "mycareerx", "seek", "jobsdb", "hirevue", "hackerrank", "codility",
    "careers", "recruitment", "recruitment team", "talent acquisition",
    "talent acquisition team", "hr team", "people team",
}

# Companies recorded as the job platform instead of the actual employer — these get
# re-extracted from the role/subject text ("Application update for X at Y").
PLATFORM_COMPANY_RE = re.compile(
    r"workday|myhr|jobstreet|linkedin|hiredly|prosple|oraclecloud|greenhouse|"
    r"ashby|smartrecruiters|icims|taleo|jobvite|recruitee|teamtailor|breezy|"
    r"mycareerx|successfactors|sapsf|workable|applytojob|jobscore|indeed|glassdoor|"
    r"seek|jobsdb|wobb|pageuppeople|avature|phenom|eightfold|cornerstoneondemand|"
    r"brassring|kenexa|njoyn|erecruit|zoho|hirevue|hackerrank|codility|testgorilla|"
    r"pymetrics|criteria corp|vidcruiter|sparkhire|berke|wonderlic|predictiveindex|"
    r"talentify|hrmos|lever|bamboohr|rolp",
    re.IGNORECASE,
)

COMPANY_ROLE_PATTERNS = [
    # "Your Job Application Has Been Received - <Company>"
    re.compile(r"(?i)^your job application has been received\s*[-–—:]\s*(?P<company>.+)$"),
    # "<Company> – <Role> Application Update" (Ipsos, etc.)
    re.compile(
        r"(?i)^(?P<company>[A-Z][\w .,&'()\-]{1,60}?)\s*[-–—]\s*(?P<role>.+?)"
        r"\s+(?:application|applying)\s*(?:update|received|sent)?\s*$"),
    # "Assessment / Interview / Application Invitation From <Company>"
    re.compile(
        r"(?i)^(?:assessment|interview|application|offer|invitation|test)\b[^.\n]{0,40}"
        r"\bfrom\s+(?P<company>.+)$"),
    # JobStreet employer-view notices carry the real company + role
    re.compile(r"^(?P<company>[A-Z][\w .&,'()\-]{2,60}?) has viewed your application for (?P<role>.+?)(?:\s*\(|\s*[-–—]\s*|$)", re.IGNORECASE),
    re.compile(r"^(?P<company>[A-Z][\w .&,'()\-]{2,60}?) (?:viewed|reviewed|received) your (?:application|resume) for (?P<role>.+?)(?:\s*\(|\s*[-–—]\s*|$)", re.IGNORECASE),
    re.compile(r"application update for (?P<role>.+?) at (?P<company>.+?)(?:\s*\(|\s*[-–—]\s*|$)", re.IGNORECASE),
    re.compile(r"application update for (?P<role>.+?) [-–—] (?P<company>.+)$", re.IGNORECASE),
    re.compile(r"an update to your (?:job )?application for (?P<role>.+?)(?:\s+at\s+(?P<company>.+?))?\s*$", re.IGNORECASE),
    re.compile(r"thank you for your (?:recent )?job application for (?P<role>.+?)(?:\s+at\s+(?P<company>.+?))?\s*$", re.IGNORECASE),
    re.compile(r"application update[:\-]\s*(?P<role>.+?)(?:\s*,\s*[^,\s]+|\s+at\s+(?P<company>.+))?\s*$", re.IGNORECASE),
    re.compile(r"application (?:update )?for (?P<role>.+?) was (?:successfully )?(?:submitted|sent) to (?P<company>.+?)(?:\s*%%|\s+jobstreet\b|\s*\[|\s*$)", re.IGNORECASE),
    re.compile(r"your application (?:for|to) (?P<role>.+?) was sent to (?P<company>.+?)(?:\s*%%|\s+jobstreet\b|\s*\[|\s*$)", re.IGNORECASE),
    re.compile(r"application (?:was|has been) sent to (?P<company>.+?)(?:\s*%%|\s+jobstreet\b|\s*\[|\s*$)", re.IGNORECASE),
    re.compile(r"application sent to (?P<company>.+?) for (?P<role>.+)$", re.IGNORECASE),
    re.compile(r"the (?P<role>.+?) job with (?P<company>.+?)(?:\s+(?:has|was|is|no longer|,)|$)", re.IGNORECASE),
    re.compile(r"(?:confirming|confirmation of) your (?P<company>[\w .&,'()-]{2,60}?) job application", re.IGNORECASE),
    # "<invitation/assessment/interview> for <Company>" (company-only wording)
    re.compile(
        r"\b(?:invitation|assessment|interview|application|update|notice)\b"
        r"[^.\n]{0,50}?\bfor\s+(?P<company>[A-Z][\w&.,'()\- ]{2,60}?)\s*(?:[.!]|$)",
        re.IGNORECASE),
    # "Thanks for applying to/at <company>" (optionally "- <junk tail>")
    re.compile(r"thanks? (?:you )?for (?:applying|your application) (?:to|at|with) (?P<company>[^,.\n]+?)\s*(?:[-–—].*)?$", re.IGNORECASE),
    # "Thank you for applying <role>" (optionally "<role> at <company>")
    re.compile(r"thanks? (?:you )?for (?:applying|your application)\s+(?:for\s+|about\s+)?(?:the\s+)?(?P<role>[^,.\n]+?)(?:\s+(?:at|with|to)\s+(?P<company>[^,.\n]+?))?\s*(?:[-–—].*)?$", re.IGNORECASE),
    # "<Company> Careers - ..." display-name prefixes
    re.compile(r"^(?P<company>[A-Z][\w .&,'()\-]{2,50}?)\s+(?:careers?|recruit\w*|hiring|talent)\s*[-–—:]", re.IGNORECASE),
    re.compile(r"(?P<role>[A-Z][^?!\n]{3,90}?) at (?P<company>[A-Z][\w&.,'()\- ]+)$"),
    # ---- Body phrasing (generic subjects like "Your application was submitted") ----
    re.compile(
        r"\b(?:applied|applying|application)\b[^.\n]{0,60}?\b(?:for|to)\b[^.\n]{0,20}?"
        r"\b(?:the )?(?:position|role|job)\s+(?:of\s+)?(?P<role>[^.\n]{3,80}?)"
        r"\s+(?:at|with|@)\s+(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\b(?:position|role|job)\s+of\s+(?P<role>[^.\n]{3,80}?)\s+(?:at|with)\s+"
        r"(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\bthe\s+(?P<role>[A-Za-z0-9][^.\n]{2,60}?)\s+(?:position|role|opening|vacancy)\s+"
        r"(?:at|with)\s+(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\bthank you for (?:applying|your application|your interest) (?:to|at|with|in)\s+"
        r"(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    # "application for the <role> role/position" (no employer named)
    re.compile(
        r"\bapplication for (?:the )?(?P<role>[^.\n,;]{3,80}?)\s+"
        r"(?:role|position|job|opening|vacancy)\b", re.IGNORECASE),
    re.compile(
        r"\byour application (?:to|for)\s+(?P<company>[^.\n,;()]{2,70}?)"
        r"(?:\s+for\s+(?:the\s+)?(?P<role>[^.\n]{3,80}))?\s*(?:[.!]|$)", re.IGNORECASE),
    re.compile(
        r"\bwe(?:'ve| have)? received your application for\s+(?P<role>[^.\n,;]{3,80}?)\s+"
        r"(?:at|with)\s+(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\bapplied for\s+(?P<role>[^.\n,;]{3,80}?)\s+(?:at|with)\s+"
        r"(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\byou have applied (?:to|for)\s+(?P<company>[^.\n,;()]{2,70}?)"
        r"(?:\s+for\s+(?:the\s+)?(?P<role>[^.\n]{3,80}))?", re.IGNORECASE),
    re.compile(
        r"\b(?:role|position) (?:with|at) (?P<company>[^.\n,;()]{2,70})"
        r"(?:\s+as\s+(?:an? )?(?P<role>[^.\n]{3,80}))?", re.IGNORECASE),
]

JUNK_ROLE_PREFIX = re.compile(
    r"^(?:hi\b|hello\b|dear\b|thanks?|thank you|application|your application|"
    r"your job application|job application|your\b|was sent|was successfully|has been|been\b|"
    r"candidate|please|faris|a task|you have|you\b|we\b|at\b|to\b|for\b|with\b|"
    r"urgent\b|immediate\b)",
    re.IGNORECASE)


# Words that indicate a captured "company" is actually part of a role description.
_ROLE_WORD_RE = re.compile(
    r"\b(?:role|position|job|vacancy|opening|intern|internship|analyst|assistant|"
    r"engineer|manager|executive|officer|consultant|specialist|trainee|associate|"
    r"director|lead|staff|clerk|technician|graduate|protege|protégé|apprentice|"
    r"scientist|developer|designer|architect|advis(?:e)?r|administrator|coordinator)\b",
    re.IGNORECASE,
)
_COMPANY_MARKER_RE = re.compile(
    r"\b(?:bhd|sdn|ltd|limited|inc|llc|corp|corporation|group|holdings|company|co\.|"
    r"plc|bank|insurance|takaful|consulting|solutions|technologies|systems|services|"
    r"partners|associates|capital|ventures|labs|digital|retail)\b",
    re.IGNORECASE,
)


def _plausible_company(text: str) -> bool:
    if not text:
        return False
    if _ROLE_WORD_RE.search(text) and not _COMPANY_MARKER_RE.search(text):
        return False
    return True


def _is_platform_company(company: str) -> bool:
    c = (company or "").strip().lower()
    if not c or c in ("nan", "none", "unknown", "other", "direct ats"):
        return True
    return bool(PLATFORM_COMPANY_RE.search(c))


def is_platform_company(company: str) -> bool:
    """Public alias: True for platform/placeholder names that are never the real employer."""
    return _is_platform_company(company)


def is_junk_company_name(company: str) -> bool:
    """True when a stored company string is unusable (pronoun, program name, email, …)."""
    cleaned = _clean_company_name(company)
    if not cleaned:
        return True
    low = cleaned.lower().strip(".,")
    if low in ("unknown", "none", "nan", "other", "direct ats"):
        return True
    return _is_platform_company(cleaned)


def _strip_platform_suffix(company: str) -> str:
    """'UOB Workday' -> 'UOB'; 'Workday rgare' -> 'Rgare' (leaves 'Myworkday' intact)."""
    cleaned = re.sub(r"(?i)^workday\s+", "", (company or "").strip())
    cleaned = re.sub(r"(?i)\s+workday.*$", "", cleaned)
    cleaned = re.sub(r"(?i)\s+(careers?|talent (?:acquisition|team)|recruit(?:ment|ing)?|hiring(?: team)?)$",
                     "", cleaned).strip(" .,-")
    return cleaned


def _looks_like_role(role: str) -> bool:
    role = (role or "").strip()
    if len(role) < 3 or len(role) > 110:
        return False
    # Real job titles contain words — reject bare job IDs / requisition numbers.
    if not re.search(r"[A-Za-z]{2}", role):
        return False
    return not JUNK_ROLE_PREFIX.match(role)


def looks_like_role(role: str) -> bool:
    """Public alias: True when a string is plausibly a job title, not a sentence fragment."""
    return _looks_like_role(role)


def extract_company_role(text: str) -> tuple:
    """Best (company, role) candidate found anywhere in free text.

    Scans every COMPANY_ROLE_PATTERN and returns the first non-platform company and the
    first plausible role (either may be ""). Used on email bodies, where generic subjects
    hide the real employer ("Your application was successfully submitted").
    """
    company, role = "", ""
    if not text:
        return company, role
    for pattern in COMPANY_ROLE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groupdict()
            cand_company = _clean_company_name(groups.get("company") or "")
            cand_role = _clean_role_name(groups.get("role") or "")
            if (not company and cand_company and len(cand_company) <= 80
                    and not _is_platform_company(cand_company)
                    and _plausible_company(cand_company)):
                company = cand_company
            if not role and cand_role and _looks_like_role(cand_role):
                role = cand_role
            if company and role:
                return company, role
    return company, role


def role_from_application_snippet(snippet: str, company: str = "") -> str:
    """Extract the job title from confirmation snippets that don't use "position of X".

    Handles the three common shapes:
      * "Your application was sent to <Company> <Role> <Company> <Location>"
      * "You've applied to <Role> (…)"
      * a short snippet that is just the job title ("Assistant Manager, Growth Marketing")
    Returns "" when nothing role-like is present.
    """
    text = sanitize_body(snippet or "")[:300].strip()
    if not text:
        return ""

    if company and company.lower() in text.lower():
        match = re.search(
            r"(?i)application was sent to\s+" + re.escape(company) + r"\s+(.+)", text)
        if match:
            rest = match.group(1)
            rest = re.split(re.escape(company), rest, flags=re.IGNORECASE)[0]
            rest = re.split(
                r"(?i)\b(?:Federal Territory|Kuala Lumpur|Selangor|Malaysia|Remote|"
                r"Job type|Full[- ]time)\b", rest)[0]
            role = _clean_role_name(rest)
            if role and _looks_like_role(role):
                return role[:110]

    match = re.search(r"(?i)you(?:'ve| have) applied (?:to|for)\s+(.+?)(?:\s*[\(|]|$)", text)
    if match:
        role = _clean_role_name(match.group(1))
        if role and _looks_like_role(role):
            return role[:110]

    # A short, single-line snippet that reads like a job title.
    if len(text) <= 110 and "\n" not in text:
        candidate = _clean_role_name(text)
        if company and candidate:
            first = company.split()[0].lower() if company.split() else ""
            if first and candidate.lower().startswith(first) and " - " in candidate:
                candidate = _clean_role_name(candidate.split(" - ", 1)[1])
        if candidate and _ROLE_WORD_RE.search(candidate) and _looks_like_role(candidate):
            return candidate[:110]
    return ""


def refine_company_role(record: dict):
    """Replace platform-sender company names (LinkedIn/JobStreet/Workday/...) with the
    real employer parsed out of the role title / subject, and clean the role title."""
    company = (record.get("company_name") or "").strip()
    stripped = _strip_platform_suffix(company)
    if stripped and stripped.lower() != company.lower() and not _is_platform_company(stripped):
        record["company_name"] = _clean_company_name(stripped)

    best_company, best_role = "", ""
    for text in (record.get("role_title") or "", record.get("latest_subject") or ""):
        if not text:
            continue
        for pattern in COMPANY_ROLE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            groups = match.groupdict()
            cand_company = _clean_company_name(groups.get("company") or "")
            cand_role = _clean_role_name(groups.get("role") or "")
            if (cand_company and not best_company and not _is_platform_company(cand_company)
                    and _plausible_company(cand_company)):
                best_company = cand_company
            if cand_role and not best_role and _looks_like_role(cand_role):
                best_role = cand_role

    if best_company and _is_platform_company(record.get("company_name", "")):
        record["company_name"] = best_company
    if best_role:
        record["role_title"] = best_role

GENERIC_SUBDOMAIN_LABELS = {
    "boards", "board", "jobs", "job", "app", "apply", "application", "careers", "career",
    "email", "mail", "notify", "notifications", "alerts", "no-reply", "noreply",
    "recruiters", "recruiting", "my", "www", "secure", "atlassian", "ext", "eu", "us",
    "company", "candidates", "talent", "redirect", "wd1", "wd3", "wd5", "wd10",
    "panel", "phonon", "job",
}
