"""CV Fit Analyzer — benchmarks job descriptions against Faris Erhan's curated CV.

Fit score is a weighted composite of five pillars:
  0.40  skills & tools coverage (CV-confirmed tools found in the JD)
  0.20  target-role title alignment
  0.15  qualification match (degree field, masters signals)
  0.15  seniority fit (fresh-grad profile vs VP/senior/manager asks)
  0.10  location fit (Malaysia / KL / Selangor / Remote)
"""
import json
import os
import re

CV_PATH = "cv_profile.txt"

# Curated CV (also persisted to cv_profile.txt — the dashboard drawer edits that file).
DEFAULT_CV_TEXT = """\
FARIS ERHAN
Kuala Lumpur / Selangor, Malaysia (open to Remote)
Target roles: Actuarial Analyst | Quantitative Risk Analyst | Data Analyst | Business Intelligence Analyst

EDUCATION
BSc (Hons) Mathematics & Statistics — University of Warwick, United Kingdom
- Core coursework: Statistical Modelling & Generalised Linear Models (GLMs), Probability Theory,
  Mathematical Statistics, Linear Algebra, Real Analysis, Optimisation, Time Series & Forecasting,
  Stochastic Processes, Numerical Methods.

TECHNICAL SKILLS
- Python: Pandas, NumPy, Scikit-learn (data wrangling, statistical modelling, predictive analytics)
- R: GLMs, statistical modelling, hypothesis testing, time series
- SQL: querying, joins, aggregations, reporting datasets
- Advanced Excel & VBA: financial models, pivot automation, dashboard-grade reporting
- Financial mathematics: discounting, valuation, interest-rate & cash-flow modelling
- Probability theory & statistical inference: distributions, hypothesis testing, Bayesian reasoning

CERTIFICATIONS & EXAMINATIONS
- SOA Exam P (Probability) — Candidate, actively preparing

ACADEMIC / PORTFOLIO PROJECTS
- GLM insurance claims severity model (R): fitted and validated Tweedie/Gamma GLMs on public
  claims data; interpreted rating factors for a business-readable summary.
- Time series forecasting project (Python): ARIMA/ETS forecasting with Pandas & NumPy pipelines.
- Monte Carlo simulation of insurance loss distributions (probability-modelled risk capital).
"""

# ---------------------------------------------------------------- lexicons

# Tools the candidate owns — ownership is CV-driven (mention in CV => owned).
TOOLS = [
    ("Python", r"\bpython\b", 6),
    ("Pandas", r"\bpandas\b", 3),
    ("NumPy", r"\bnumpy\b", 2),
    ("Scikit-learn", r"scikit[- ]?learn|\bsklearn\b", 3),
    ("R", r"(?<![A-Za-z&/])R(?![A-Za-z&/])|r\s?studio|r programming|ggplot", 6),
    ("GLM / Statistical Modelling", r"\bglm?s?\b|generalized linear|generalised linear|statistical model\w*|regression", 6),
    ("SQL", r"\bsql\b|mysql|postgres(?:ql)?|t[- ]?sql|sql server|mssql|bigquery|snowflake", 6),
    ("Excel / VBA", r"\bexcel\b|pivot\s?table|vlookup|xlookup|\bvba\b|\bmacro\b", 5),
    ("Financial Mathematics", r"financial (?:mathematic|model\w*)|\bvaluation\b|discount\w* (?:cash flow|factor)|\bdcf\b|interest rate|bond pric", 4),
    ("Probability / Statistics", r"\bprobability\b|\bstatistic\w*\b|hypothesis test\w*|time series|stochastic", 4),
    ("Data Analysis", r"data anal\w*|data driven|analytics|insight\w*|exploratory data", 4),
    ("Dashboards / BI", r"business intelligence|\bbi\b|dashboard\w*|management report\w*|kpi", 4),
    ("Risk Analysis", r"risk (?:analysis|analytics|assessment|model\w*|management|modelling)|\bvar\b|stress test\w*|monte carlo", 5),
]

