"""Pattern tables for the deterministic email parser.

Pure data: every regex the parser uses lives here, grouped by the decision it feeds.
They encode a lot of hard-won knowledge about real recruiting mail in Malaysia / SEA
(JobStreet, Workday tenants, Hiredly, Prosple, LinkedIn, ATS vendors) — edit with a test.
Behaviour lives in classify.py / extract.py / status.py / categorize.py.
"""
from __future__ import annotations

import re

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
    ("Indeed", ("indeed.com", "indeedemail.com")),
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

QUOTE_SPLIT_PATTERNS = [
    r"(?m)^On .{5,120}\bwrote:\s*$",
    r"(?m)^-{2,}\s*(?:Original|Forwarded) [Mm]essage\s*-{2,}",
    r"(?m)^_+\s*$",
    r"(?m)^From:\s.+$",
    r"(?m)^Sent from my\b",
]

SIGNATURE_SEPARATOR = r"(?m)^--\s*$"


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
ROLE_WORD_RE = re.compile(
    r"\b(?:role|position|job|vacancy|opening|intern|internship|analyst|assistant|"
    r"engineer|manager|executive|officer|consultant|specialist|trainee|associate|"
    r"director|lead|staff|clerk|technician|graduate|protege|protégé|apprentice|"
    r"scientist|developer|designer|architect|advis(?:e)?r|administrator|coordinator)\b",
    re.IGNORECASE,
)
COMPANY_MARKER_RE = re.compile(
    r"\b(?:bhd|sdn|ltd|limited|inc|llc|corp|corporation|group|holdings|company|co\.|"
    r"plc|bank|insurance|takaful|consulting|solutions|technologies|systems|services|"
    r"partners|associates|capital|ventures|labs|digital|retail)\b",
    re.IGNORECASE,
)

GENERIC_SUBDOMAIN_LABELS = {
    "boards", "board", "jobs", "job", "app", "apply", "application", "careers", "career",
    "email", "mail", "notify", "notifications", "alerts", "no-reply", "noreply",
    "recruiters", "recruiting", "my", "www", "secure", "atlassian", "ext", "eu", "us",
    "company", "candidates", "talent", "redirect", "wd1", "wd3", "wd5", "wd10",
    "panel", "phonon",
}


# Role family drives the "what kind of job" slice of every report. Order matters: the first
# match wins (an "Actuarial Data Analyst" is Actuarial before it is Data & Analytics).
ROLE_FAMILY_RULES = [
    ("Actuarial", r"actuari\w+|\bpricing\b|reserving|valuation|experience analys\w*"),
    ("Risk & Quantitative", r"\brisk\b|quant\w*|\bcapital\b|solvency|market risk|credit risk"),
    ("Data & Analytics", r"\bdata\b|\banalytics\b|insight\w*|business intelligence|\bbi\b|"
                         r"statistic\w*|reporting analyst|machine learning|\bml\b"),
    ("Business Analysis", r"business analyst|business analysis|product owner|project manage\w*|"
                          r"consultant"),
    ("Insurance Operations", r"claims|underwrit\w+|reinsur\w+|broking|takaful|insurance|"
                             r"portfolio services|renewal"),
    ("Finance & Accounting", r"financ\w+|account\w+|audit\w*|treasury|tax\b|fp&a"),
    ("Marketing & Product", r"marketing|product (?:strategy|development|manage\w*)|brand|"
                            r"growth|campaign"),
]
DEFAULT_ROLE_FAMILY = "Other"
