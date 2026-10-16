"""Pattern tables for the deterministic email parser.

Pure data: every regex the parser uses lives here, grouped by the decision it feeds.
They encode a lot of hard-won knowledge about real recruiting mail in Malaysia / SEA
(JobStreet, Workday tenants, Hiredly, Prosple, LinkedIn, ATS vendors). Edit with a test.
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
        # "Congratulations" alone proves nothing (confirmations, shortlists and portals say it
        # too): it needs an offer, or "selected / successful for the role" in the same sentence.
        r"\bcongratulations\b[^.!?\n]{0,80}\b(?:offer\b|(?:selected|successful) (?:for|in) "
        r"(?:the|this) (?:role|position|job)\b)",
        r"\b(?:would|'d) (?:like|love) to offer you\b",
        r"\bmake you an? (?:\w+ )?offer\b",
        r"\b(?:conditional|formal|written|verbal|final) (?:job |employment )?offer\b",
        r"\boffer (?:has been|was) extended\b",
        r"\bsurat tawaran\b",
    ]),
    ("Interview", [
        r"\binvitation to (?:an? )?interview\b",
        r"\binterview (?:invitation|request|confirmation|availability|details|schedule|slot)s?\b",
        r"\binterview (?:has been )?(?:re)?scheduled\b",
        # A generic "we'd like to invite you", unless what follows is a test, an assessment
        # centre or an event (those are other stages / not job mail).
        r"\b(?:we(?:'d| would)? ?(?:like|love|be delighted|be pleased) to|"
        r"we(?:'re| are) (?:pleased|delighted|happy|excited) to|"
        r"i(?:'d| would)? ?(?:like|love) to) invite you\b"
        r"(?!\s+(?:to|for) (?:take |complete |sit |attend )?(?:our |an? |the |your )?"
        r"(?:[\w-]+ ){0,2}(?:assessment|test|exercise|games?|webinar|event|open day|fair)\b)",
        # Guard against hypotheticals ("we may/will/could invite you", "if we invite you").
        r"(?<!\bmay )(?<!\bmight )(?<!\bcould )(?<!\bwill )(?<!\bwould )(?<!\bif we )"
        r"\binvite you (?:to|for) (?:an? |the )?(?:[\w-]+ ){0,2}"
        r"(?:interview|call|chat|conversation|meeting|catch[- ]?up)\b",
        r"(?<!\bmay )(?<!\bmight )(?<!\bcould )(?<!\bwill )(?<!\bwould )"
        r"\b(?:schedule|arrange|book|set up|confirm)[^.\n]{0,40}\binterview\b",
        r"\binterview[^.\n]{0,30}\b(?:scheduled|confirmed|arranged|invitation)\b",
        r"\b(?:shortlisted|selected)\b\s*,?\s*\bfor (?:an? )?interview\b",
        r"\b(?:phone|video|virtual|panel|final|first|second|third|initial|technical|case[- ]study|"
        r"behaviou?ral|competency|face[- ]to[- ]face|in[- ]person|on[- ]?site|hr|"
        r"hiring manager)[ -]?(?:round )?interviews?\b",
        r"\b(?:details of|confirm) your (?:\w+ )?interview\b",
        r"\b(?:move|progress|advance) you (?:forward )?to the (?:second|third|final|next) round\b",
        r"\b(?:find|book|pick|grab|schedule|arrange|set up) a (?:time|slot) to "
        r"(?:chat|talk|speak|meet|connect)\b",
        r"\b\d{2}[- ]?min(?:ute)?s? (?:phone |video |zoom |teams )?(?:call|chat|conversation)\b",
        r"\b(?:would like|wants|want) to (?:speak|talk) (?:with|to) you\b",
        r"\bpanggilan temuduga\b|\bsesi temuduga\b",
        r"\bavailable for an? (?:[\w-]+ ){0,3}interview\b",
        r"\b(?:wants?|would like|'d like|like) to (?:schedule an? )?interview(?: with)? you\b",
        r"\bhiring manager (?:would like|wants|will|is keen)\b",
        r"\bnext (?:round|stage)[^.\n]{0,30}\b(?:interview|call|conversation|discussion)\b",
        r"\bdiscovery call\b",
        r"\bphone screen\b",
        r"\binterview (?:with|at)\b[^.\n]{0,60}\b(?:team|manager|panel)\b",
    ]),
    ("Assessment / OA", [
        r"\bonline assessment\b",
        r"\bassessment (?:invitation|link|platform|task|centre|center|round)\b",
        r"\b(?:complete|take|start|attempt|finish|sit for)[^.\n]{0,40}\b(?:assessment|test|challenge|"
        r"exercise|task|case study|games?)\b",
        r"\btake[- ]home (?:case study|assignment|exercise|test|task)\b",
        r"\b(?:on[- ]demand|one[- ]way|pre[- ]?recorded|recorded|asynchronous|digital) video "
        r"(?:interview|assessment)\b",
        r"\bvideo assessment\b",
        r"\bujian (?:dalam talian|online)\b",
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
        r"\b(?:pursue|chose|choose|selected|moving forward with|gone with|advancing|"
        r"proceed(?:ing)? with|move forward with|continue with) "
        r"(?:other|another|a different) (?:candidates?|applicants?)\b",
        r"\bdecided to (?:pursue|move forward with|progress|advance|proceed with) (?:other|another)\b",
        r"\bdecided not to (?:move forward|proceed|progress|continue|pursue|take)\b",
        # "we will be pursuing candidates whose…", "decided to go with a candidate who…",
        # "move ahead with other candidates"
        r"\b(?:pursu\w+|proceed\w* with|mov\w+ (?:forward|ahead) with|go(?:ing)? with|"
        r"continu\w+ with|select\w*) (?:other |another |a different |an? )?(?:candidates?|"
        r"applicants?) (?:whose|who|with)\b",
        r"\bmov\w+ ahead with (?:other|another)\b",
        r"\b(?:will|shall|would) not (?:be )?(?:move|moving|proceed|proceeding|progress|"
        r"progressing) (?:forward )?with your (?:application|candidacy)\b",
        r"\b(?:won't|will not|wouldn't|would not) be (?:moving forward|progressing|proceeding)\b",
        r"\b(?:won't|will not|not) be (?:progressing|taking) your (?:application|candidacy)\b",
        r"\b(?:offer(?:ed)?|give|gave|extend(?:ed)?) the (?:role|position|job) to (?:another|other|"
        r"a different) (?:candidate|applicant|person)\b",
        r"\b(?:unable|not able|not in a position) to offer you (?:a|an|the|this)\b",
        r"\bnot (?:been )?selected to (?:proceed|progress|move)\b",
        r"\bnot suitable\b|\bunsuitable\b",
        r"\bclosing (?:all )?(?:remaining |your )?applications?\b",
        r"\btidak berjaya\b|\bdukacita dimaklumkan\b",
        r"\bregret to inform\b",
        r"\bwe regret\b",
        r"\bwent with (?:another|other) candidate",
        r"\b(?:position|role|vacancy|vacancies|positions|roles) (?:has|have|is|was|were) "
        r"(?:now |already )?(?:been )?filled\b",
        r"\b(?:position|role|requisition|vacancy) (?:has been|was) (?:cancelled|canceled|withdrawn)\b",
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
        r"\bsuccessfully applied\b",
        r"\bpermohonan anda\b",
    ]),
]

# Sentences that talk about a *possible* outcome, not this email's outcome: "if you do not
# hear from us within 4 weeks, please consider your application unsuccessful", "due to the
# high volume, unfortunately we cannot respond to everyone", "if shortlisted for an interview
# we will contact you". Status detection ignores them; classification still sees them.
CONDITIONAL_SENTENCE_PATTERNS = [
    r"\bif (?:you (?:do not|don't|have not|haven't) (?:hear|heard)|we do not contact you|"
    r"you are not contacted)\b",
    r"\bif (?:you are|you're|your (?:application|profile|cv|resume) is|you get|you have been) "
    r"(?:\w+ ){0,2}(?:shortlisted|successful|selected|suitable)\b",
    r"\bif (?:shortlisted|selected|successful)\b",
    r"\bshould (?:you|your (?:application|profile)) be (?:shortlisted|successful|selected)\b",
    r"\bonly (?:the )?(?:shortlisted|successful|selected) (?:candidates?|applicants?)\b",
    r"\b(?:unable|not able|cannot|can't|can not) (?:to )?(?:respond|reply) (?:to )?(?:each|every|all|"
    r"individual)",
    r"\bdue to the (?:high |large |overwhelming )?(?:volume|number) of (?:applications|applicants)\b",
    r"\bhigh volume of applications\b",
]

SUBJECT_PATTERNS = [
    re.compile(r"Thank you for applying to (?P<company>.*?) for (?P<role>.*)", re.IGNORECASE),
    # LinkedIn: "Your application to <Role> at <Company>"
    re.compile(r"^Your application to (?P<role>[^-–—|]+?) at (?P<company>[^-–—|]+)$", re.IGNORECASE),
    re.compile(r"Your application to (?P<company>.*?) - (?P<role>.*)", re.IGNORECASE),
    re.compile(r"Application (?:Received|Confirmation) - (?P<role>.*?) at (?P<company>.*)", re.IGNORECASE),
    re.compile(r"(?P<company>.*?) - (?:Application|Interview)(?: (?:Received|Confirmation|Update|"
               r"Invitation|Acknowledgement))? for (?P<role>.*)"),
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
    r"terms (?:of|and)", r"profile (?:photo|name) was",
    # social "follow" noise (but not "the written offer will follow" / "following up")
    r"\bfollow us\b", r"\bnew followers?\b", r"started following",
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
    r"career (?:fair|webinar|event|expo|carnival|day|talk)", r"virtual career", r"hiring event",
    r"\bvisiting our booth\b", r"\bour booth\b",
    r"salary (?:report|guide|insights?)", r"resume (?:review|tips?)", r"interview tips",
    r"career (?:advice|coaching|resources?)", r"job search (?:tips|strategies)",
    r"talent (?:insights?|acquisition newsletter)", r"recruitment (?:trends?|newsletter)",
    r"top \d+ (?:jobs|companies|employers)",
    r"(?:jobs?|roles?|programs?|programmes?|opportunities) you may (?:like|be interested)",
    r"applications? (?:close|closing)\b", r"wants you to apply", r"invites? you to apply",
    r"(?:you(?:'re| are)|you have been) invited to apply",
    # Candidate-portal housekeeping that "thanks you for your interest". Soft noise only: an
    # ATS confirmation may also say "your profile has been created" or carry a "join our
    # talent community" footer, and its explicit "application received" must still win.
    r"\b(?:candidate )?profile has been created\b", r"\byou can now apply\b",
    r"\btalent community\b",
    # Unrelated commercial email that keyword-trips recruiter words
    r"\brefund\b", r"trade[- ]?in", r"special offer", r"\bdiscount\b",
    r"brokerage", r"monthly subscription", r"up to \d+% off",
    # Non-job correspondence that trips "application/offer" words
    r"accommodation|campus (?:accommodation|tour|room)",
]

GARBAGE_SENDER_DOMAINS = {"quora", "quora-mail", "digest"}

# Stage 1 (hard blacklist): sponsored / marketing / automated digests.
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
    # Account / identity housekeeping: a task, never an application event
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
    r"application is incomplete",
    r"finish applying",
    r"(?:candidate )?profile has been created",
    r"registration confirmed",
    r"talent community",
]

# Stage 2 (positive affirmation): a message is ONLY valid if it confirms an
# action already taken (an application) or is a direct recruitment response.
AFFIRMATION_PATTERNS = [
    # --- Explicit confirmations (STRONG: these win over marketing tails in the body) ---
    r"thank(?:s| you)(?: so much)? for (?:your )?(?:recent |job )?(?:application|applying|interest|considering)\b",
    r"application (?:was|has been|is|had been) (?:successfully )?(?:received|submitted|sent)",
    r"application [^.,;!?]{0,120}\b(?:received|submitted)\b",
    r"application [^.,;!?]{0,120}\b(?:was|has been) sent\b",
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
    r"successfully applied (?:for|to)\b",
    r"\b(?:share|forward|pass)(?:ed)? (?:on )?your (?:cv|resume|application) (?:with|to) the "
    r"hiring (?:manager|team)",
    r"terima kasih atas permohonan anda",
    # --- Direct recruitment responses / status changes (WEAK: garbage veto still applies) ---
    r"invitation to interview",
    r"interview (?:invitation|request|confirmation|availability|details|schedule|slot)s?\b",
    r"\binterview (?:has been )?(?:re)?scheduled\b",
    r"\b(?:phone|video|virtual|panel|final|first|second|third|initial|technical|case[- ]study|"
    r"behaviou?ral|competency|face[- ]to[- ]face|in[- ]person|hr|hiring manager)[ -]?(?:round )?"
    r"interview\b",
    r"\bdetails of your (?:\w+ )?interview\b",
    r"invite you (?:to|for) (?:an? |the )?(?:[\w-]+ ){0,2}(?:interview|call|chat|conversation|meeting)",
    r"\b(?:move|progress|advance) you (?:forward )?to the (?:second|third|final|next) round\b",
    r"\bpanggilan temuduga\b",
    r"\bavailable for an? (?:[\w-]+ ){0,3}interview\b",
    r"\b(?:wants?|would like|'d like) to (?:schedule an? )?interview(?: with)? you\b",
    r"\b(?:schedule|arrange|set up|book) an? (?:[\w-]+ )?interview with you\b",
    r"\b(?:pursu\w+|proceed\w* with|mov\w+ (?:forward|ahead) with|go(?:ing)? with|"
    r"continu\w+ with) (?:other |another |a different |an? )?(?:candidates?|applicants?) "
    r"(?:whose|who|with)\b",
    r"\b(?:will|shall) not (?:be )?(?:move|moving|proceed|proceeding) (?:forward )?with your "
    r"(?:application|candidacy)\b",
    r"thank(?:s| you)(?: so much)? for (?:your )?interest\b",
    r"online assessment",
    r"complete (?:this|the|your|a|an|our) (?:[\w-]+ ){0,3}(?:assessment|test|exercise|case study|"
    r"challenge|games?)\b",
    r"invited? you to (?:take|complete|sit|attempt|do) ",
    r"\binvites you to take\b",
    r"\btake[- ]home (?:case study|assignment|exercise|test|task)\b",
    r"\b(?:on[- ]demand|one[- ]way|recorded|digital) video interview\b",
    r"\bujian dalam talian\b",
    r"assessment invitation",
    r"\boa invitation\b",
    r"invitation (?:for|to) (?:the |an |your )?(?:online )?assessment",
    r"regret to inform",
    r"not moving forward",
    r"unsuccessful",
    r"other candidates",
    r"not (?:been )?shortlisted",
    r"application (?:was |has )?(?:not been |not )successful",
    r"\bdecided not to (?:move forward|proceed|progress|continue)\b",
    r"\b(?:won't|will not) be (?:moving forward|progressing|taking your application)\b",
    r"(?:proceed|move forward|continue) with (?:other|another) (?:candidates?|applicants?)",
    r"\b(?:position|role|positions) (?:has|have) (?:now )?been filled\b",
    r"\bto (?:another|a different) candidate\b",
    r"\bnot suitable\b",
    r"\btidak berjaya\b",
    r"job with [^\n]{0,90}has closed",
    r"\bshortlisted\b",
    r"\bnext (?:round|stage)[^.\n]{0,30}\b(?:process|application|interview|assessment|selection)\b",
    r"\bassessment (?:invitation|link|platform|task)\b",
    r"\bhiring manager (?:would like|wants|will)\b",
    r"\brecruiter (?:will|to|would) (?:reach|contact|be in touch)\b",
    r"\b(?:pre[- ]?employment|background|reference) (?:check|screening)\b",
    # --- Contextual "offer", never a bare match (avoids promos like "special offer: RM15.90") ---
    r"\boffer letter\b",
    r"pleased to offer",
    r"offer of employment",
    r"\bjob offer\b",
    r"we(?:'d| would| are) (?:like to|pleased to|delighted to) offer(?: you)?\b",
    r"extend(?:ed|ing)? (?:an |this |the )?offer",
    r"offer to join",
    r"congratulations[^\n]{0,80}\boffer\b",
    r"\boffer\b[^\n]{0,60}\b(?:position|role|employment|join us|onboard)\b",
    r"(?:would|'d) (?:like|love) to offer you",
    r"\bmake you an? (?:\w+ )?offer\b",
    r"\bsurat tawaran\b",
]

STRONG_AFFIRMATION_PATTERNS = [
    # Application-specific "thank you", NOT the bare "thanks for subscribing/purchasing".
    r"thank(?:s| you)(?: so much)? for[^.\n]{0,60}\b(?:appl(?:ying|ication|y|ied)|"
    r"your (?:recent )?application|considering (?:me|my))\b",
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
    r"application [^.,;!?]{0,120}\bhas been sent\b",
    r"successfully applied (?:for|to)\b",
    # An explicit outcome *for your application* is as specific as a confirmation, and job
    # boards append "jobs you may like" tails to rejections too.
    r"\b(?:decided not to|unable to|will not|won't|not be|not able to) (?:progress|proceed with|"
    r"move forward with|take|taking|progressing|consider)\b[^.\n]{0,30}\byour (?:application|"
    r"candidacy)\b",
    r"\byour application (?:has|was) (?:been )?(?:unsuccessful|not (?:been )?successful)\b",
]

BLACKLIST_SENDER_DOMAINS = {"quora.com", "quora-mail.bounce", "digest"}
BLACKLIST_SENDER_HINTS = ("jobalert", "job-alert", "newsletter", "digest@", "promotions")

# Non-job commercial/correspondence terms that veto even strong confirmations
# (an accommodation portal also writes "your application has been received"; a bank writes
# "your credit card application has been received"; a university "pleased to offer you a
# place"; a shop "we're pleased to offer you RM20 off").
HARD_GARBAGE = [
    r"\brefund\b", r"trade[- ]?in", r"special offer", r"\bdiscount\b",
    r"brokerage", r"monthly subscription", r"up to \d+% off",
    r"accommodation",
    # consumer finance, worded for the customer, so "Credit Card Risk Analyst" or "Loan
    # Operations Executive" job mail is never vetoed
    r"\bcredit card\b(?!(?:\s+[\w-]+){0,3}\s+(?:analyst|manager|executive|specialist|officer|"
    r"associate|lead|intern|engineer|scientist|consultant|role|position|team|risk|"
    r"portfolio|analytics|strategy|operations))",
    r"\byour (?:[\w-]+ ){0,2}(?:loan|financing(?:-i)?|card|mortgage) application\b",
    r"\b(?:loan|financing) (?:repayment|deferment|amount)\b", r"\bdeferment\b",
    r"\bpre-?approved\b",
    # education / admissions / scholarships
    r"\boffer of admission\b", r"\badmissions?\b", r"\bscholarship\b",
    r"\b(?:application|applying) (?:to|for) the (?:master|bachelor|doctor|phd|diploma|"
    r"postgraduate|undergraduate)\w*\b",
    r"\bapplication (?:received|submitted)?\s*[:\-–]\s*(?:master|bachelor|doctor|phd|diploma|"
    r"postgraduate)\w*\b",
    r"\bstudent portal\b", r"\bplace in the [^.\n]{0,60}\bprogramme\b",
    # other non-job "applications"
    r"\bvisa application\b", r"\brental application\b", r"\btenancy\b",
    r"\bvolunteer (?:application|at|shift)|\bto volunteer\b",
    r"\bpolicy (?:number|no\.?)\s*[:#]?\s*[A-Z0-9]",
    r"\b(?:travel|motor|car|home|medical) insurance (?:application|policy|quote|plan|cover)\b",
    r"\b(?:ekyc|identity verification)\b",
    # retail promotions
    r"\brm ?\d+(?:\.\d+)? off\b", r"\buse (?:promo )?code\b", r"\bshop now\b",
    r"\bexclusive offer\b", r"\boffer (?:ends|expires|valid until)\b",
    r"\b(?:extended|extend) (?:the|our) offer until\b", r"\bcheckout\b",
    r"\breturn (?:request|application)\b", r"\border (?:#|no\.?|number)?\s*[A-Z]{0,4}-?\d{3,}",
    r"\bget (?:a )?quote\b", r"\bget covered\b", r"\bfinancial aid\b",
    r"\bregistration (?:is )?confirmed\b",
    # unfinished applications are not applications
    r"\b(?:haven't|have not|not yet) (?:submitted|completed)\b", r"\bstarted an application\b",
    r"\bapplication is incomplete\b", r"\bfinish applying\b",
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
# Assessment vendors only email candidates about a test an employer sent them.
ASSESSMENT_VENDOR_SENDERS = (
    "hackerrank", "codility", "codesignal", "hirevue", "testgorilla", "shl.com", "criteria.com",
    "pymetrics", "codingame", "hackerearth", "devskiller", "imocha", "vidcruiter", "sparkhire",
    "wonderlic", "predictiveindex", "cut-e", "talentq", "aon-assessment",
)
ASSESSMENT_WORD_RE = re.compile(r"\b(?:test|assessment|challenge|games?|video interview)\b")
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
    "urgent", "immediate", "important", "new", "update", "reminder", "action required",
    "invitation", "re", "fwd", "fw", "congratulations", "great news", "next steps",
}

SENDER_NAME_NOISE = [
    "careers", "career", "jobs", "job", "recruiting", "recruitment", "recruiter",
    "talent acquisition", "talent", "acquisition", "hiring team", "hiring", "team",
    "notifications", "notification", "no-reply", "noreply", "donotreply",
    "do-not-reply", "updates", "update", "mail", "hr", "people", "university",
    "status", "official", "assistant", "system", "alerts", "confirmations",
    "inbox", "automated", "emails", "graduate", "graduates", "early", "campus",
    "assessment", "assessments", "centre", "center",
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
    "talent acquisition team", "hr team", "people team", "google calendar", "calendar",
    "outlook calendar", "microsoft teams", "zoom", "calendly", "docusign",
}

# Department display names that are never the employer ("Unit Sumber Manusia" = HR unit).
DEPARTMENT_DISPLAY_RE = re.compile(
    r"(?i)^(?:unit|jabatan|bahagian|division|department)\b|sumber manusia|pengambilan|"
    r"^human (?:resources?|capital)(?: department| team)?$")

PLATFORM_COMPANY_RE = re.compile(
    r"workday|myhr|jobstreet|linkedin|hiredly|prosple|oraclecloud|greenhouse|"
    r"ashby|smartrecruiters|icims|taleo|jobvite|recruitee|teamtailor|breezy|"
    r"mycareerx|successfactors|sapsf|workable|applytojob|jobscore|indeed|glassdoor|"
    r"seek|jobsdb|wobb|pageuppeople|avature|phenom|eightfold|cornerstoneondemand|"
    r"brassring|kenexa|njoyn|erecruit|zoho|hirevue|hackerrank|codility|testgorilla|"
    r"pymetrics|criteria corp|vidcruiter|sparkhire|berke|wonderlic|predictiveindex|"
    r"talentify|hrmos|lever|bamboohr|rolp|\bshl\b|myfuturejobs|jobkini|maukerja|ricebowl|"
    r"google calendar|calendly|docusign",
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
    # Vendor / ATS subjects: "<Company> (has) invited you to …", "<Company>: <next step>"
    re.compile(r"^(?P<company>[A-Z][\w&.,'() -]{1,60}?) (?:has |have )?(?:invite[sd]? you to|"
               r"wants? to (?:interview|meet|speak with|talk to) you|would like to (?:interview|meet)"
               r" you|sent you a message)\b"),
    re.compile(
        r"^(?P<company>[A-Z][\w&.'() -]{1,60}?)\s*:\s*(?:application|your\b|interview|"
        r"assessment|offer|next steps?|complete|let's|action required|online|invitation|update)",
        re.IGNORECASE),
    re.compile(r"\b(?:test|assessment|challenge) from (?P<company>[A-Z][\w&'() -]{1,60}?)"
               r"(?=\s+(?:expires|is|has|will|for)\b|[.,!]|$)"),
    re.compile(r"\bnew message from (?P<company>[A-Z][\w&.'() -]{1,60}?)\s*$"),
    # Calendar invites: "invited by Melissa Tan (Kirana Re)"
    re.compile(r"\binvited by [A-Z][\w'’ -]{1,40}? \((?P<company>[A-Z][^)]{1,60})\)"),
    # "The following items were sent to <Company>." (Indeed)
    re.compile(r"\b(?:items|application|resume|cv|details) (?:were|was|has been|have been) "
               r"(?:successfully )?sent to (?P<company>[A-Z][^.\n,;()]{1,70})"),
    # "<Company> has reviewed/viewed/shortlisted your application for <role>"
    re.compile(r"(?:^|[.!?]\s+)(?P<company>[A-Z][\w .&,'()\-]{2,60}?) (?:has )?(?:viewed|reviewed|"
               r"received|shortlisted) your (?:application|resume)(?: for (?P<role>[^.\n]{3,80}?))?"
               r"(?:\s+and\b|\s*[.(]|\s*[-–—]\s*|$)"),
    # ---- Body phrasing (generic subjects like "Your application was submitted") ----
    re.compile(
        r"\b(?:your )?application for (?:the )?(?:position of |role of )?(?P<role>[^.\n;]{3,80}?)"
        r"(?:\s+\([^)]*\))?\s+(?:has been |was )?(?:successfully )?(?:sent|submitted) to "
        r"(?P<company>[A-Z][^\n;()]{1,70}?)(?=\.\s|\.$|$|\s+(?:The|We|You|Our)\b)"),
    re.compile(
        r"\bapplication for (?:the )?(?:position of |role of )?(?P<role>[A-Z][^.\n;]{2,80}?)"
        r"\s+(?:at|with)\s+(?P<company>[A-Z][^\n;()]{1,70}?)(?=\.\s|\.$|$|,|\s+(?:has|was|is|"
        r"and|for|on)\b)"),
    re.compile(
        r"\b(?:applied|applying) for (?:the )?(?P<role>[A-Z][^.\n;]{2,80}?)\s+(?:at|with)\s+"
        r"(?P<company>[A-Z][^\n;()]{1,70}?)(?=\.\s|\.$|$|,|\s+(?:has|was|is|and|for|on)\b)"),
    re.compile(
        r"\b(?:applied|applying|application)\b[^.\n]{0,60}?\b(?:for|to)\b[^.\n]{0,20}?"
        r"\b(?:the )?(?:position|role|job)\s+(?:of\s+)?(?P<role>[^.\n]{3,80}?)"
        r"\s+(?:at|with|@)\s+(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\b(?:position|role|job)\s+of\s+(?P<role>[^.\n]{3,80}?)\s+(?:at|with)\s+"
        r"(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\bthe\s+(?P<role>[A-Za-z0-9][^.\n]{2,60}?)\s+(?:position|role|opening|vacancy|job|"
        r"programme|program)\s+(?:at|with)\s+(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    re.compile(
        r"\bthank you for (?:applying|your application|your interest) (?:to|at|with|in)\s+"
        r"(?P<company>[^.\n,;()]{2,70})", re.IGNORECASE),
    # "application for/to the <role> role/position" (no employer named)
    re.compile(
        r"\b(?:application|applying|applied) (?:for|to) (?:(?:[A-Z][\w&'-]* ){1,5}for )?(?:the )?"
        r"(?P<role>[^.\n;]{3,80}?)\s+(?:role|position|job|opening|vacancy)\b"),
    # "your application for <Role>." / "about your application for <Role>:"
    re.compile(r"\b(?:your|of) application for (?:the )?(?P<role>[A-Z][^.\n;:]{2,70}?)"
               r"(?=\s*[.:!]\s|\s*[.:!]$|$|\s+(?:has|was|is|and|on)\b)"),
    # Assessment vendors: "… invites you to take the <Role> assessment"
    re.compile(r"\btake the (?P<role>[A-Z][^.\n'\"]{2,60}?) (?:assessment|test|challenge)\b"),
    # "interest in / offer you / invite you … the <Role> position" (capitalised title)
    re.compile(r"\bthe (?P<role>[A-Z][\w&/(),' -]{2,70}?) (?:position|role|job|vacancy|opening)\b"),
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
    "panel", "phonon", "hire", "s", "e", "em", "em1", "em2", "em3", "fa", "mail1", "hr",
    "people", "recruit", "workday", "us1", "eu1",
}

# Strings that end a company name when a capitalised run continues past it.
COMPANY_RUN_STOP_RE = re.compile(
    r"(?i)^(?:kuala|lumpur|selangor|malaysia|petaling|jaya|shah|alam|cyberjaya|putrajaya|"
    r"penang|johor|bahru|singapore|remote|hybrid|on-site|onsite|kl|bangsar|talent|"
    r"recruitment|recruiting|careers?|hr|human|team|people|hiring|graduate|early|"
    r"dear|hi|hello|regards|thanks|best|kind|re|fwd?|the|we|our|your|you|i|please)$")
LEGAL_SUFFIX_RE = re.compile(r"(?i)^(?:berhad|bhd|limited|ltd|inc|plc|llc|pte|corp|corporation)$")

# Words that mark a string as an email-subject decoration, not (part of) a job title.
ROLE_DECORATION_RE = re.compile(
    r"(?i)\b(?:interview\w*|application|applications|applying|applied|offer|update[sd]?|"
    r"invitation|invite[sd]?|assessment|next steps?|thank|thanks|schedul\w*|outcome|"
    r"result|status|confirmation|confirm\w*|reminder|action required|congratulations|"
    r"round|tawaran|temuduga|ujian|permohonan|pekerjaan|games|complete|let's|news|"
    r"following up|follow[- ]up|quick question|details|take-home|case study|availability|"
    r"resched\w*|now|expires|online test|aptitude test|checking in|catch up|hello|hi)\b|"
    r"^(?:re|fwd?|fw)\s*:|!|\?")


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