# Market signals: present in JD but NOT owned by the CV => hard gap (feeds missing_skills).
# 4th element = CV-ownership pattern (stricter than the demand pattern when the demand
# regex is loose — e.g. "solvency" in a project bullet ≠ knowing IFRS 17).
IN_DEMAND_SKILLS = [
    ("Power BI", r"power\s?bi|\bpbix\b|\bdax\b", 6, r"power\s?bi|\bpbix\b|\bdax\b"),
    ("Tableau", r"tableau", 5, r"tableau"),
    ("SAS", r"\bsas\b(?!s)|sas (?:eg|enterprise|studio)", 6, r"\bsas\b(?!s)|sas (?:eg|enterprise|studio)"),
    ("Prophet / Actuarial Software", r"\bprophet\b|\bmoses\b|ggy axis|\bemblem\b|actuarial (?:software|system|model\w*)", 6, r"\bprophet\b|\bmoses\b|ggy axis|actuarial (?:software|system|model\w*)"),
    ("IFRS 17 / Reserving", r"ifrs\s?17|mfrs\s?17|loss reserv\w+|reserv\w+ basis|solvency|\bmcr\b|\bscr\b", 5, r"ifrs\s?17|mfrs\s?17|reserv\w+|\bsolvency ii\b|loss reserv\w+"),
    ("Actuarial Exams", r"\bsoa\b|\bifoa\b|actuarial exam\w*|exam p\b|exam fm\b|\basa\b|\bfsa\b", 5, r"\bsoa\b|\bifoa\b|actuarial exam\w*|exam p\b|exam fm\b"),
    ("Alteryx / ETL", r"alteryx|\betl\b|\bssis\b|data pipeline\w*|informatica", 4, r"alteryx|\betl\b|\bssis\b|data pipeline\w*|informatica"),
    ("Qlik / Looker", r"\bqlik(?:view|sense)?\b|looker", 3, r"\bqlik(?:view|sense)?\b|looker"),
    ("Cloud (AWS/Azure)", r"\baws\b|azure|\bgcp\b|google cloud", 3, r"\baws\b|azure|\bgcp\b|google cloud"),
    ("Big Data (Spark/Hadoop)", r"\bspark\b|hadoop|databricks|\bhive\b", 3, r"\bspark\b|hadoop|databricks|\bhive\b"),
    ("C++ / Java", r"\bc\+\+\b|\bjava\b(?!script)", 3, r"\bc\+\+\b|\bjava\b(?!script)"),
    ("Credit / Market Risk Domain", r"credit risk|market risk|operational risk|\bbasel\b|liquidity risk|\bilm\b|expected credit loss|\becl\b|\brisk\b", 4, r"credit risk|market risk|operational risk|\bbasel\b|\bvar\b|stress test|risk (?:analysis|model\w*|management|modelling|analytics)"),
    ("Actuarial / Insurance Domain", r"actuari\w+|premium rat\w+|\bpricing\b|tariff|claims|underwrit\w+|takaful|\binsur\w+\b", 4, r"actuari\w+|\binsur\w+\b|takaful|\bpricing\b|claims|underwrit\w+"),
    ("Presentation / Stakeholder", r"stakeholder\w*|present\w+|communicat\w+", 2, r"stakeholder\w*|present\w+|communicat\w+"),
    ("Git / Version Control", r"\bgit\b|github|gitlab|bitbucket|version control", 3, r"\bgit\b|github|gitlab|bitbucket"),
    ("Airflow / Orchestration", r"\bairflow\b|orchestrat\w+|\bluigi\b|\bprefect\b|\bdagster\b", 3, r"\bairflow\b|\bluigi\b|\bprefect\b|\bdagster\b"),
    ("Docker / Kubernetes", r"\bdocker\b|kubernetes|\bk8s\b|containeriz\w+|container\w*|helm", 3, r"\bdocker\b|kubernetes|\bk8s\b|containeriz\w+"),
    ("Bloomberg / Market Data", r"bloomberg|\bbloomberg terminal\b|\bbbg\b|market data|refinitiv|reuters", 2, r"bloomberg|\bbbg\b|market data|refinitiv"),
    ("SAP / ERP", r"\bsap\b|enterprise resource planning|sap hana|\bsap fico\b|\berp\b", 3, r"\bsap\b|sap hana|\berp\b"),
    ("Data Warehousing", r"data warehous\w+|\betl\b(?!.*alteryx)|snowflake|redshift|\bazure (?:synapse|sql data warehouse)\b", 3, r"data warehous\w+|snowflake|redshift|azure (?:synapse|sql data warehouse)"),
]

demand_patterns = {s[0]: s[1] for s in IN_DEMAND_SKILLS}
OWNED_PATTERNS = {s[0]: (s[3] if len(s) > 3 and s[3] else s[1]) for s in IN_DEMAND_SKILLS}

# Compiled once at import — `analyze` runs on every application and every market role.
TOOLS_COMPILED = [(label, re.compile(pattern, re.IGNORECASE), weight)
                  for label, pattern, weight in TOOLS]
DEMAND_COMPILED = [(s[0], re.compile(s[1], re.IGNORECASE), s[2])
                   for s in IN_DEMAND_SKILLS]
