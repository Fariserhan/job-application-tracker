"""Measure the deterministic parser against a hand-labelled email corpus.

    python -m jobtracker eval            # print the report
    python -m jobtracker eval --write    # ...and regenerate docs/parser-evaluation.md

Every corpus row is one email (sender, subject, snippet, body) with a human label written
from what the email *is*, never from what the parser outputs:

    {"is_application_event": bool, "status": "Applied|Assessment / OA|Interview|Offer|
     Rejected|none", "company": str, "role": str}

Each row goes through the production entry point ``parse_message(meta, body)`` with the
LLM fallback untouched (it is a separate, opt-in module), so the numbers describe the
offline rules alone. Metrics:

* event detection: precision / recall / F1 / accuracy (positive = application event)
* status: end-to-end accuracy on the true events. An event the parser missed counts as
  a wrong status, so detection errors are never hidden by a conditional denominator
  (the conditional figure is reported alongside); plus a confusion matrix
* company / role: normalised exact match on the true events (case, whitespace and
  punctuation ignored; an expected "" means the email names none, so 'Unknown' / '' match)
  and a lenient variant (legal suffixes dropped / containment) shown for context only
* a per-tag breakdown and the full failure list

The CI gate is event F1 >= 0.95 and end-to-end status accuracy >= 0.90.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..domain import APPLIED, ASSESSMENT, INTERVIEW, OFFER, REJECTED
from .pipeline import parse_message

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = PROJECT_ROOT / "tests" / "fixtures" / "email_corpus.jsonl"
REPORT_PATH = PROJECT_ROOT / "docs" / "parser-evaluation.md"

GATE_EVENT_F1 = 0.95
GATE_STATUS_ACCURACY = 0.90

STATUS_LABELS = [APPLIED, ASSESSMENT, INTERVIEW, OFFER, REJECTED]
MISSED = "(missed)"
NONE = "none"
# A fixed clock: the corpus is about content, not dates (2026-10-01 12:00 UTC).
_BASE_EPOCH_MS = 1_790_856_000_000

_LEGAL_SUFFIX = re.compile(
    r"\s+(?:sdn bhd|sdn|bhd|berhad|ltd|limited|plc|inc|llc|co|corp|corporation)$")

# --------------------------------------------------------------------- frozen history
# The corpus was built in three batches, and the numbers below were recorded once and are
# never recomputed. They are the record of how well the rules generalise:
#
#   dev        written first; every rule fix in this round was made while looking at it
#   holdout-1  written after round-1 fixes, scored blind once (BLIND_FIRST_LOOK), then its
#              failures drove round-2 fixes, so its *current* numbers are no longer blind
#   holdout-2  written after round-2 fixes, scored blind, never tuned on
#
# Fixing a holdout-2 failure makes it a dev row: add a fresh blind batch before quoting a
# new generalisation number.
BASELINE_V1 = {  # the parser before this evaluation existed (commit b5482af), same corpus
    "all": {"rows": 261, "precision": 0.851, "recall": 0.829, "f1": 0.840, "status": 0.638,
            "company": 0.388, "role": 0.395},
    "dev": {"rows": 177, "precision": 0.837, "recall": 0.845, "f1": 0.841, "status": 0.650,
            "company": 0.417, "role": 0.437},
    "holdout-1": {"rows": 46, "precision": 0.875, "recall": 0.778, "f1": 0.824,
                  "status": 0.630, "company": 0.259, "role": 0.259},
    "holdout-2": {"rows": 38, "precision": 0.900, "recall": 0.818, "f1": 0.857,
                  "status": 0.591, "company": 0.409, "role": 0.364},
}
BLIND_FIRST_LOOK = {  # each held-out batch, scored once before any fix it could inform
    "holdout-1": {"rows": 46, "precision": 0.889, "recall": 0.889, "f1": 0.889,
                  "status": 0.778, "company": 0.815, "role": 0.778},
    "holdout-2": {"rows": 38, "precision": 1.000, "recall": 0.955, "f1": 0.977,
                  "status": 0.818, "company": 0.773, "role": 0.818},
}

# Hand-written explanations for failures that are understood but deliberately left
# unfixed (genuinely ambiguous, beyond what deterministic rules should attempt, or in the
# blind holdout-2 batch, where fixing them would turn the blind estimate into a training
# score).
KNOWN_FAILURE_NOTES: dict[str, str] = {
    "dr-applied-07": "Agency recruiter; the client employer is undisclosed. The rules store "
                     "the agency (verified by the sender domain). A human would leave it blank.",
    "dr-interview-07": "Reply in a thread; the employer is named only in earlier messages and "
                       "in a concatenated domain ('tasikactuarial'). Needs thread context.",
    "rj-03": "Same as dr-interview-07: employer only in the domain and earlier messages.",
    "ho-rejected-07": "Same class: employer only in a concatenated sender domain.",
    "as-reminder-01": "Vendor reminder names the test ('Junior Data Engineer - SQL & Python'), "
                      "not the job; no title to extract without thread context.",
    "ho-applied-06": "Employer and programme run together ('Layang Re Graduate Program 2027') "
                     "with no separator; splitting needs a list of known employers.",
    "h2-applied-03": "Blind batch: trailing 'and' kept on the company ('Rimau Reinsurance and "
                     "for submitting…') and 'for the position of X' yielded role 'the'. Clear "
                     "rule bugs, deliberately left unfixed so holdout-2 stays blind.",
    "h2-rejected-01": "Blind batch: the hyphen in 'Follow-up' was read as a 'Company - Role' "
                      "separator. Rule bug, left unfixed (blind batch).",
    "h2-assessment-01": "Blind batch: 'attached is the case exercise … send your solution' matches "
                        "no assessment phrase the rules know. Flagged ambiguous (0.55), so the "
                        "optional fallback would review it.",
    "h2-offer-01": "Blind batch: DocuSign envelope; employer only in 'Angsana Capital HR sent "
                   "you a document'. No rule for e-signature senders.",
    "h2-rejected-02": "Blind batch: 'we have identified candidates whose experience more "
                      "closely matches' is an unseen rejection phrasing.",
    "h2-assessment-02": "Blind batch: unknown assessment vendor ('Penilai Assessments'); its "
                        "display name was taken as the employer.",
    "h2-interview-04": "Blind batch: role ran on into 'position tomorrow'.",
    "h2-rejected-03": "Blind batch: 'you're no longer being considered' is unseen phrasing "
                      "(only 'no longer under consideration' is known).",
    "h2-offer-02": "Blind batch: 'happy to offer you the role' / Malay 'menawarkan'. Only "
                   "'pleased/delighted to offer' are known.",
    "h2-assessment-03": "Blind batch: 'applying for the Graduate Risk Analyst programme'. "
                        "'programme' is not a recognised title terminator there.",
}


# ------------------------------------------------------------------ helpers


def load_corpus(path: Path | str | None = None) -> list[dict]:
    corpus_path = Path(path) if path else DEFAULT_CORPUS
    rows = []
    with open(corpus_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def row_meta(row: dict, index: int = 0) -> dict:
    return {
        "id": row["id"],
        "thread_id": row["id"],
        "internal_date": _BASE_EPOCH_MS + index * 60_000,
        "snippet": row.get("snippet", ""),
        "from": row.get("sender", ""),
        "subject": row.get("subject", ""),
        "date_header": "",
        "gmail_link": "",
    }


def normalise(text: str) -> str:
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _strip_legal(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, _LEGAL_SUFFIX.sub("", text)
    return text


def company_match(expected: str, got: str, lenient: bool = False) -> bool:
    exp, out = normalise(expected), normalise(got)
    if out == "unknown":
        out = ""
    if not exp:
        return not out
    if lenient:
        return bool(out) and _strip_legal(exp) == _strip_legal(out)
    return exp == out


def role_match(expected: str, got: str, lenient: bool = False) -> bool:
    exp, out = normalise(expected), normalise(got)
    if not exp:
        return not out
    if lenient:
        return bool(out) and (exp in out or out in exp)
    return exp == out


def _ratio(num: float, den: float) -> float:
    return num / den if den else 0.0


def _prf(tp: int, fp: int, fn: int, tn: int) -> dict:
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    f1 = _ratio(2 * precision * recall, precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall,
            "f1": f1, "accuracy": _ratio(tp + tn, tp + fp + fn + tn)}


# ------------------------------------------------------------------ evaluation


def evaluate(corpus_path: Path | str | None = None) -> dict[str, Any]:
    rows = load_corpus(corpus_path)
    results = []
    for index, row in enumerate(rows):
        rec = parse_message(row_meta(row, index), row.get("body", ""))
        label = row["label"]
        event = bool(label["is_application_event"])
        predicted_status = rec.status if rec.is_valid else (MISSED if event else NONE)
        results.append({
            "id": row["id"],
            "split": row.get("split", "dev"),
            "tags": row.get("tags", []),
            "event": event,
            "detected": rec.is_valid,
            "ambiguous": rec.ambiguous,
            "confidence": rec.confidence,
            "reason": rec.filter_reason,
            "expected_status": label["status"],
            "status": predicted_status,
            "expected_company": label.get("company", ""),
            "company": rec.company_name,
            "expected_role": label.get("role", ""),
            "role": rec.role_title,
        })
    for r in results:
        r["status_ok"] = r["event"] and r["status"] == r["expected_status"]
        r["company_ok"] = r["event"] and company_match(r["expected_company"], r["company"])
        r["company_lenient_ok"] = r["event"] and (
            r["company_ok"] or company_match(r["expected_company"], r["company"], lenient=True))
        r["role_ok"] = r["event"] and role_match(r["expected_role"], r["role"])
        r["role_lenient_ok"] = r["event"] and (
            r["role_ok"] or role_match(r["expected_role"], r["role"], lenient=True))

    return {
        "corpus": _composition(rows, corpus_path),
        "detection": _detection(results),
        "status": _status(results),
        "company": _field(results, "company"),
        "role": _field(results, "role"),
        "ambiguity": _ambiguity(results),
        "splits": _splits(results),
        "per_tag": _per_tag(results),
        "failures": _failures(results),
        "gate": _gate(results),
        "results": results,
    }


def _composition(rows: list[dict], corpus_path) -> dict:
    statuses = Counter(r["label"]["status"] for r in rows)
    tags = Counter(t for r in rows for t in r.get("tags", []))
    negatives = sum(1 for r in rows if not r["label"]["is_application_event"])
    return {
        "path": str(Path(corpus_path) if corpus_path else DEFAULT_CORPUS),
        "rows": len(rows),
        "events": len(rows) - negatives,
        "negatives": negatives,
        "negative_share": _ratio(negatives, len(rows)),
        "statuses": dict(statuses),
        "tags": dict(sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _detection(results: list[dict]) -> dict:
    tp = sum(1 for r in results if r["event"] and r["detected"])
    fp = sum(1 for r in results if not r["event"] and r["detected"])
    fn = sum(1 for r in results if r["event"] and not r["detected"])
    tn = sum(1 for r in results if not r["event"] and not r["detected"])
    return _prf(tp, fp, fn, tn)


def _status(results: list[dict]) -> dict:
    events = [r for r in results if r["event"]]
    detected = [r for r in events if r["detected"]]
    confusion: dict[str, dict[str, int]] = {
        s: dict.fromkeys([*STATUS_LABELS, MISSED], 0) for s in STATUS_LABELS}
    for r in events:
        confusion.setdefault(r["expected_status"], dict.fromkeys([*STATUS_LABELS, MISSED], 0))
        confusion[r["expected_status"]][r["status"]] = (
            confusion[r["expected_status"]].get(r["status"], 0) + 1)
    per_status = {}
    for s in STATUS_LABELS:
        row = confusion[s]
        support = sum(row.values())
        predicted = sum(confusion[e].get(s, 0) for e in confusion)
        tp = row.get(s, 0)
        precision, recall = _ratio(tp, predicted), _ratio(tp, support)
        per_status[s] = {"support": support, "precision": precision, "recall": recall,
                         "f1": _ratio(2 * precision * recall, precision + recall)
                         if precision + recall else 0.0}
    return {
        "n": len(events),
        "correct": sum(1 for r in events if r["status_ok"]),
        "accuracy": _ratio(sum(1 for r in events if r["status_ok"]), len(events)),
        "n_detected": len(detected),
        "conditional_accuracy": _ratio(sum(1 for r in detected if r["status_ok"]), len(detected)),
        "labels": [*STATUS_LABELS, MISSED],
        "confusion": confusion,
        "per_status": per_status,
    }


def _field(results: list[dict], name: str) -> dict:
    events = [r for r in results if r["event"]]
    named = [r for r in events if r[f"expected_{name}"]]
    return {
        "n": len(events),
        "accuracy": _ratio(sum(1 for r in events if r[f"{name}_ok"]), len(events)),
        "lenient_accuracy": _ratio(sum(1 for r in events if r[f"{name}_lenient_ok"]), len(events)),
        "n_named": len(named),
        "named_accuracy": _ratio(sum(1 for r in named if r[f"{name}_ok"]), len(named)),
    }


def _splits(results: list[dict]) -> dict:
    """Headline metrics per corpus split. 'dev' rows were used while fixing rules; 'holdout'
    rows were written afterwards and never tuned on, so they are the generalisation estimate."""
    out = {}
    for split in sorted({r["split"] for r in results}):
        rows = [r for r in results if r["split"] == split]
        out[split] = {
            "rows": len(rows),
            "events": sum(1 for r in rows if r["event"]),
            "detection": _detection(rows),
            "status_accuracy": _status(rows)["accuracy"],
            "company_accuracy": _field(rows, "company")["accuracy"],
            "role_accuracy": _field(rows, "role")["accuracy"],
        }
    return out


def _ambiguity(results: list[dict]) -> dict:
    wrong = [r for r in results if r["event"] != r["detected"]]
    flagged = [r for r in results if r["ambiguous"]]
    return {
        "flagged": len(flagged),
        "flagged_share": _ratio(len(flagged), len(results)),
        "detection_errors": len(wrong),
        "detection_errors_flagged": sum(1 for r in wrong if r["ambiguous"]),
        "confident_accuracy": _ratio(
            sum(1 for r in results if not r["ambiguous"] and r["event"] == r["detected"]),
            sum(1 for r in results if not r["ambiguous"])),
    }


def _per_tag(results: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        for tag in r["tags"]:
            groups[tag].append(r)
    table = {}
    for tag, members in sorted(groups.items()):
        events = [r for r in members if r["event"]]
        table[tag] = {
            "n": len(members),
            "events": len(events),
            "detection_accuracy": _ratio(
                sum(1 for r in members if r["event"] == r["detected"]), len(members)),
            "status_accuracy": _ratio(sum(1 for r in events if r["status_ok"]), len(events))
            if events else None,
            "company_accuracy": _ratio(sum(1 for r in events if r["company_ok"]), len(events))
            if events else None,
            "role_accuracy": _ratio(sum(1 for r in events if r["role_ok"]), len(events))
            if events else None,
        }
    return table


def _failures(results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        base = {"id": r["id"], "tags": r["tags"], "reason": r["reason"],
                "ambiguous": r["ambiguous"]}
        if not r["event"] and r["detected"]:
            out.append(base | {"kind": "false positive", "expected": "not an event",
                               "got": f"event ({r['status']})"})
            continue
        if r["event"] and not r["detected"]:
            out.append(base | {"kind": "missed event", "expected": r["expected_status"],
                               "got": f"invalid: {r['reason']}"})
            continue
        if not r["event"]:
            continue
        if not r["status_ok"]:
            out.append(base | {"kind": "status", "expected": r["expected_status"],
                               "got": r["status"]})
        if not r["company_ok"]:
            out.append(base | {"kind": "company", "expected": r["expected_company"] or "(none)",
                               "got": r["company"]})
        if not r["role_ok"]:
            out.append(base | {"kind": "role", "expected": r["expected_role"] or "(none)",
                               "got": r["role"] or "(none)"})
    return out


def _gate(results: list[dict]) -> dict:
    f1 = _detection(results)["f1"]
    status = _status(results)["accuracy"]
    return {
        "event_f1": f1, "event_f1_min": GATE_EVENT_F1,
        "status_accuracy": status, "status_accuracy_min": GATE_STATUS_ACCURACY,
        "passed": f1 >= GATE_EVENT_F1 and status >= GATE_STATUS_ACCURACY,
    }


# ------------------------------------------------------------------ reporting


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def format_text(report: dict) -> str:
    c, d, s = report["corpus"], report["detection"], report["status"]
    lines = [
        f"Parser evaluation: {c['rows']} labelled emails "
        f"({c['events']} events, {c['negatives']} negatives = {_pct(c['negative_share'])})",
        "",
        "Event detection",
        f"  precision {_pct(d['precision'])}  recall {_pct(d['recall'])}  "
        f"F1 {_pct(d['f1'])}  accuracy {_pct(d['accuracy'])}",
        f"  TP {d['tp']}  FP {d['fp']}  FN {d['fn']}  TN {d['tn']}",
        "",
        f"Status (end to end, n={s['n']}): {_pct(s['accuracy'])}   "
        f"conditional on detection (n={s['n_detected']}): {_pct(s['conditional_accuracy'])}",
        "  expected \\ predicted  " + "  ".join(f"{lab[:10]:>10}" for lab in s["labels"]),
    ]
    for exp in STATUS_LABELS:
        row = s["confusion"][exp]
        lines.append(f"  {exp:<20} " + "  ".join(f"{row.get(lab, 0):>10}" for lab in s["labels"]))
    co, ro = report["company"], report["role"]
    lines += [
        "",
        f"Company exact {_pct(co['accuracy'])} (lenient {_pct(co['lenient_accuracy'])})   "
        f"Role exact {_pct(ro['accuracy'])} (lenient {_pct(ro['lenient_accuracy'])})",
        "",
        "By split (dev / holdout-1 were used while fixing rules; holdout-2 never tuned on):",
    ]
    for split, m in report["splits"].items():
        lines.append(
            f"  {split:<8} n={m['rows']:<4} event F1 {_pct(m['detection']['f1'])}  "
            f"status {_pct(m['status_accuracy'])}  company {_pct(m['company_accuracy'])}  "
            f"role {_pct(m['role_accuracy'])}")
    lines += [
        "",
        f"Failures ({len(report['failures'])}):",
    ]
    for f in report["failures"]:
        flag = " [flagged ambiguous]" if f["ambiguous"] else ""
        lines.append(f"  {f['id']:<24} {f['kind']:<15} expected {f['expected']!r} got "
                     f"{f['got']!r}{flag}")
    g = report["gate"]
    lines += ["", f"Gate (event F1 >= {g['event_f1_min']:.2f}, status accuracy >= "
                  f"{g['status_accuracy_min']:.2f}): {'PASS' if g['passed'] else 'FAIL'}"]
    return "\n".join(lines)


def format_markdown(report: dict) -> str:
    c, d, s = report["corpus"], report["detection"], report["status"]
    co, ro, amb, g = report["company"], report["role"], report["ambiguity"], report["gate"]
    platform_tags = {k: v for k, v in c["tags"].items() if k.startswith(("platform:", "vendor:"))}
    other_tags = {k: v for k, v in c["tags"].items() if k not in platform_tags}
    out = [
        "# Parser evaluation",
        "",
        "> Generated by `python -m jobtracker eval --write` "
        "(`jobtracker/parsing/evaluate.py`). Do not edit by hand.",
        "",
        "## Methodology",
        "",
        f"* **Corpus**: `tests/fixtures/email_corpus.jsonl`: {c['rows']} hand-written, labelled "
        "emails modelled on real Malaysian / SEA job-search mail (LinkedIn, JobStreet, Workday, "
        "SuccessFactors, Oracle Cloud HCM, Greenhouse, Lever, iCIMS, Taleo, SmartRecruiters, "
        "Ashby, Hiredly, Prosple, Indeed, MYFutureJobs, recruiters writing from personal "
        "addresses, assessment vendors, calendar invites, Malay-English mail). Every employer "
        "is fictional.",
        "* **Labels** were written from human judgement of what each email *is*, before the "
        "parser was run, and were not changed to fit its output. Hard rows the rules still get "
        "wrong are kept and listed below.",
        "* **Three batches**: `dev` (written first; the rule fixes were made against it), "
        "`holdout-1` (written after the first round of fixes, scored blind once, then used for "
        "a second round) and `holdout-2` (written after the last fix, scored blind, never "
        "tuned on). Only holdout-2 is an unbiased estimate, and it is small: with 22 events, a "
        "95% interval on its status / company / role figures is roughly ±15–20 points.",
        "* **Run**: each row goes through the production entry point "
        "`parse_message(meta, body)`; the optional Claude fallback is not called, so these "
        "numbers describe the offline deterministic rules alone.",
        "* **Event detection**: positive class = *is an application event* (confirmation, "
        "assessment, interview, offer, rejection, or an employer update on an application).",
        "* **Status**: scored on the true events, *end to end*: a missed event counts as a wrong "
        "status. `Applied` covers confirmations and neutral updates. The conditional figure "
        "(only events the parser detected) is shown for reference.",
        "* **Company / role**: on the true events; case, whitespace and punctuation are ignored. "
        "An empty label means the email names none, so `Unknown` / empty is correct. The "
        "*lenient* column (legal suffixes like `Sdn Bhd` / `Berhad` dropped for companies; "
        "containment for roles) is context only. The headline is exact match.",
        f"* **CI gate**: event F1 ≥ {g['event_f1_min']:.2f} and status accuracy ≥ "
        f"{g['status_accuracy_min']:.2f} (`tests/test_parser_eval.py`).",
        "",
        "## Corpus composition",
        "",
        "| | count |",
        "|---|---:|",
        f"| emails | {c['rows']} |",
        f"| application events | {c['events']} |",
        f"| hard negatives | {c['negatives']} ({_pct(c['negative_share'])}) |",
    ]
    for status in STATUS_LABELS:
        out.append(f"| - {status} | {c['statuses'].get(status, 0)} |")
    out += ["", "Sources: " + ", ".join(f"`{k}` {v}" for k, v in platform_tags.items()), "",
            "Other tags: " + ", ".join(f"`{k}` {v}" for k, v in other_tags.items()), ""]
    out += [
        "## Headline metrics",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| event precision | {_pct(d['precision'])} |",
        f"| event recall | {_pct(d['recall'])} |",
        f"| **event F1** | **{_pct(d['f1'])}** |",
        f"| event accuracy | {_pct(d['accuracy'])} |",
        f"| **status accuracy** (end to end, n={s['n']}) | **{_pct(s['accuracy'])}** |",
        f"| status accuracy (detected events only, n={s['n_detected']}) | "
        f"{_pct(s['conditional_accuracy'])} |",
        f"| company exact match (n={co['n']}) | {_pct(co['accuracy'])} |",
        f"| company lenient match | {_pct(co['lenient_accuracy'])} |",
        f"| role exact match (n={ro['n']}) | {_pct(ro['accuracy'])} |",
        f"| role lenient match | {_pct(ro['lenient_accuracy'])} |",
        "",
        f"Detection counts: TP {d['tp']} · FP {d['fp']} · FN {d['fn']} · TN {d['tn']}. "
        f"Gate: **{'PASS' if g['passed'] else 'FAIL'}**.",
        "",
        f"Confidence flags: {amb['flagged']} of {c['rows']} emails "
        f"({_pct(amb['flagged_share'])}) are flagged `ambiguous` (the rows the optional LLM "
        f"fallback would review); {amb['detection_errors_flagged']} of the "
        f"{amb['detection_errors']} detection errors are among them. Detection accuracy on the "
        f"un-flagged rows is {_pct(amb['confident_accuracy'])}.",
        "",
        "## Before / after and generalisation",
        "",
        "`v1` = the parser before this evaluation existed (commit `b5482af`), run on the same "
        "corpus. *Blind* = the batch scored once, before any rule could be fitted to it.",
        "",
        "| rows | n | event F1 | status | company | role |",
        "|---|---:|---:|---:|---:|---:|",
        *_history_rows(report),
        "",
        "The generalisation estimate to quote is the **holdout-2 blind** row: it was written "
        "after the last rule change and never tuned on. The dev and holdout-1 rows are "
        "training scores, because the rules were fixed while looking at them.",
        "",
        "## Status confusion matrix",
        "",
        "Rows = labelled status, columns = parser output (`(missed)` = event not detected).",
        "",
        "| expected \\ predicted | " + " | ".join(s["labels"]) + " |",
        "|---|" + "---:|" * len(s["labels"]),
    ]
    for exp in STATUS_LABELS:
        row = s["confusion"][exp]
        out.append(f"| **{exp}** | " + " | ".join(
            f"**{row.get(lab, 0)}**" if lab == exp else str(row.get(lab, 0))
            for lab in s["labels"]) + " |")
    out += ["", "| status | support | precision | recall | F1 |", "|---|---:|---:|---:|---:|"]
    for status, m in s["per_status"].items():
        out.append(f"| {status} | {m['support']} | {_pct(m['precision'])} | "
                   f"{_pct(m['recall'])} | {_pct(m['f1'])} |")
    out += ["", "## Breakdown by tag", "",
            "| tag | n | events | detection | status | company | role |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    for tag, m in report["per_tag"].items():
        out.append(f"| `{tag}` | {m['n']} | {m['events']} | {_pct(m['detection_accuracy'])} | "
                   f"{_pct(m['status_accuracy'])} | {_pct(m['company_accuracy'])} | "
                   f"{_pct(m['role_accuracy'])} |")
    out += ["", "## Known failure modes", ""]
    failures = report["failures"]
    if not failures:
        out.append("None on this corpus. That says more about the corpus size than the "
                   "parser. Real inboxes will surface new templates.")
    else:
        out += ["Every row the rules still get wrong. These are kept in the corpus on purpose: "
                "removing or relabelling them would make the numbers look better and the tool "
                "worse.", "",
                "| id | kind | expected | got | flagged ambiguous | note |",
                "|---|---|---|---|---|---|"]
        for f in failures:
            note = KNOWN_FAILURE_NOTES.get(f"{f['id']}:{f['kind']}") or KNOWN_FAILURE_NOTES.get(
                f["id"], "")
            out.append(f"| `{f['id']}` | {f['kind']} | {_md(f['expected'])} | {_md(f['got'])} | "
                       f"{'yes' if f['ambiguous'] else 'no'} | {_md(note)} |")
    out += ["", *_FAILURE_THEMES, "",
            "## Caveats", "",
            f"* {c['rows']} emails is a small, hand-written sample, and the emails were written "
            "by the same author as the rules. The full-corpus numbers are mostly a training "
            "score and a regression gate; the holdout-2 blind row is the generalisation "
            "estimate, and even that is optimistic for templates unlike anything here.",
            "* The CI gate runs on the full corpus. It guards against regressions; it does not "
            "certify accuracy on a real inbox.",
            "* Rule fixes were made as general patterns (non-job application vetoes, conditional "
            "'if unsuccessful…' sentences, sender-domain-verified employer names, card layouts, "
            "subject decoration stripping), never as per-row special cases; the 48 legacy "
            "behaviour tests in `tests/test_parsing.py` still pass.",
            "* Real mail is HTML-heavy and often truncated in Gmail snippets; the corpus bodies "
            "are clean text, so extraction from messy HTML is not measured here.",
            ""]
    return "\n".join(out)


# Narrative summary of the failure classes, kept next to the code that measures them.
_FAILURE_THEMES: list[str] = [
    "### Failure themes",
    "",
    "* **Unseen phrasing is the main risk.** The blind holdout-2 batch had near-perfect event "
    "detection, but three of its 22 events got the wrong *status* because the rejection or "
    "offer was worded in a way no rule had seen ('identified candidates whose…', 'no longer "
    "being considered', 'happy to offer you'). Deterministic rules generalise by vocabulary; "
    "every new template family needs a pattern.",
    "* **Field extraction is the weakest layer.** Company and role are right on roughly four "
    "in five blind events. Errors are run-on captures ('Rimau Reinsurance and'), separators "
    "inside words ('Follow-up'), unknown vendors whose name looks like an employer, and "
    "employers named only in an earlier message of the thread or a concatenated domain.",
    "* **Thread context.** A reply that only says \"you're all set for Thursday\" carries no "
    "employer; the store merges messages by Gmail thread, so in the app the earlier "
    "confirmation usually supplies it. The per-email numbers here understate the "
    "per-application result for those rows.",
    "* **Ambiguity flags help.** Rows the rules are unsure about are flagged (`ambiguous`, "
    "confidence < 0.75) for the optional, off-by-default LLM fallback; a recruiter writing "
    "'Next steps' from a personal company address is flagged rather than silently dropped.",
]


def _history_rows(report: dict) -> list[str]:
    def row(name: str, m: dict) -> str:
        return (f"| {name} | {m['rows']} | {_pct(m['f1'])} | {_pct(m['status'])} | "
                f"{_pct(m['company'])} | {_pct(m['role'])} |")

    def current(m: dict) -> dict:
        return {"rows": m["rows"], "f1": m["detection"]["f1"], "status": m["status_accuracy"],
                "company": m["company_accuracy"], "role": m["role_accuracy"]}

    c = report["corpus"]
    rows = [row("all (v1 parser)", BASELINE_V1["all"]),
            row("all (**current**)", {"rows": c["rows"], "f1": report["detection"]["f1"],
                                       "status": report["status"]["accuracy"],
                                       "company": report["company"]["accuracy"],
                                       "role": report["role"]["accuracy"]})]
    for split, m in report["splits"].items():
        if split in BASELINE_V1:
            rows.append(row(f"{split} (v1 parser)", BASELINE_V1[split]))
        if split in BLIND_FIRST_LOOK:
            rows.append(row(f"{split} (blind first look)", BLIND_FIRST_LOOK[split]))
        rows.append(row(f"{split} (current)", current(m)))
    return rows


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def main(write_report: bool = False) -> int:
    report = evaluate()
    print(format_text(report))
    if write_report:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(format_markdown(report), encoding="utf-8")
        print(f"\nwrote {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
