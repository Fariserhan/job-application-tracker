import unittest
from unittest import mock

import discovery


SAMPLE_HTML = """
<div class="base-card base-search-card base-search-card--link job-search-card"
     data-entity-urn="urn:li:jobPosting:4012345678">
  <a class="base-card__full-link"
     href="https://www.linkedin.com/jobs/view/risk-analyst-at-acme-4012345678?position=1&amp;trk=x">
  </a>
  <h3 class="base-search-card__title">Risk Analyst</h3>
  <h4 class="base-search-card__subtitle"><a class="hidden-nested-link"
      href="https://www.linkedin.com/company/acme">Acme Sdn Bhd</a></h4>
  <span class="job-search-card__location">Kuala Lumpur, Malaysia</span>
  <time class="job-search-card__listdate" datetime="2026-08-30">1 day ago</time>
</div>
<div class="base-card base-search-card base-search-card--link job-search-card"
     data-entity-urn="urn:li:jobPosting:4099999999">
  <h3 class="base-search-card__title">Data Analyst</h3>
  <h4 class="base-search-card__subtitle"><a href="https://www.linkedin.com/company/beta">Beta Corp</a></h4>
  <span class="job-search-card__location">Selangor, Malaysia</span>
</div>
"""


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class CleanTests(unittest.TestCase):
    def test_clean_removes_tags_and_decodes_entities(self):
        self.assertEqual(discovery._clean("<b>Data &amp; Analytics</b>"),
                         "Data & Analytics")
        self.assertEqual(discovery._clean("&quot;Risk&quot; &#39;A&#39;"),
                         '"Risk" \'A\'')
        self.assertEqual(discovery._clean("  a\n\n  b  "), "a b")

    def test_job_key_is_stable(self):
        k1 = discovery.job_key("LinkedIn", "https://x", "Data Analyst")
        k2 = discovery.job_key("LinkedIn", "https://x", "Data Analyst")
        k3 = discovery.job_key("LinkedIn", "https://y", "Data Analyst")
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)


class LinkedInCardTests(unittest.TestCase):
    def test_cards_split_without_tearing(self):
        cards = discovery._linkedin_cards(SAMPLE_HTML)
        self.assertEqual(len(cards), 2)
        self.assertIn("Risk Analyst", cards[0])
        self.assertIn("Data Analyst", cards[1])

    def test_fetch_linkedin_parses_fields(self):
        session = mock.Mock()
        session.get.return_value = FakeResponse(SAMPLE_HTML)
        with mock.patch.object(discovery, "_session", return_value=session), \
                mock.patch.object(discovery.time, "sleep"):
            jobs = discovery.fetch_linkedin("Risk Analyst", "Malaysia")
        self.assertEqual(len(jobs), 2)
        first = jobs[0]
        self.assertEqual(first["title"], "Risk Analyst")
        self.assertEqual(first["company"], "Acme Sdn Bhd")
        self.assertIn("linkedin.com/jobs/view/", first["url"])
        self.assertNotIn("&amp;", first["url"])
        self.assertEqual(first["posted_date"], "2026-08-30")

    def test_fetch_linkedin_non_200_retries_then_empty(self):
        session = mock.Mock()
        session.get.return_value = FakeResponse("", status_code=429)
        with mock.patch.object(discovery, "_session", return_value=session), \
                mock.patch.object(discovery.time, "sleep"):
            jobs = discovery.fetch_linkedin("Risk Analyst", "Malaysia")
        self.assertEqual(jobs, [])
        self.assertEqual(session.get.call_count, 2)


class ProviderRobustnessTests(unittest.TestCase):
    def test_jobstreet_network_error_returns_empty(self):
        import requests
        fake = mock.Mock()
        fake.get.side_effect = requests.exceptions.ConnectionError("no network")
        with mock.patch.object(discovery, "_session", return_value=fake), \
                mock.patch.object(discovery.time, "sleep"):
            self.assertEqual(discovery.fetch_jobstreet("Data Analyst", "Malaysia"), [])

    def test_remotive_network_error_returns_empty(self):
        import requests
        fake = mock.Mock()
        fake.get.side_effect = requests.exceptions.Timeout("slow")
        with mock.patch.object(discovery, "_session", return_value=fake), \
                mock.patch.object(discovery.time, "sleep"):
            self.assertEqual(discovery.fetch_remotive("Data Analyst", "Malaysia"), [])


if __name__ == "__main__":
    unittest.main()
