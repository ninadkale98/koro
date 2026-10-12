import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DocsInSync(unittest.TestCase):
    def test_generated_copies_match(self):
        spec = importlib.util.spec_from_file_location("sync_docs", ROOT / "tools" / "sync_docs.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for rel, content in mod.render().items():
            self.assertEqual((ROOT / rel).read_text(encoding="utf-8"), content,
                             f"{rel} is out of date: run python tools/sync_docs.py")

    def test_plugin_shortcuts_point_at_learn(self):
        for name in ("continue", "start", "status", "add", "ingest", "save", "saved"):
            text = (ROOT / "plugin" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("koro:learn", text)
            self.assertIn("disable-model-invocation: true", text)


if __name__ == "__main__":
    unittest.main()
