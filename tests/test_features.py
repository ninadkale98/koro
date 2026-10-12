"""koro feature / koro features: capture things to build from any tab, then build them later."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from koro import cli, render
from koro.client import Client

from .fakes import L, FakeStorage, Recorder

FEATURE = {"no": 3, "ref": "F3", "id": "f3", "project": "koro", "title": "Save feature requests from any tab",
           "status": "idea", "priority": "normal", "tags": [], "context": {"client": "cli", "device": "lab"},
           "created_at": "2026-10-12T21:00:00+00:00"}


def feature(**kw):
    return dict(FEATURE, **kw)


def answers(**over):
    base = {
        "koro_add_feature": lambda body, n: feature(title=body["p_title"], body=body.get("p_body"),
                                                    project=body.get("p_project"), existed=False,
                                                    priority=body.get("p_priority") or "normal"),
        "koro_features": {"items": [feature(status="building", priority="high"),
                                    feature(no=5, title="Dark mode", project="code-praxis"),
                                    feature(no=6, title="Loose idea", project=None)],
                          "counts": {"idea": 2, "planned": 0, "building": 1, "done": 4, "dropped": 0},
                          "projects": [{"project": "koro", "open": 1, "done": 4}], "filter": {"status": "open"}},
        "koro_feature": feature(body="so I can build them at night"),
        "koro_update_feature": lambda body, n: feature(status=body.get("p_status") or "idea",
                                                       done_note=body.get("p_done_note")),
    }
    base.update(over)
    return Recorder(base)


class FeaturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "KORO_CONFIG": os.path.join(self.tmp.name, "config.json"),
            "XDG_CACHE_HOME": os.path.join(self.tmp.name, "cache"),
            "KORO_SUPABASE_URL": "https://example.supabase.co",
            "KORO_SUPABASE_KEY": "sb_secret_test",
            "KORO_LEARNER_ID": L,
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, recorder=None, stdin=None):
        rec = recorder or answers()
        client = Client("https://example.supabase.co", "sb_secret_test", transport=rec, storage_transport=FakeStorage())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin or "")):
            code = cli.main(list(argv), client=client)
        return code, out.getvalue(), err.getvalue(), rec

    def test_add_a_feature(self):
        code, out, err, rec = self.run_cli("feature", "save", "feature", "requests", "from", "any", "tab", "-P", "koro",
                                           "-n", "so I can build them at night", "-t", "cli", "--priority", "high")
        self.assertEqual(code, 0, err)
        body = rec.last("koro_add_feature")
        self.assertEqual(body["p_title"], "save feature requests from any tab")
        self.assertEqual((body["p_project"], body["p_priority"], body["p_tags"]), ("koro", "high", ["cli"]))
        self.assertEqual(body["p_body"], "so I can build them at night")
        self.assertNotIn("repo", body["p_context"])  # repo/folder only with --here
        self.assertIn("Added F3 to koro: save feature requests from any tab  (high priority)", out)

    def test_project_dot_is_the_repo_you_are_in(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout="/home/me/rterm\n")):
            code, out, err, rec = self.run_cli("feature", "resize events", "-P", ".")
        self.assertEqual(rec.last("koro_add_feature")["p_project"], "rterm")
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=128, stdout="")):
            code, out, err, rec = self.run_cli("feature", "x", "-P", ".")
        self.assertEqual(code, 1)
        self.assertIn("isn't in one", err)

    def test_details_from_stdin(self):
        code, out, err, rec = self.run_cli("feature", "-", "-P", "koro",
                                           stdin="Tonight's build prompt\n\nList open features, suggest an order.\n")
        body = rec.last("koro_add_feature")
        self.assertEqual(body["p_title"], "Tonight's build prompt")
        self.assertEqual(body["p_body"], "List open features, suggest an order.")
        code, out, err, rec = self.run_cli("feature", "Big spec", "-n", "-", stdin="## Why\nbecause\n")
        self.assertEqual(rec.last("koro_add_feature")["p_body"], "## Why\nbecause\n")
        code, out, err, rec = self.run_cli("feature", "-", "-n", "-", stdin="x")
        self.assertEqual(code, 1)
        code, out, err, rec = self.run_cli("feature")
        self.assertEqual(code, 1)
        self.assertIn("what should koro build", err)

    def test_long_request_keeps_whole_text_as_details(self):
        text = "make the notebook pages exportable as markdown files " * 6
        code, out, err, rec = self.run_cli("feature", text)
        body = rec.last("koro_add_feature")
        self.assertTrue(body["p_title"].endswith("…"))
        self.assertEqual(body["p_body"], text.strip())

    def test_edit_in_editor(self):
        with mock.patch.object(cli, "open_in_editor", return_value="Export pages\n\nAs .md files, one per page."):
            code, out, err, rec = self.run_cli("feature", "--edit", "-P", "koro")
        body = rec.last("koro_add_feature")
        self.assertEqual((body["p_title"], body["p_body"]), ("Export pages", "As .md files, one per page."))
        with mock.patch.object(cli, "open_in_editor", return_value=None):
            code, out, err, rec = self.run_cli("feature", "--edit")
        self.assertEqual(rec.names(), [])
        self.assertIn("Nothing saved.", out)

    def test_list_grouped_by_project(self):
        code, out, err, rec = self.run_cli("features")
        self.assertEqual(rec.last("koro_features"), {"p_learner": L, "p_status": "open", "p_limit": 100})
        self.assertIn("Features: 1 building, 0 planned, 2 ideas, 4 done", out)
        self.assertIn("koro:\n▶ F3", out)
        self.assertIn("code-praxis:", out)
        self.assertIn("no project:", out)
        self.assertIn(" !", out)  # high priority marker
        code, out, err, rec = self.run_cli("features", "-P", "koro", "--search", "tab", "--all")
        self.assertEqual(rec.last("koro_features"), {"p_learner": L, "p_status": "all", "p_project": "koro",
                                                     "p_query": "tab", "p_limit": 100})
        empty = answers(koro_features={"items": [], "counts": {}, "filter": {"status": "open"}})
        code, out, err, rec = self.run_cli("features", recorder=empty)
        self.assertIn('koro feature "<what to build>"', out)

    def test_show_and_move_along(self):
        code, out, err, rec = self.run_cli("features", "F3")
        self.assertEqual(rec.last("koro_feature")["p_ref"], "F3")
        self.assertIn("so I can build them at night", out)
        code, out, err, rec = self.run_cli("features", "building", "F3", "4")
        self.assertEqual([c[1]["p_ref"] for c in rec.calls], ["F3", "4"])
        self.assertTrue(all(c[1]["p_status"] == "building" for c in rec.calls))
        code, out, err, rec = self.run_cli("features", "done", "F3", "-n", "shipped in 0.5.0")
        body = rec.last("koro_update_feature")
        self.assertEqual((body["p_status"], body["p_done_note"]), ("done", "shipped in 0.5.0"))
        self.assertNotIn("p_note", body)
        self.assertIn("shipped: shipped in 0.5.0", out)
        for action, status in (("drop", "dropped"), ("reopen", "idea"), ("planned", "planned")):
            code, out, err, rec = self.run_cli("features", action, "3")
            self.assertEqual(rec.last("koro_update_feature")["p_status"], status)
        code, out, err, rec = self.run_cli("features", "ship", "3")
        self.assertEqual(code, 1)
        self.assertIn("unknown action", err)
        code, out, err, rec = self.run_cli("features", "done")
        self.assertIn("which feature", err)

    def test_edit_fields(self):
        code, out, err, rec = self.run_cli("features", "edit", "F3", "-n", "one more detail", "--priority", "high",
                                           "-P", "rterm", "-t", "x", "--untag", "y", "--title", "New title")
        body = rec.last("koro_update_feature")
        self.assertEqual(body, {"p_learner": L, "p_ref": "F3", "p_title": "New title", "p_note": "one more detail",
                                "p_project": "rterm", "p_priority": "high", "p_add_tags": ["x"],
                                "p_remove_tags": ["y"]})
        code, out, err, rec = self.run_cli("features", "edit", "F3", "--details", "-", stdin="## Spec\nall of it\n")
        self.assertEqual(rec.last("koro_update_feature")["p_body"], "## Spec\nall of it\n")
        with mock.patch.object(cli, "open_in_editor", return_value="Renamed\n\nNew details"):
            code, out, err, rec = self.run_cli("features", "edit", "F3", "--edit")
        body = rec.last("koro_update_feature")
        self.assertEqual((body["p_title"], body["p_body"]), ("Renamed", "New details"))

    def test_json_output(self):
        code, out, err, rec = self.run_cli("feature", "x", "-P", "koro", "--json")
        self.assertEqual(json.loads(out)["ref"], "F3")
        code, out, err, rec = self.run_cli("features", "--json")
        self.assertEqual(len(json.loads(out)["items"]), 3)

    def test_render_done_line(self):
        line = render.feature_line(feature(status="done", done_at="2026-10-12T22:00:00+00:00"))
        self.assertTrue(line.startswith("✓ F3"))
        self.assertIn("done today", line)


if __name__ == "__main__":
    unittest.main()
