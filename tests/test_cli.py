import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from koro import cli, config
from koro.client import Client, KoroError, auth_headers

from .fakes import E, L, Recorder

PAYLOAD = {
    "summary": "This section explains why the scale of an AI product changes how you should serve its models.",
    "teachable": True,
    "concepts": [{
        "key": "scale-economics", "name": "Scale changes the economics", "summary": "One line.",
        "modality": "read",
        "objectives": [{"key": "explain", "description": "Explain why dedicated inference pays off at scale"}],
        "teaching_content": {
            "hook": "When does running your own inference beat paying per token?",
            "beats": [{"title": "Two cost curves", "explain": "Per-token APIs cost the same at every volume. "
                       "Dedicated hardware is a fixed cost that gets cheaper per token as utilization rises."},
                      {"title": "The crossover", "explain": "Once traffic is steady and high, the fixed cost is "
                       "spread over enough tokens that dedicated serving wins, and it brings control too."}],
            "checks": [{"objective": "explain", "kind": "explain", "question": "Why does utilization matter?",
                        "answer_guide": "Fixed cost divided by tokens served."}],
            "takeaway": "Scale plus steady traffic favours dedicated inference."},
        "depends_on": []}]}


class CliTest(unittest.TestCase):
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

    def run_cli(self, *argv, recorder=None):
        rec = recorder or Recorder()
        client = Client("https://example.supabase.co", "sb_secret_test", transport=rec)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), client=client)
        return code, out.getvalue(), err.getvalue(), rec

    def test_auth_headers(self):
        self.assertEqual(auth_headers("sb_secret_abc"), {"apikey": "sb_secret_abc"})
        jwt = "eyJhbGciOi.x.y"
        self.assertEqual(auth_headers(jwt)["Authorization"], f"Bearer {jwt}")

    def test_unconfigured_client(self):
        with self.assertRaises(KoroError):
            Client("", "")

    def test_rpc_prefixes_params_and_drops_none(self):
        rec = Recorder()
        Client("u", "k", transport=rec).rpc("koro_status", learner=L, entity=E, node_key=None)
        self.assertEqual(rec.calls[-1], ("koro_status", {"p_learner": L, "p_entity": E}))

    def test_init_picks_single_learner_and_saves_private_config(self):
        code, out, _, rec = self.run_cli("init", "--url", "https://x.supabase.co/", "--key", "sb_secret_k", "--json")
        self.assertEqual(code, 0)
        self.assertIn("koro_learners", [c[0] for c in rec.calls])
        path = Path(os.environ["KORO_CONFIG"])
        data = json.loads(path.read_text())
        self.assertEqual(data["learner_id"], L)
        self.assertEqual(data["supabase_url"], "https://x.supabase.co")
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")

    def test_continue(self):
        code, out, _, rec = self.run_cli("continue")
        self.assertEqual(code, 0)
        self.assertEqual(rec.last("koro_continue"), {"p_learner": L, "p_client": "cli"})
        self.assertIn("Inference Engineering", out)
        self.assertIn("next up", out)

    def test_start_at_section_resolves_entity_by_title(self):
        code, out, _, rec = self.run_cli("start", "5.3", "-e", "Inference")
        self.assertEqual(code, 0)
        self.assertEqual(rec.last("koro_resolve_entity"), {"p_ref": "Inference"})
        self.assertEqual(rec.last("koro_start"), {"p_learner": L, "p_entity": E, "p_node_key": "5.3", "p_client": "cli"})

    def test_default_entity_is_most_recent_active(self):
        self.run_cli("next")
        code, out, _, rec = self.run_cli("next", "--node", "5.3.2")
        self.assertEqual(rec.last("koro_next_concept"), {"p_learner": L, "p_entity": E, "p_node_key": "5.3.2"})
        self.assertIn("prerequisite first", out)

    def test_empty_library_is_a_clear_error(self):
        code, _, err, _ = self.run_cli("toc", recorder=Recorder({"koro_library": []}))
        self.assertEqual(code, 1)
        self.assertIn("library is empty", err)

    def test_teach_renders_lesson(self):
        code, out, _, rec = self.run_cli("teach", "0/inference-stack-layers")
        self.assertEqual(rec.last("koro_teach")["p_concept_ref"], "0/inference-stack-layers")
        self.assertIn("Why?", out)
        self.assertIn("a -> b", out)
        self.assertIn("Check yourself", out)

    def test_assess_sends_evidence(self):
        code, out, _, rec = self.run_cli("assess", "0/x", "--verdict", "partial", "--objective", "o1",
                                         "--answer", "my answer", "--misconception", "thinks Y", "--json")
        body = rec.last("koro_assess")
        self.assertEqual(body["p_verdict"], "partial")
        self.assertEqual(body["p_objective_key"], "o1")
        self.assertEqual(body["p_misconception"], "thinks Y")
        self.assertEqual(body["p_kind"], "question")
        self.assertEqual(json.loads(out)["status"], "learning")

    def test_dispute_known_status_search_toc(self):
        self.assertEqual(self.run_cli("dispute", "a1", "--verdict", "correct", "--reason", "r")[0], 0)
        code, out, _, rec = self.run_cli("known", "2.1", "--note", "work")
        self.assertEqual(rec.last("koro_mark_known"), {"p_learner": L, "p_entity": E, "p_ref": "2.1",
                                                       "p_note": "work", "p_client": "cli"})
        code, out, _, _ = self.run_cli("status")
        self.assertIn("misconception: thinks Y", out)
        code, out, _, rec = self.run_cli("search", "kv cache")
        self.assertEqual(rec.last("koro_search"), {"p_learner": L, "p_query": "kv cache"})
        code, out, _, _ = self.run_cli("toc")
        self.assertIn("— Preface", out)

    def test_habits(self):
        for cmd, status in (("pause", "paused"), ("resume", "active"), ("done", "completed"), ("drop", "dropped")):
            code, _, _, rec = self.run_cli(cmd)
            self.assertEqual(rec.last("koro_set_status")["p_status"], status)
        code, _, err, _ = self.run_cli("restart")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        code, _, _, rec = self.run_cli("restart", "--yes")
        self.assertEqual(code, 0)
        code, _, _, rec = self.run_cli("goal", "--by", "2026-12-01", "--daily", "20")
        self.assertEqual(rec.last("koro_set_goal"), {"p_learner": L, "p_entity": E, "p_target_date": "2026-12-01",
                                                     "p_daily_minutes": 20})
        code, out, _, rec = self.run_cli("time", "--days", "7")
        self.assertIn("p_since", rec.last("koro_study_time"))
        self.assertIn("goal 140 min", out)

    def test_ingest_save_validates_first(self):
        good = Path(self.tmp.name, "p.json")
        good.write_text(json.dumps(PAYLOAD))
        code, out, _, rec = self.run_cli("ingest", "save", "1.1", str(good))
        self.assertEqual(code, 0, out)
        body = rec.last("koro_save_node")
        self.assertEqual(body["p_node_key"], "1.1")
        self.assertEqual(body["p_payload"]["concepts"][0]["key"], "scale-economics")
        bad = Path(self.tmp.name, "bad.json")
        bad.write_text(json.dumps({"summary": "x", "concepts": [{"key": "Bad Key"}]}))
        code, _, err, rec = self.run_cli("ingest", "save", "1.1", str(bad))
        self.assertEqual(code, 1)
        self.assertNotIn("koro_save_node", [c[0] for c in rec.calls])

    def test_ingest_next_without_source_file(self):
        code, out, _, rec = self.run_cli("ingest", "next", "--json")
        r = json.loads(out)
        self.assertTrue(r["claimed"])
        self.assertIsNone(r["source_text"])
        self.assertIn("--pdf", r["source_text_note"])
        self.assertTrue(r["payload_spec"].endswith("node-payload.md"))
        self.assertTrue(os.path.exists(r["payload_spec"]))

    def test_ingest_release_link_finalize(self):
        links = Path(self.tmp.name, "l.json")
        links.write_text(json.dumps([{"from": "5.3/a", "to": "2.2/b", "confidence": 0.8}]))
        self.assertEqual(self.run_cli("ingest", "release", "1.1", "--error", "bad parse")[0], 0)
        code, _, _, rec = self.run_cli("ingest", "link", str(links))
        self.assertEqual(rec.last("koro_add_edge_refs")["p_refs"][0]["to"], "2.2/b")
        self.assertEqual(self.run_cli("ingest", "finalize")[0], 0)

    def test_json_error_output(self):
        def boom(fn, body):
            from koro.client import KoroError
            raise KoroError("koro_continue: permission denied")
        client = Client("u", "k", transport=boom)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["continue", "--json"], client=client)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["error"], "koro_continue: permission denied")

    def test_install_skill(self):
        target = os.path.join(self.tmp.name, "skills", "koro")
        code, out, _, _ = self.run_cli("install-skill", "--dir", target)
        self.assertEqual(code, 0)
        self.assertTrue(Path(target, "SKILL.md").read_text().startswith("---"))
        self.assertTrue(Path(target, "node-payload.md").exists())


if __name__ == "__main__":
    unittest.main()
