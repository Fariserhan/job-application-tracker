import json
import os
import shutil
import tempfile
import unittest

import ai


class SmallHelperTests(unittest.TestCase):
    def test_cv_signature(self):
        self.assertEqual(ai.cv_signature("abc"), ai.cv_signature("abc"))
        self.assertNotEqual(ai.cv_signature("abc"), ai.cv_signature("abd"))
        self.assertEqual(len(ai.cv_signature("abc")), 12)

    def test_sanitize_fit_results(self):
        clean = ai.sanitize_fit_results({
            "a": {"fit_score": 70},
            "b": {"fit_score": "high"},
            "c": "not a dict",
        })
        self.assertEqual(set(clean), {"a"})

    def test_parse_json_text(self):
        self.assertEqual(ai.parse_json_text('{"a": 1}'), {"a": 1})
        self.assertEqual(ai.parse_json_text('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(ai.parse_json_text('noise {"a": 1} tail'), {"a": 1})
        self.assertEqual(ai.parse_json_text(""), {})
        self.assertEqual(ai.parse_json_text("[1,2]"), {})

    def test_sanitize_key(self):
        self.assertEqual(ai._sanitize_key("  sk-\u274c123\n"), "sk-123")
        self.assertEqual(ai._sanitize_key(None), "")

    def test_model_version_and_sort(self):
        self.assertGreater(ai.model_version("gemini-3.6-flash"),
                           ai.model_version("gemini-2.5-flash"))
        ordered = ai._newest_first(["gemini-2.0-flash", "gemini-3.6-flash",
                                    "gemini-3.6-flash-lite"])
        self.assertEqual(ordered[0], "gemini-3.6-flash")

    def test_providers_registry(self):
        self.assertIn("Google Gemini (free)", ai.providers())
        self.assertIn("Ollama (local)", ai.providers())


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jobsuite_ai_")
        self._orig_env = os.environ.get("AI_CACHE_PATH")
        os.environ["AI_CACHE_PATH"] = os.path.join(self.tmp, "cache.json")
        self._orig_generate = ai.generate
        ai.clear_cache()

    def tearDown(self):
        ai.generate = self._orig_generate
        ai.clear_cache()
        if self._orig_env is None:
            os.environ.pop("AI_CACHE_PATH", None)
        else:
            os.environ["AI_CACHE_PATH"] = self._orig_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generate_cached_only_calls_once(self):
        calls = {"n": 0}

        def fake_generate(system, prompt, model=None, **kwargs):
            calls["n"] += 1
            return "hello world"

        ai.generate = fake_generate
        first = ai.generate_cached("sys", "prompt")
        second = ai.generate_cached("sys", "prompt")
        self.assertEqual(first, "hello world")
        self.assertEqual(second, "hello world")
        self.assertEqual(calls["n"], 1)

    def test_failed_generation_is_not_cached(self):
        ai.generate = lambda *a, **k: None
        self.assertIsNone(ai.generate_cached("sys", "prompt"))
        calls = {"n": 0}

        def later(system, prompt, model=None, **kwargs):
            calls["n"] += 1
            return "ok"

        ai.generate = later
        self.assertEqual(ai.generate_cached("sys", "prompt"), "ok")
        self.assertEqual(calls["n"], 1)

    def test_batch_json_parses(self):
        ai.generate = lambda *a, **k: '{"x": 1}'
        self.assertEqual(ai.batch_json("sys", "prompt"), {"x": 1})

    def test_clear_cache(self):
        ai.generate = lambda *a, **k: "value"
        ai.generate_cached("s", "p")
        self.assertGreaterEqual(ai.clear_cache(), 0)


class JsonModeTests(unittest.TestCase):
    def setUp(self):
        self._orig_config = ai._client_config
        self._orig_post = ai.requests.post
        ai._client_config = lambda: {
            "base_url": "http://example.test/v1",
            "api_key": "k",
            "model": "m",
        }

    def tearDown(self):
        ai._client_config = self._orig_config
        ai.requests.post = self._orig_post

    def _capture(self):
        seen = {}

        class FakeResp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": '{"ok": 1}'},
                                     "finish_reason": "stop"}]}

        def fake_post(endpoint, headers=None, json=None, timeout=None):
            seen["payload"] = json
            return FakeResp()

        ai.requests.post = fake_post
        return seen

    def test_json_mode_sends_response_format_and_system_first(self):
        seen = self._capture()
        self.assertEqual(ai.generate("SYS", "USER", json_mode=True), '{"ok": 1}')
        self.assertEqual(seen["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in seen["payload"]["messages"]],
                         ["system", "user"])

    def test_plain_generate_omits_response_format(self):
        seen = self._capture()
        ai.generate("SYS", "USER")
        self.assertNotIn("response_format", seen["payload"])

    def test_batch_json_requests_json_mode(self):
        captured = {}

        def fake_generate_cached(system, prompt, **kwargs):
            captured.update(kwargs)
            return '{"x": 1}'

        orig = ai.generate_cached
        ai.generate_cached = fake_generate_cached
        try:
            self.assertEqual(ai.batch_json("s", "p"), {"x": 1})
        finally:
            ai.generate_cached = orig
        self.assertTrue(captured.get("json_mode"))


class FitPromptTests(unittest.TestCase):
    def _capture_system(self, **kwargs):
        captured = {}
        orig = ai.batch_json

        def fake(system, prompt, **k):
            captured["system"] = system
            return {}

        ai.batch_json = fake
        try:
            ai.fit_score_batch([{"id": "x", "title": "t", "description": "d"}], **kwargs)
        finally:
            ai.batch_json = orig
        return captured["system"]

    def test_real_cv_text_is_sent_to_model(self):
        system = self._capture_system(cv_text="UNIQUE_CV_MARKER_123 actuarial experience")
        self.assertIn("UNIQUE_CV_MARKER_123", system)

    def test_falls_back_to_builtin_summary_without_cv(self):
        system = self._capture_system(cv_text=None)
        self.assertIn(ai._CV_SIG, system)


if __name__ == "__main__":
    unittest.main()