OWNED_COMPILED = {label: re.compile(pattern, re.IGNORECASE)
                  for label, pattern in OWNED_PATTERNS.items()}
DEMAND_COMPILED_MAP = {label: pattern for label, pattern, _ in DEMAND_COMPILED}


def owned_regex(label: str) -> str:
    return OWNED_PATTERNS.get(label, demand_patterns.get(label, ""))

TITLE_TARGETS = [
    (r"actuari\w+", 1.0),
    (r"(?:quant\w*|risk) (?:analyst|consultant|modell?er|manager)|risk model\w*", 1.0),
    (r"business intelligence (?:analyst|executive|specialist)|\bbi (?:analyst|executive)\b", 1.0),
    (r"data analyst|data analytics|analytics (?:analyst|consultant)|insight(?:s)? analyst", 1.0),
    (r"data scientist|machine learning", 0.8),
    (r"statistician|statistical analyst|research analyst", 0.75),
    (r"credit (?:analyst|officer)|financial analyst|business analyst|underwrit\w+|pricing analyst", 0.6),
    (r"analyst|analytics|\bdata\b", 0.45),
]

# Fresh-graduate seniority fit (order matters: senior tiers checked FIRST).
SENIORITY_TIERS = [
    (r"intern\b|industrial train\w+|industrial attach\w+", 1.0),
    (r"management train\w+|graduate train\w+|fresh graduate|entry[- ]level|graduate programme|graduate program|protege", 1.0),
    (r"\bmanager\b|\bavp\b|\bvp\d*\b|vice president|\bdirector\b|\bhead of\b|\bprincipal\b|\bchief\b", 0.35),
    (r"\bsenior\b|\bsr\.?\b|\blead\b|\bspecialist\b", 0.6),
    (r"\bjunior\b", 0.95),
    (r"\bexecutive\b", 1.0),          # MY convention: "Executive" = entry-level
    (r"\bassociate\b", 0.9),          # consulting "Associate" = entry analyst tier
    (r"\banalyst\b", 0.95),
]

QUAL_STRONG_FIELD = r"mathematic|statistic|actuari\w+|quantitative"
QUAL_ADJACENT_FIELD = r"data (?:science|analytics)|computer (?:science|engineering)|financ\w*|econom\w*|engineering|\brisk\b|insurance|business analytics"

LOCATION_OK = (r"malaysia|kuala lumpur|\bkl\b|selangor|petaling|subang|damansara|cyberjaya|"
               r"putrajaya|penang|johor|remote|worldwide|flexible|hybrid")
LOCATION_OFF = r"singapore|united kingdom|\buk\b|united states|\busa?\b|australia|hong kong"

FLOOR_SCORE = 8.0


def get_cv_text() -> str:
    if os.path.exists(CV_PATH):
        try:
            with open(CV_PATH, "r", encoding="utf-8") as fh:
                custom = fh.read().strip()
                if custom:
                    return custom
        except OSError:
            pass
    return DEFAULT_CV_TEXT


def save_cv_text(text: str):
    """Atomic write so a crash can never truncate the CV profile."""
    tmp = CV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, CV_PATH)


def _find_skills(text: str, lexicon):
    return [(label, weight) for label, pattern, weight in lexicon
            if pattern.search(text)]


def _title_alignment(title: str) -> float:
    t = (title or "").lower()
    for pattern, score in TITLE_TARGETS:
        if re.search(pattern, t):
            return score
    return 0.3


def _seniority_fit(title: str) -> float:
    t = (title or "").lower()
    for pattern, score in SENIORITY_TIERS:
        if re.search(pattern, t):
            return score
    return 0.95  # unmarked titles default to analyst-level


def _qualification_fit(jd_text: str) -> float:
    jd = jd_text.lower()
    m = re.search(
        r"(?:bachelor|masters?|master's|degree|bsc|msc|diploma|phd)[^.;\n]{0,120}"
        r"?in[^.;\n]{0,80}", jd)
    if not m:
        return 0.9  # no explicit degree requirement
    field = m.group(0)
    if re.search(QUAL_STRONG_FIELD, field):
        return 1.0
    if re.search(QUAL_ADJACENT_FIELD, field):
        return 0.85
    if re.search(r"phd|master|msc", field):
        return 0.78
    return 0.7  # unrelated field demanded


