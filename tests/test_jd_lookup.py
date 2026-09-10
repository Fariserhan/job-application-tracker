import unittest

import jd_lookup


class ScoreTests(unittest.TestCase):
    def test_company_score_strips_suffixes(self):
        self.assertGreater(
            jd_lookup.company_score("Acme Sdn Bhd", "Acme Berhad"), 0.8)
        self.assertLess(
            jd_lookup.company_score("Acme", "Totally Different Corp"), 0.5)

    def test_role_score_token_overlap(self):
        self.assertGreater(
            jd_lookup.role_score("Data Analyst", "Data Analyst, Risk"), 0.5)
        self.assertEqual(jd_lookup.role_score("", "Data Analyst"), 0.0)

    def test_match_score_weights_company(self):
        job = {"company": "Acme", "title": "Data Analyst"}
        self.assertGreater(
            jd_lookup.match_score("Data Analyst", "Acme", job),
            jd_lookup.match_score("Data Analyst", "Other Co", job))


class UsableUrlTests(unittest.TestCase):
    def test_usable(self):
        self.assertTrue(jd_lookup._usable_url("https://jobs.lever.co/acme/abc123"))
        self.assertFalse(jd_lookup._usable_url(""))
        self.assertFalse(jd_lookup._usable_url("https://www.jobstreet.com.my/job/None"))
        self.assertFalse(jd_lookup._usable_url("https://www.jobstreet.com.my/job"))


class LookupTests(unittest.TestCase):
    def test_strong_match_returns_hit(self):
        pool = [{"company": "Acme Sdn Bhd", "title": "Data Analyst",
                 "url": "https://jobs.lever.co/acme/abc123", "description": "jd",
                 "source": "LinkedIn"}]
        hit = jd_lookup.lookup("Data Analyst", "Acme", pool=pool)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["company"], "Acme Sdn Bhd")

    def test_weak_company_rejected(self):
        pool = [{"company": "Totally Different Corp", "title": "Data Analyst",
                 "url": "https://jobs.lever.co/other/abc123", "description": "jd",
                 "source": "LinkedIn"}]
        self.assertIsNone(jd_lookup.lookup("Data Analyst", "Acme", pool=pool))

    def test_url_only_hit_allowed_for_link_repair(self):
        pool = [{"company": "Acme", "title": "Data Analyst",
                 "url": "https://jobs.lever.co/acme/abc123", "description": "",
                 "source": "LinkedIn"}]
        hit = jd_lookup.lookup("Data Analyst", "Acme", pool=pool)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["url"], "https://jobs.lever.co/acme/abc123")

    def test_no_usable_url(self):
        pool = [{"company": "Acme", "title": "Data Analyst", "url": "",
                 "description": "jd", "source": "LinkedIn"}]
        self.assertIsNone(jd_lookup.lookup("Data Analyst", "Acme", pool=pool))


if __name__ == "__main__":
    unittest.main()
