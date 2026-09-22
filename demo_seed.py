"""Generate demo.db - a SYNTHETIC dataset for README screenshots only.

Never touches real data (applications.db / job_tracker.csv / sync_state.json are
untouched: all storage paths are redirected to demo_* files first).

Run: venv\\Scripts\\python.exe demo_seed.py
"""
import random

import storage

storage.DB_PATH = "demo.db"
storage.CSV_PATH = "demo_job_tracker.csv"
storage.STATE_PATH = "demo_sync_state.json"

COMPANIES = [
    "Acme Insurance", "Bering Re", "Cahaya Bank", "Delta Takaful", "Epsilon Assurance",
    "Fajar Analytics", "Gemilang Life", "Harbour Mutual", "Impian Re", "Jaya Pricing Co",
    "Kencana Insurance", "Langit Consulting", "Meridian Actuarial", "Nadi Health", "Ombak Re",
    "Perdana Bank", "Qistina Analytics", "Riang Data Co", "Selat Re", "Teras Insurance",
    "Utama Life", "Warna BI Studio", "Zenith Risk Partners", "Bayu Analytics",
]
ROLES = [
    "Actuarial Analyst", "Data Analyst", "BI Analyst", "Risk Analyst",
    "Pricing Analyst", "Reserving Analyst", "Junior Data Scientist", "Reporting Analyst",
]
PLATFORMS = ["LinkedIn", "JobStreet", "MyFutureJobs", "Indeed", "Prosple", "Company Site"]

random.seed(42)
storage.init_db()

statuses = (
    ["Applied"] * 78 + ["Rejected"] * 26 + ["Ghosted"] * 14 +
    ["Response"] * 10 + ["Interview"] * 6 + ["Offer"] * 1
)
random.shuffle(statuses)

n = 0
for i, status in enumerate(statuses, 1):
    day = 120 - int(i * 0.8) - random.randint(0, 5)  # spread over ~4 months
    date = f"2026-{max(5, 9 - (day // 30)):02d}-{(day % 27) + 1:02d}"
    storage.upsert_application({
        "thread_id": f"DEMO-{i:03d}",
        "company_name": random.choice(COMPANIES),
        "role_title": random.choice(ROLES),
        "source_platform": random.choice(PLATFORMS),
        "application_date": date,
        "current_status": status,
        "last_updated": date,
        "job_description_snippet": "Synthetic demo record.",
        "job_url": f"https://example.com/jobs/{i}",
        "gmail_link": "https://mail.google.com/",
        "latest_subject": "Application received",
        "is_valid": True,
        "filter_reason": "affirmed",
    })
    n += 1

print(f"demo.db seeded with {n} synthetic applications")
