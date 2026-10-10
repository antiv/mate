"""Configuration drift must fail --check even after reference regeneration."""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class ConfigurationCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "gen_docs_configuration", repo / "scripts" / "gen_docs.py"
        )
        self.docs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.docs)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.docs.ROOT = self.root
        self.docs.REFERENCE_DIR = self.root / "docs" / "reference"
        self.source = self.root / "settings.py"
        self.example = self.root / ".env.example"
        for name, value in (
            ("tracked_files", ["settings.py", ".env.example"]),
            ("lint_guides", []), ("migration_gaps", []), ("guides", []),
        ):
            patch = mock.patch.object(self.docs, name, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(self.docs, "build_reference", side_effect=lambda: {
            "configuration.md": self.docs.render_configuration(["settings.py"])
        })
        patch.start()
        self.addCleanup(patch.stop)

    def check(self, source: str, example: str = "") -> tuple:
        self.source.write_text(source, encoding="utf-8")
        self.example.write_text(example, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.docs.cmd_generate(check=False), 0)
        before = (self.docs.REFERENCE_DIR / "configuration.md").read_bytes()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.docs.cmd_generate(check=True)
        self.assertEqual((self.docs.REFERENCE_DIR / "configuration.md").read_bytes(), before)
        self.assertNotIn("out of date", output.getvalue())
        return result, output.getvalue()

    def test_missing_example_entry_fails_after_regeneration(self) -> None:
        result, output = self.check('import os\nos.getenv("SOME_NEW_VAR")\n')
        self.assertEqual(result, 1)
        self.assertIn("SOME_NEW_VAR", output)
        self.assertIn(".env.example", output)
        self.assertIn("commented", output)

    def test_conflicting_defaults_fail_after_regeneration(self) -> None:
        result, output = self.check(
            'import os\nos.getenv("DB_TYPE", "sqlite")\nos.getenv("DB_TYPE", "postgresql")\n',
            '# Database backend\n# DB_TYPE=sqlite\n',
        )
        self.assertEqual(result, 1)
        for text in ("DB_TYPE", "sqlite", "postgresql", "settings.py", "shared/utils/settings.py"):
            self.assertIn(text, output)

    def test_allowlisted_variable_stays_visible_without_failing(self) -> None:
        result, output = self.check('import os\nos.getenv("TIKTOKEN_CACHE_DIR")\n')
        self.assertEqual(result, 0, output)
        page = (self.docs.REFERENCE_DIR / "configuration.md").read_text(encoding="utf-8")
        self.assertIn("missing from `.env.example`", page)
        self.assertIn("TIKTOKEN_CACHE_DIR", page)

    def test_allowlist_does_not_hide_conflicting_defaults(self) -> None:
        result, output = self.check(
            'import os\nos.getenv("TIKTOKEN_CACHE_DIR", "a")\nos.getenv("TIKTOKEN_CACHE_DIR", "b")\n'
        )
        self.assertEqual(result, 1)
        self.assertIn("TIKTOKEN_CACHE_DIR", output)

    def test_documented_consistent_default_passes(self) -> None:
        result, output = self.check(
            'import os\nos.getenv("DB_TYPE", "sqlite")\nos.getenv("DB_TYPE", "sqlite")\n',
            '# Database backend\n# DB_TYPE=sqlite\n',
        )
        self.assertEqual(result, 0, output)
