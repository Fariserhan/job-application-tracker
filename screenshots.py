"""Capture README screenshots of the demo dashboard (demo_wrapper must be running).

Run: venv\\Scripts\\python.exe screenshots.py
"""
import sys

from playwright.sync_api import sync_playwright

OUT = "docs/screenshots"
URL = "http://127.0.0.1:8502"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 2200})
    page.goto(URL, wait_until="networkidle")
    page.wait_for_timeout(8000)  # let the websocket-driven widgets settle
    page.screenshot(path=f"{OUT}/dashboard-overview.png", full_page=True)
    page.screenshot(path=f"{OUT}/dashboard-top.png", full_page=False)
    browser.close()

print("screenshots written to", OUT)
