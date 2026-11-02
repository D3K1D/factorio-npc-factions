from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from designer_llm.__main__ import main, read_context
from designer_llm._reference_contract import ContractError


BASE = Path(__file__).resolve().parents[1]
TEST_TEMP = BASE / "work" / "test-temp"


class CLITests(unittest.TestCase):
    def setUp(self):
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TEST_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"model": "explicit-test-model", "max_request_bytes": 96_000,
                                          "max_retries": 0, "max_output_tokens": 2048}))

    def args(self, mode="preflight"):
        return [mode, "--config", str(self.config), "--context", str(BASE / "fixtures" / "context.json"),
                "--goal", "Expand iron at the declared synthetic test site", "--out", str(self.root / "run"),
                "--memory", str(self.root / "memory.sqlite3")]

    def test_preflight_validates_full_http_payload_without_key_or_network(self):
        output = io.StringIO()
        with patch("designer_llm.__main__.MaskedCredentialSupplier", side_effect=AssertionError("key read")), \
                patch("designer_llm.providers.OpenAIHTTPTransport.__call__", side_effect=AssertionError("network")), \
                redirect_stdout(output):
            self.assertEqual(main(self.args()), 0)
        self.assertIn('"http_payload_bytes"', output.getvalue())
        self.assertIn("No credential was read", output.getvalue())
        self.assertFalse((self.root / "run").exists())

    def test_tiny_payload_limit_fails_before_paid_call_or_key_read(self):
        self.config.write_text(json.dumps({"model": "explicit-test-model", "max_request_bytes": 1024}))
        with patch("designer_llm.__main__.MaskedCredentialSupplier", side_effect=AssertionError("key read")), \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(main(self.args("live")), 2)
        self.assertFalse((self.root / "run").exists())

    def test_existing_output_and_custom_endpoint_fail_before_key(self):
        (self.root / "run").mkdir()
        with patch("designer_llm.__main__.MaskedCredentialSupplier", side_effect=AssertionError("key read")), \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(main(self.args("live")), 2)
        self.config.write_text(json.dumps({"model": "explicit-test-model", "endpoint": "https://example.com/v1/responses",
                                          "allow_custom_endpoint": True}))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(self.args()), 2)

    def test_configure_saves_model_and_budgets_without_a_credential(self):
        target = self.root / "new-config.json"
        with patch("builtins.input", side_effect=["explicit-test-model", "2048"]), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["configure", "--config", str(target)]), 0)
        data = json.loads(target.read_text())
        self.assertEqual(data["model"], "explicit-test-model")
        self.assertEqual(data["max_retries"], 0)
        self.assertNotIn("api_key", data)

    def test_context_file_is_bounded_before_parsing(self):
        large = self.root / "large.json"
        large.write_bytes(b" " * 128_001)
        with self.assertRaises(ContractError):
            read_context(large)


if __name__ == "__main__":
    unittest.main()
