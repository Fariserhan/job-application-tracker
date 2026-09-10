import unittest
from unittest import mock

import enricher


class OgExtractionTests(unittest.TestCase):
    def test_property_before_content(self):
        html = ('<html><head><meta property="og:title" content="Data Analyst - Acme">'
                '<meta property="og:description" content="Great role &amp; benefits">'
                "</head></html>")
        tags = enricher.extract_og(html)
        self.assertEqual(tags["og_title"], "Data Analyst - Acme")
        self.assertEqual(tags["og_description"], "Great role & benefits")

    def test_content_before_property(self):
        html = ('<meta content="Data Analyst" property="og:title">'
                '<meta content="Desc" property="og:description">')
        tags = enricher.extract_og(html)
        self.assertEqual(tags["og_title"], "Data Analyst")
        self.assertEqual(tags["og_description"], "Desc")

    def test_title_fallback(self):
        tags = enricher.extract_og("<title>Fallback Title</title>")
        self.assertEqual(tags["title"], "Fallback Title")

    def test_empty_html(self):
        self.assertEqual(enricher.extract_og(""), {})


class JsonLdTests(unittest.TestCase):
    def test_jobposting_extraction(self):
        payload = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Risk Analyst",
            "description": "<p>Do <b>risk</b> analysis with Python</p>",
            "datePosted": "2026-08-01",
            "employmentType": "FULL_TIME",
            "hiringOrganization": {"@type": "Organization", "name": "Acme Bhd"},
            "jobLocation": {"@type": "Place", "address": {
                "@type": "PostalAddress", "addressLocality": "Kuala Lumpur",
                "addressRegion": "Selangor", "addressCountry": "MY"}},
            "baseSalary": {"@type": "MonetaryAmount", "currency": "MYR",
                           "value": {"@type": "QuantitativeValue",
                                     "minValue": 5000, "maxValue": 8000}},
        }
        html = ('<script type="application/ld+json">'
                + __import__("json").dumps(payload)
                + "</script>")
        ld = enricher.extract_jsonld(html)
        self.assertEqual(ld["title"], "Risk Analyst")
        self.assertEqual(ld["company"], "Acme Bhd")
        self.assertIn("Python", ld["description"])
        self.assertIn("Kuala Lumpur", ld["location"])
        self.assertIn("5000", ld["salary"])

    def test_broken_json_is_ignored(self):
        html = '<script type="application/ld+json">{bad json</script>'
        self.assertEqual(enricher.extract_jsonld(html), {})


class UrlSafetyTests(unittest.TestCase):
    def test_private_hosts_blocked(self):
        for url in ("http://localhost:8501", "http://127.0.0.1/x",
                    "http://10.0.0.5/x", "http://192.168.1.10/x",
                    "http://172.16.5.5/x", "file:///etc/passwd",
                    "ftp://example.com/x", "http://intranet/x"):
            self.assertFalse(enricher._is_public_url(url), url)

    def test_public_hosts_allowed(self):
        for url in ("https://www.linkedin.com/jobs/view/1",
                    "http://example.com/job", "https://jobs.lever.co/acme/abc"):
            self.assertTrue(enricher._is_public_url(url), url)

    def test_fetch_page_refuses_private_url(self):
        page, error, _ = enricher.fetch_page("http://127.0.0.1:8080/secret")
        self.assertIsNone(page)
        self.assertIn("non-public", error)


class EnrichTests(unittest.TestCase):
    def test_enrich_with_mocked_fetch(self):
        page = ('<meta property="og:title" content="Data Analyst">'
                '<meta property="og:description" content="Short summary">'
                '<script type="application/ld+json">{"@type":"JobPosting",'
                '"title":"Data Analyst","description":"' + ("Long JD text. " * 30) + '"}'
                "</script>")
        with mock.patch.object(enricher, "fetch_page",
                               return_value=(page, None, "https://x/final")):
            result = enricher.enrich("https://x")
        self.assertTrue(result["ok"])
        self.assertIn("Long JD text", result["og_description"])
        self.assertEqual(result["url"], "https://x/final")

    def test_enrich_failure_shape(self):
        with mock.patch.object(enricher, "fetch_page",
                               return_value=(None, "timeout", "https://x")):
            result = enricher.enrich("https://x")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "timeout")

    def test_enrich_many_empty(self):
        self.assertEqual(enricher.enrich_many({}), {})


if __name__ == "__main__":
    unittest.main()