def _seniority_improvement(tier_score: float, title: str):
    t = (title or "").lower()
    if re.search(r"\bmanager\b|\bavp\b|\bvp\d*\b|vice president|\bdirector\b|\bhead of\b|\bprincipal\b|\bchief\b", t):
        return ("This is a leadership-tier posting — if you still want to apply, target the same "
                "team's Analyst/Executive track (search the company's early-career portal) and "
                "emphasize ownership of end-to-end projects to compensate for years of experience.")
    if re.search(r"\bsenior\b|\bsr\.?\b|\blead\b|\bspecialist\b", t):
        return ("Senior-tier ask: counter the experience gap by quantifying depth — dataset sizes, "
                "models validated, stakeholders supported — rather than listing tools.")
    return None


def _improvements(matched, missing, title_align: float, seniority: float, title: str):
    tips = []
    if missing:
        for label in [m[0] for m in missing[:2]]:
            if label == "Power BI":
                tips.append("Build one Power BI dashboard project (e.g., interactive claims or sales KPI report) "
                            "and list it above Excel — Power BI appears in this JD and pairs naturally with your SQL/VBA base.")
            elif label == "Tableau":
                tips.append("Publish a small Tableau Public viz mirroring this JD's metrics; one visible portfolio piece offsets the tool gap.")
            elif label == "SAS":
                tips.append("Frame your R GLM projects as 'SAS-equivalent statistical modelling' in the resume bullet, "
                            "and try SAS OnDemand for Academics so SAS reads as hands-on, not aspirational.")
            elif label.startswith("Prophet"):
                tips.append("Mention actuarial modelling concepts (reserving/projection logic) alongside your "
                            "SOA Exam P prep — concrete exam progress substitutes for vendor software exposure.")
            elif label.startswith("IFRS 17"):
                tips.append("Add an IFRS 17 / MFRS 17 mini-project (e.g., a discounting cash-flow model in Python) "
                            "to your portfolio to satisfy this insurance-domain requirement.")
            elif label == "Actuarial Exams":
                tips.append("Put 'SOA Exam P — Candidate (sitting [month/year])' in the resume header; "
                            "it is a hard screening filter for actuarial roles.")
            elif label.startswith("Alteryx"):
                tips.append("Describe one Python data pipeline (extract → clean → aggregate) in your bullets; "
                            "recruiters read it as Alteryx/ETL-equivalent capability.")
            elif label.startswith("Qlik"):
                tips.append("Add 'Qlik/Looker awareness (self-taught)' under a Tools section — low cost, covers this JD keyword.")
            elif label.startswith("Cloud"):
                tips.append("Host your next project's dataset on a free-tier cloud DB (e.g., AWS RDS) and note it in the resume.")
            elif label.startswith("Big Data"):
                tips.append("Quantify data scale in your bullets (row counts, chunked Pandas processing) — it maps to Spark-scale language.")
            elif label.startswith("Git"):
                tips.append("Host your projects on GitHub and link the repo in your resume header — version control is a cheap, visible signal for data roles.")
            elif label.startswith("Docker"):
                tips.append("Add one containerized project line (e.g. 'packaged the ETL in a Docker image') — a single bullet covers Docker/K8s keywords.")
            elif label.startswith("Airflow"):
                tips.append("Frame any scheduled Python job as 'scheduled ETL pipeline' — Airflow/Prefect keywords read as orchestration experience.")
            elif label.startswith("Bloomberg"):
                tips.append("Mention Bloomberg/Refinitiv awareness and any financial-data coursework — a line is enough to clear this screening keyword.")
            elif label.startswith("SAP"):
                tips.append("Note any ERP exposure (e.g. 'structured data exports from an ERP') — one line satisfies the SAP keyword without hands-on SAP.")
            elif label.startswith("Data Warehousing"):
                tips.append("Describe a project dataset as 'warehoused in SQL (Snowflake/Postgres)' — maps your SQL work onto warehouse tooling language.")
            elif label.startswith("C++"):
                tips.append("Emphasize numerical-computing strength (NumPy vectorization) as the performance-critical alternative to C++.")
            elif "Risk" in label:
                tips.append("Add one quantified risk-themed bullet (e.g., 'backtested a VaR proxy at 95% CI') "
                            "to satisfy this risk-domain keyword.")
            elif label.startswith("Actuarial / Insurance Domain"):
                tips.append("Add a pricing-flavoured bullet (e.g., GLM severity model on public claims data) — a direct hit for this JD.")
            else:
                tips.append(f"Add a one-line mention of {label} backed by a concrete project or coursework example.")
    if title_align < 0.9:
        tips.append("Mirror the exact job title keywords (e.g., 'Risk Analyst', 'Business Intelligence') in your resume "
                    "headline and first bullet — ATS filters weight this heavily.")
    if seniority < 0.8:
        extra = _seniority_improvement(seniority, title)
        if extra:
            tips.append(extra)
    if not tips or len(tips) < 3:
        tips.append("Lead with quantified achievements (%, RM amounts, dataset sizes) instead of duty lists — "
                    "this consistently lifts response rates for analyst roles.")
    return tips[:3]


