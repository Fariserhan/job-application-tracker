import base64
import unittest

import gmail_fetcher


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _html_part(html: str) -> dict:
    return {"mimeType": "text/html", "body": {"data": _b64(html)}}


def _plain_part(text: str) -> dict:
    return {"mimeType": "text/plain", "body": {"data": _b64(text)}}


class BodyExtractionTests(unittest.TestCase):
    def test_multipart_keeps_plain_and_appends_html_links(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                _plain_part("Thanks for applying."),
                _html_part('<a href="https://jobs.lever.co/acme/abc123">View application</a>'),
            ],
        }
        body = gmail_fetcher._extract_body(payload)
        self.assertIn("Thanks for applying.", body)
        self.assertIn("https://jobs.lever.co/acme/abc123", body)

    def test_html_only_strips_tags_and_decodes_entities(self):
        payload = _html_part("<p>Data &amp; Analytics &lt;role&gt;</p>")
        body = gmail_fetcher._extract_body(payload)
        self.assertIn("Data & Analytics <role>", body)

    def test_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [{
                "mimeType": "multipart/alternative",
                "parts": [_plain_part("inner text")],
            }],
        }
        self.assertEqual(gmail_fetcher._extract_body(payload), "inner text")

    def test_empty_payload(self):
        self.assertEqual(gmail_fetcher._extract_body({}), "")

    def test_hrefs_unescape_amp(self):
        hrefs = gmail_fetcher._hrefs_from_html(
            '<a href="https://x.com/job?a=1&amp;b=2">x</a>')
        self.assertEqual(hrefs, ["https://x.com/job?a=1&b=2"])


class MetadataTests(unittest.TestCase):
    def test_full_metadata(self):
        message = {
            "id": "m1", "threadId": "t1", "internalDate": "1735689600000",
            "snippet": "snip",
            "payload": {"headers": [{"name": "From", "value": "a@b.com"},
                                    {"name": "Subject", "value": "Hi"}]},
        }
        meta = gmail_fetcher._parse_metadata(message)
        self.assertEqual(meta["id"], "m1")
        self.assertEqual(meta["thread_id"], "t1")
        self.assertEqual(meta["internal_date"], 1735689600000)
        self.assertIn("t1", meta["gmail_link"])

    def test_missing_fields_do_not_raise(self):
        meta = gmail_fetcher._parse_metadata({})
        self.assertEqual(meta["id"], "")
        self.assertEqual(meta["thread_id"], "")
        self.assertEqual(meta["internal_date"], 0)
        self.assertEqual(meta["gmail_link"], "")

    def test_bad_internal_date(self):
        meta = gmail_fetcher._parse_metadata({"internalDate": "not-a-number"})
        self.assertEqual(meta["internal_date"], 0)


class _FakeRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        return self._response


class _FakeMessagesAPI:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRequest(self._pages.pop(0))


class _FakeService:
    def __init__(self, pages):
        self.messages_api = _FakeMessagesAPI(pages)

    def users(self):
        class _Users:
            def __init__(self, api):
                self._api = api

            def messages(self):
                return self._api

        return _Users(self.messages_api)


class SearchQueryTests(unittest.TestCase):
    def test_query_covers_platforms_and_body_phrases(self):
        query = gmail_fetcher.SEARCH_QUERY
        for token in ("myworkdayjobs.com", "icims.com", "oraclecloud.com",
                      "hirevue.com", "thank you for applying",
                      "we regret to inform", "in:anywhere", "-in:trash"):
            self.assertIn(token, query, token)

    def test_query_excludes_marketing_subjects(self):
        self.assertIn("job alert", gmail_fetcher.SEARCH_QUERY)

    def test_two_complementary_queries_stay_within_length(self):
        self.assertEqual(len(gmail_fetcher.SEARCH_QUERIES), 2)
        for query in gmail_fetcher.SEARCH_QUERIES:
            self.assertLess(len(query), 1900, query)

    def test_search_paginates_and_caps(self):
        pages = [
            {"messages": [{"id": f"a{i}"} for i in range(500)], "nextPageToken": "t1"},
            {"messages": [{"id": f"b{i}"} for i in range(500)]},
        ]
        service = _FakeService(pages)
        ids = gmail_fetcher.search_messages(service, after_epoch=None, max_results=750)
        self.assertEqual(len(ids), 750)
        self.assertEqual(ids[0], "a0")
        self.assertEqual(len(service.messages_api.calls), 2)


if __name__ == "__main__":
    unittest.main()
