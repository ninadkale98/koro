"""koro save / koro saved: capture things to learn later, list them, move them along."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from koro import cli, render
from koro.client import Client

from .fakes import L, PNG, FakeStorage, Recorder

ITEM = {"no": 7, "id": "s7", "kind": "topic", "title": "Paged attention", "tags": ["gpu"], "status": "inbox",
        "context": {"client": "cli", "device": "lab"}, "created_at": "2026-10-12T18:00:00+00:00"}


def item(**kw):
    return dict(ITEM, **kw)


def answers(**over):
    def save(body, n):
        kind = body.get("p_kind") or ("url" if body.get("p_url") else "topic")
        return item(title=body["p_title"], kind=kind, url=body.get("p_url"), body=body.get("p_body"),
                    tags=body.get("p_tags") or [], existed=False)

    base = {
        "koro_save_item": save,
        "koro_attach_saved_file": lambda body, n: item(kind="pdf", title="paper.pdf", file={
            "filename": body["p_filename"], "bucket": body["p_bucket"], "storage_path": body["p_storage_path"]}),
        "koro_saved_items": {"items": [item(), item(no=8, kind="url", title="vLLM blog", url="https://x.io/b",
                                                status="learning")],
                             "counts": {"inbox": 1, "planned": 0, "learning": 1, "done": 3, "archived": 0},
                             "tags": [{"tag": "gpu", "n": 2}], "filter": {"status": "open"}},
        "koro_saved_item": item(body="came up in the scheduler"),
        "koro_update_saved_item": lambda body, n: item(status=body.get("p_status") or "inbox"),
    }
    base.update(over)
    return Recorder(base)


class SavedTest(unittest.TestCase):
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
        self.title = mock.patch.object(cli, "page_title", return_value="Efficient Memory Management for LLM Serving")
        self.fetch = self.title.start()

    def tearDown(self):
        self.title.stop()
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, recorder=None, storage=None, stdin=None):
        rec = recorder or answers()
        store = storage or FakeStorage()
        client = Client("https://example.supabase.co", "sb_secret_test", transport=rec, storage_transport=store)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin or "")):
            code = cli.main(list(argv), client=client)
        return code, out.getvalue(), err.getvalue(), rec, store

    def test_save_a_topic_with_note_and_tags(self):
        code, out, _, rec, _ = self.run_cli("save", "paged", "attention", "internals", "-n", "saw it in vLLM",
                                            "-t", "gpu,serving", "--tag", "#kv-cache")
        self.assertEqual(code, 0, out)
        body = rec.last("koro_save_item")
        self.assertEqual(body["p_title"], "paged attention internals")
        self.assertEqual(body["p_body"], "saw it in vLLM")
        self.assertEqual(body["p_tags"], ["gpu", "serving", "#kv-cache"])
        self.assertIn("device", body["p_context"])
        self.assertNotIn("repo", body["p_context"])  # repo/folder only with --here
        self.assertEqual(body["p_client"], "cli")
        self.assertNotIn("p_kind", body)  # the database decides topic vs question
        self.assertIn("Saved #7: paged attention internals", out)

    def test_save_a_link_looks_up_its_title(self):
        code, out, _, rec, _ = self.run_cli("save", "https://arxiv.org/abs/2309.06180", "--json")
        body = rec.last("koro_save_item")
        self.assertEqual(body["p_url"], "https://arxiv.org/abs/2309.06180")
        self.assertEqual(body["p_kind"], "url")
        self.assertEqual(body["p_title"], "Efficient Memory Management for LLM Serving")
        self.fetch.reset_mock()
        code, out, _, rec, _ = self.run_cli("save", "https://x.io/paper.pdf", "--no-fetch")
        self.fetch.assert_not_called()
        self.assertEqual((rec.last("koro_save_item")["p_kind"], rec.last("koro_save_item")["p_title"]),
                         ("pdf", "https://x.io/paper.pdf"))

    def test_save_a_file_uploads_it_privately(self):
        path = os.path.join(self.tmp.name, "flash attention.pdf")
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4 tiny")
        code, out, err, rec, store = self.run_cli("save", path, "-n", "for the tiling trick", "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(rec.last("koro_save_item")["p_kind"], "pdf")
        self.assertEqual(rec.last("koro_save_item")["p_title"], "flash attention.pdf")
        (up_path, mime, size), = store.uploads()
        self.assertEqual(up_path, f"object/koro-private/saved/{L}/7-flash-attention.pdf")
        self.assertEqual((mime, size), ("application/pdf", 13))
        att = rec.last("koro_attach_saved_file")
        self.assertEqual((att["p_ref"], att["p_filename"], len(att["p_sha256"])), ("7", "flash attention.pdf", 64))

    def test_save_a_snippet_from_stdin(self):
        code, out, _, rec, _ = self.run_cli("save", "-", "-n", "why is this lock-free?",
                                            stdin="\n  atomic_fetch_add(&q->tail, 1);\n  more();\n")
        body = rec.last("koro_save_item")
        self.assertEqual(body["p_kind"], "snippet")
        self.assertEqual(body["p_title"], "atomic_fetch_add(&q->tail, 1);")
        self.assertTrue(body["p_body"].startswith("why is this lock-free?\n\n"))
        code, _, err, _, _ = self.run_cli("save", "-", stdin="   ")
        self.assertEqual(code, 1)
        self.assertIn("stdin", err)

    def test_long_thought_becomes_a_note(self):
        thought = "why " + "does the scheduler preempt sequences " * 8
        code, out, _, rec, _ = self.run_cli("save", thought)
        body = rec.last("koro_save_item")
        self.assertEqual(body["p_kind"], "note")
        self.assertTrue(body["p_title"].endswith("…") and len(body["p_title"]) <= 81)
        self.assertEqual(body["p_body"], thought.strip())

    def test_here_records_repo_and_branch(self):
        rec = answers()
        with mock.patch("subprocess.run") as run:
            run.side_effect = [mock.Mock(returncode=0, stdout="/work/dsp-kernel\n"),
                               mock.Mock(returncode=0, stdout="pddump\n")]
            self.run_cli("save", "qdi refcounting", "--here", recorder=rec)
        ctx = rec.last("koro_save_item")["p_context"]
        self.assertEqual((ctx["repo"], ctx["branch"]), ("dsp-kernel", "pddump"))
        self.assertIn("cwd", ctx)

    def test_saved_lists_open_items_with_filters(self):
        code, out, _, rec, _ = self.run_cli("saved")
        self.assertEqual(rec.last("koro_saved_items"), {"p_learner": L, "p_status": "open", "p_limit": 50})
        self.assertIn("#7", out)
        self.assertIn("1 inbox, 0 planned, 1 learning, 3 done", out)
        self.assertIn("#gpu (2)", out)
        _, _, _, rec, _ = self.run_cli("saved", "--tag", "gpu", "--search", "kv", "--all")
        self.assertEqual(rec.last("koro_saved_items"), {"p_learner": L, "p_status": "all", "p_tag": "gpu",
                                                        "p_query": "kv", "p_limit": 50})
        _, _, _, rec, _ = self.run_cli("saved", "--status", "done")
        self.assertEqual(rec.last("koro_saved_items")["p_status"], "done")

    def test_show_change_status_and_edit(self):
        code, out, _, rec, _ = self.run_cli("saved", "7")
        self.assertEqual(rec.last("koro_saved_item"), {"p_learner": L, "p_ref": "7"})
        self.assertIn("came up in the scheduler", out)
        code, out, _, rec, _ = self.run_cli("saved", "done", "7", "#8", "--json")
        calls = [c[1] for c in rec.calls if c[0] == "koro_update_saved_item"]
        self.assertEqual([(c["p_ref"], c["p_status"]) for c in calls], [("7", "done"), ("#8", "done")])
        self.assertEqual(len(json.loads(out)), 2)
        _, _, _, rec, _ = self.run_cli("saved", "edit", "7", "-n", "read section 3", "-t", "papers", "--untag", "gpu")
        body = rec.last("koro_update_saved_item")
        self.assertEqual((body["p_note"], body["p_add_tags"], body["p_remove_tags"]), ("read section 3", ["papers"], ["gpu"]))
        self.assertNotIn("p_status", body)
        _, _, _, rec, _ = self.run_cli("saved", "reopen", "7")
        self.assertEqual(rec.last("koro_update_saved_item")["p_status"], "inbox")
        code, _, err, _, _ = self.run_cli("saved", "someday", "7")
        self.assertEqual(code, 1)
        self.assertIn("unknown action", err)
        code, _, err, _, _ = self.run_cli("saved", "done")
        self.assertIn("which item", err)

    def test_open_a_saved_file_or_link(self):
        rec = answers(koro_saved_item=item(kind="pdf", file={"filename": "paper.pdf", "bucket": "koro-private",
                                                             "storage_path": f"saved/{L}/7-paper.pdf"}))
        with mock.patch("subprocess.Popen"), mock.patch.object(cli.sys, "platform", "linux"):
            code, out, _, _, store = self.run_cli("saved", "open", "7", "--json", recorder=rec)
        r = json.loads(out)
        with open(r["path"], "rb") as fh:
            self.assertEqual(fh.read(), PNG)  # FakeStorage serves these bytes for any download
        self.assertEqual(store.calls[0][:2], ("GET", f"object/koro-private/saved/{L}/7-paper.pdf"))
        rec = answers(koro_saved_item=item(kind="url", url="https://x.io/b"))
        with mock.patch("webbrowser.open", return_value=True) as wb:
            self.run_cli("saved", "open", "7", recorder=rec)
        wb.assert_called_once_with("https://x.io/b")

    def test_render_empty_list(self):
        text = render.saved_list({"items": [], "counts": {}, "filter": {"status": "open"}})
        self.assertIn("koro save", text)


class PageTitleTest(unittest.TestCase):
    def test_reads_the_title_and_survives_failures(self):
        class Resp(io.BytesIO):
            headers = mock.Mock()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        resp = Resp(b"<html><head><title>\n  PagedAttention &amp; vLLM \n</title></head></html>")
        resp.headers.get.return_value = "text/html; charset=utf-8"
        resp.headers.get_content_charset.return_value = "utf-8"
        with mock.patch("urllib.request.urlopen", return_value=resp):
            self.assertEqual(cli.page_title("https://x.io"), "PagedAttention & vLLM")
        with mock.patch("urllib.request.urlopen", side_effect=OSError("blocked")):
            self.assertIsNone(cli.page_title("https://x.io"))


if __name__ == "__main__":
    unittest.main()