def analyze(title: str, description: str) -> dict:
    title = title or ""
    description = description or ""
    jd_text = f"{title} {description}"
    cv_text = get_cv_text().lower()

    owned = _find_skills(cv_text, TOOLS_COMPILED)              # CV-confirmed toolset
    owned_map = {label: weight for label, weight in owned}

    jd_tools = _find_skills(jd_text, TOOLS_COMPILED)           # tools the JD asks about
    demands = _find_skills(jd_text, DEMAND_COMPILED)

    matched, missing = [], []
    for label, weight in demands:
        owned_pattern = OWNED_COMPILED.get(label)
        if label in owned_map or (owned_pattern and owned_pattern.search(cv_text)):
            if not any(ml == label for ml, _ in matched):
                matched.append((label, weight))
        else:
            missing.append((label, weight))
    for label, weight in jd_tools:
        if label in owned_map and not any(ml == label for ml, _ in matched):
            matched.append((label, weight))

    # Pillar 1: skills coverage (neutral 0.5 when the JD names none of our tooling)
    if jd_tools:
        total_w = sum(w for _, w in jd_tools)
        got_w = sum(owned_map.get(label, 0) for label, _ in jd_tools)
        skills = got_w / total_w if total_w else 0.5
    else:
        skills = 0.5
    if missing:
        gap_penalty = sum(w for _, w in missing) / (sum(w for _, w in missing) + 6.0)
        skills = max(0.0, skills - 0.25 * gap_penalty)

    # Pillars 2-5
    title_align = _title_alignment(title)
    seniority = _seniority_fit(title)
    qual = _qualification_fit(jd_text)
    blob = f"{title} {description}".lower()
    if re.search(LOCATION_OK, blob):
        location = 1.0
    elif re.search(LOCATION_OFF, blob):
        location = 0.5
    else:
        location = 0.9

    score = 100.0 * (0.40 * skills + 0.20 * title_align + 0.15 * qual
                     + 0.15 * seniority + 0.10 * location)
    score = max(FLOOR_SCORE, min(98.0, round(score, 1)))
    if not jd_tools and not demands and title_align >= 1.0:
        score = max(score, 55.0)  # title-aligned but skill-unknown JD text

    # Hard seniority caps: a fresh-grad profile is capped regardless of skill overlap.
    cap = 98.0
    if seniority <= 0.35:
        cap = 55.0      # manager / AVP / VP / director tier
    elif seniority <= 0.60:
        cap = 70.0      # senior / lead / specialist tier
    years = re.search(r"(\d+)\+?\s*(?:years?|yrs)\b", jd_text.lower())
    if years:
        n = int(years.group(1))
        if n >= 5:
            cap = min(cap, 60.0)
        elif n >= 3:
            cap = min(cap, 72.0)
    score = min(score, cap)

    return {
        "fit_score": score,
        "matched_skills": [m[0] for m in matched],
        "missing_skills": [m[0] for m in missing],
        "actionable_improvements": _improvements(matched, missing, title_align, seniority, title),
        "breakdown": {
            "Skills & Tools": round(100 * skills),
            "Title Match": round(100 * title_align),
            "Qualifications": round(100 * qual),
            "Seniority Fit": round(100 * seniority),
            "Location": round(100 * location),
            "_cap": cap,
        },
    }


def extract_requirements(text: str, limit: int = 8) -> list:
    """Pull requirement-ish sentences out of a raw JD/email snippet."""
    if not text:
        return []
    parts = re.split(r"[•·;\n]+|\.\s+", text)
    reqs = []
    seen = set()
    for part in parts:
        line = part.strip(" -–—*")
        if len(line) < 12 or len(line) > 240:
            continue
        if re.search(
                r"requirement|qualification|\bskills?\b|experience|degree|bachelor|"
                r"proficien|familiar|knowledge of|must (?:have|be)|\bminimum\b|\byears\b|"
                r"responsib|you will|candidate should|\bdegree\b|undergraduate",
                line, re.IGNORECASE):
            key = line[:60].lower()
            if key not in seen:
                seen.add(key)
                reqs.append(line)
        if len(reqs) >= limit:
            break
    return reqs


def top_missing(missing_skills, missing_json=None):
    if missing_skills:
        return missing_skills[0]
    if missing_json:
        try:
            items = json.loads(missing_json)
            if items:
                return items[0]
        except (ValueError, TypeError):
            pass
    return "—"
