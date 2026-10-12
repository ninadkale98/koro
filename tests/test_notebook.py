"""The notebook: a table of contents, pages addressed by number or name, edits that touch only
the part you name, and an editor for long text."""
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

TOC = {"pages": [
    {"number": "1", "depth": 0, "title": "Linux internals", "status": "inbox", "items": 0, "has_writeup": False},
    {"number": "1.1", "depth": 1, "title": "Signals", "status": "learning", "items": 2, "has_writeup": True,
     "item_list": [{"no": 4, "kind": "snippet", "title": "EINTR example"}]},
    {"number": "1.1.1", "depth": 2, "title": "sigaction & SA_RESTART", "status": "inbox", "items": 1,
     "has_writeup": False}],
    "inbox": {"items": 3}, "scope": None}
BODY = "Why I care: the PD-dump race.\n\n## EINTR\nBlocking calls return EINTR.\n\n```c\n#include <signal.h>\n```"
PAGE = {"id": "pg1", "number": "1.1", "title": "Signals", "status": "learning", "tags": ["linux"], "version": 5,
        "body": BODY, "path": [{"number": "1", "title": "Linux internals"}],
        "headings": [{"heading": "EINTR", "level": 2}],
        "subpages": [{"number": "1.1.1", "title": "sigaction & SA_RESTART", "items": 1}],
        "item_list": [{"no": 4, "kind": "snippet", "title": "EINTR example", "chars": 120}]}
SECTION = {"page": {"number": "1.1", "title": "Signals", "version": 5}, "heading": "EINTR", "level": 2,
           "text": "Blocking calls return EINTR.", "version": 5}


def answers(**over):
    base = {
        "koro_notebook": TOC,
        "koro_page": PAGE,
        "koro_page_section": SECTION,
        "koro_page_create": lambda b, n: {"id": "new", "number": "1.2", "title": b["p_title"], "version": 1,
                                          "existed": False},
        "koro_page_move": {"number": "1.1.2", "title": "ioctl & EINTR"},
        "koro_page_write": lambda b, n: {"number": "1.1", "title": "Signals", "version": 6, "changed": True},
        "koro_page_update": {"number": "1.1", "title": "Unix signals", "version": 5},
        "koro_page_history": {"number": "1.1", "title": "Signals", "version": 5,
                              "history": [{"version": 4, "chars": 80, "starts": "Why I care", "replaced_at": "2026-10-12T21:00"}]},
        "koro_page_restore": {"number": "1.1", "title": "Signals", "version": 6},
        "koro_file_items": lambda b, n: {"page": {"number": b["p_page"], "title": "pthread_kill"},
                                         "items": [{"no": int(i.lstrip("#"))} for i in b["p_items"]]},
        "koro_capture": lambda b, n: {"no": 9, "title": b["p_title"], "kind": b.get("p_kind") or "topic",
                                      "body": b.get("p_body"), "tags": [], "existed": False,
                                      "page": {"number": "1.1", "title": "Signals"} if b.get("p_page") else None},
        "koro_saved_item": {"no": 7, "title": "Unix signals", "body": "old note", "kind": "topic", "status": "inbox",
                            "tags": [], "created_at": "2026-10-12T20:48:01+00:00"},
        "koro_set_saved_text": lambda b, n: {"no": 7, "title": b["p_title"], "body": b["p_body"], "kind": "topic",
                                             "status": "inbox", "tags": []},
    }
    base.update(over)
    return Recorder(base)


def fake_editor(new_text):
    """A subprocess.run stand-in that 'edits' the temp file: records what it was shown, writes new_text
    (a function of the shown text, or a string) above the scissors line."""
    seen = {}

    def run(cmd, check=False):
        path = cmd[-1]
        with open(path, encoding="utf-8") as fh:
            seen["shown"] = fh.read()
        before, sep, after = seen["shown"].partition(cli.SCISSORS)
        text = new_text(before) if callable(new_text) else new_text
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n" + sep + after)
        return mock.Mock(returncode=0)

    return run, seen


class NotebookCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "KORO_CONFIG": os.path.join(self.tmp.name, "config.json"),
            "XDG_CACHE_HOME": os.path.join(self.tmp.name, "cache"),
            "KORO_SUPABASE_URL": "https://example.supabase.co", "KORO_SUPABASE_KEY": "sb_secret_test",
            "KORO_LEARNER_ID": L, "EDITOR": "fake-editor",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, recorder=None, stdin=None, editor=None):
        rec = recorder or answers()
        client = Client("https://example.supabase.co", "sb_secret_test", transport=rec, storage_transport=FakeStorage())
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch("sys.stdin", io.StringIO(stdin or ""))]
        if editor:
            patches.append(mock.patch("subprocess.run", editor))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = cli.main(list(argv), client=client)
        return code, out.getvalue(), err.getvalue(), rec

    # -- table of contents ------------------------------------------------------
    def test_notebook_toc(self):
        code, out, _, rec = self.run_cli("notebook")
        self.assertEqual(rec.last("koro_notebook"), {"p_learner": L})
        lines = out.splitlines()
        self.assertIn("Inbox", lines[0])
        self.assertIn("3 items", lines[0])
        self.assertTrue(any(l.startswith("1.1      ") and "  Signals" in l and "◐ learning" in l and "✎" in l
                            for l in lines), out)
        self.assertTrue(any(l.startswith("1.1.1") and "    sigaction" in l for l in lines))
        _, out, _, rec = self.run_cli("notebook", "1.1", "--items", "--all")
        self.assertEqual(rec.last("koro_notebook"), {"p_learner": L, "p_ref": "1.1", "p_items": True, "p_all": True})
        self.assertIn("#4", out)

    def test_empty_notebook_says_how_to_start(self):
        _, out, _, _ = self.run_cli("notebook", recorder=answers(koro_notebook={"pages": [], "inbox": {"items": 0}}))
        self.assertIn('koro page new "Signals"', out)

    # -- reading ----------------------------------------------------------------
    def test_read_a_page(self):
        code, out, _, rec = self.run_cli("page", "1.1")
        self.assertEqual(rec.last("koro_page"), {"p_learner": L, "p_ref": "1.1"})
        self.assertIn("1 Linux internals", out.splitlines()[0])
        self.assertIn("#include <signal.h>", out)  # code shown as written
        self.assertIn("Sections: EINTR", out)
        self.assertIn("1.1.1", out)
        self.assertIn("#4", out)
        _, out, _, rec = self.run_cli("page", "unix", "signals", "--full")  # names can be several words
        self.assertEqual(rec.last("koro_page"), {"p_learner": L, "p_ref": "unix signals", "p_full": True})
        _, out, _, _ = self.run_cli("page", "1.1", "--raw")
        self.assertEqual(out, BODY + "\n")
        _, out, _, rec = self.run_cli("page", "1.1", "--section", "EINTR")
        self.assertEqual(rec.last("koro_page_section"), {"p_learner": L, "p_ref": "1.1", "p_section": "EINTR"})
        self.assertIn("Blocking calls return EINTR.", out)

    # -- creating and organizing -----------------------------------------------
    def test_new_and_move(self):
        code, out, _, rec = self.run_cli("page", "new", "Real-time", "signals", "-p", "1.1", "--after", "1.1.1", "-t", "linux")
        self.assertEqual(rec.last("koro_page_create"), {"p_learner": L, "p_title": "Real-time signals", "p_parent": "1.1",
                                                        "p_after": "1.1.1", "p_tags": ["linux"], "p_client": "cli"})
        self.assertIn("Made page 1.2", out)
        _, out, _, rec = self.run_cli("page", "move", "1.2", "-p", "1.1")
        self.assertEqual(rec.last("koro_page_move"), {"p_learner": L, "p_ref": "1.2", "p_parent": "1.1"})
        self.assertIn("now 1.1.2", out)
        _, _, _, rec = self.run_cli("page", "move", "1.1.2", "--top")
        self.assertEqual(rec.last("koro_page_move")["p_top"], True)
        code, _, err, _ = self.run_cli("page", "new")
        self.assertEqual(code, 1)
        self.assertIn("koro page new", err)

    def test_new_page_with_a_first_write_up(self):
        run, seen = fake_editor("Why I care: the race.\n\n## EINTR\nnotes")
        _, out, _, rec = self.run_cli("page", "new", "Signals", "--edit", editor=run)
        self.assertEqual(rec.last("koro_page_write"), {"p_learner": L, "p_ref": "new", "p_op": "replace",
                                                       "p_text": "Why I care: the race.\n\n## EINTR\nnotes",
                                                       "p_if_version": 1, "p_client": "cli"})
        _, _, _, rec = self.run_cli("page", "new", "Signals", "--write", "-", stdin="from stdin")
        self.assertEqual(rec.last("koro_page_create")["p_body"], "from stdin")

    def test_rename_status_tags_history_restore(self):
        _, out, _, rec = self.run_cli("page", "1.1", "--rename", "Unix signals", "--status", "learning", "-t", "fastrpc",
                                      "--untag", "linux")
        self.assertEqual(rec.last("koro_page_update"), {"p_learner": L, "p_ref": "1.1", "p_title": "Unix signals",
                                                        "p_status": "learning", "p_add_tags": ["fastrpc"],
                                                        "p_remove_tags": ["linux"]})
        self.assertNotIn("koro_page_write", rec.names())
        _, out, _, rec = self.run_cli("page", "1.1", "--history")
        self.assertIn("v4", out)
        _, out, _, rec = self.run_cli("page", "1.1", "--restore", "4")
        self.assertEqual(rec.last("koro_page_restore"), {"p_learner": L, "p_ref": "1.1", "p_version": 4, "p_client": "cli"})

    def test_file_items(self):
        _, out, _, rec = self.run_cli("file", "3", "#4", "1.1.2")
        self.assertEqual(rec.last("koro_file_items"), {"p_learner": L, "p_items": ["3", "#4"], "p_page": "1.1.2"})
        self.assertIn("Moved #3, #4 to page 1.1.2", out)
        code, _, err, _ = self.run_cli("file", "3")
        self.assertIn("koro file", err)

    # -- writing only the part you name -------------------------------------------
    def test_small_edits(self):
        _, out, _, rec = self.run_cli("page", "1.1", "--append", "one more paragraph")
        self.assertEqual(rec.last("koro_page_write"), {"p_learner": L, "p_ref": "1.1", "p_op": "append",
                                                       "p_text": "one more paragraph", "p_client": "cli"})
        self.assertIn("version 6", out)
        _, _, _, rec = self.run_cli("page", "1.1", "--append", "-", "--under", "EINTR", stdin="while (...) {}\n")
        body = rec.last("koro_page_write")
        self.assertEqual((body["p_op"], body["p_section"], body["p_text"]), ("append", "EINTR", "while (...) {}\n"))
        _, _, _, rec = self.run_cli("page", "1.1", "--replace", "any thread", "--with", "any one thread", "--if-version", "5")
        body = rec.last("koro_page_write")
        self.assertEqual((body["p_op"], body["p_find"], body["p_text"], body["p_if_version"]),
                         ("replace_text", "any thread", "any one thread", 5))
        _, _, _, rec = self.run_cli("page", "1.1", "--section", "EINTR", "--with", "new text")
        body = rec.last("koro_page_write")
        self.assertEqual((body["p_op"], body["p_section"], body["p_text"]), ("replace_section", "EINTR", "new text"))
        _, _, _, rec = self.run_cli("page", "1.1", "--write", "-", stdin="everything new")
        self.assertEqual(rec.last("koro_page_write")["p_op"], "replace")
        code, _, err, _ = self.run_cli("page", "1.1", "--replace", "x")
        self.assertIn("--with", err)

    def test_edit_whole_page_in_editor_with_version_guard(self):
        run, seen = fake_editor(lambda shown: shown.replace("return EINTR", "fail with EINTR").rstrip("\n"))
        _, out, _, rec = self.run_cli("page", "1.1", "--edit", editor=run)
        self.assertTrue(seen["shown"].startswith(BODY))
        self.assertIn("version 5", seen["shown"])  # the help below the scissors line
        body = rec.last("koro_page_write")
        self.assertEqual((body["p_op"], body["p_if_version"]), ("replace", 5))
        self.assertIn("fail with EINTR", body["p_text"])
        self.assertIn("#include <signal.h>", body["p_text"])  # '#' lines in code survive
        self.assertNotIn(cli.SCISSORS, body["p_text"])

    def test_edit_one_section_in_editor(self):
        run, seen = fake_editor("Blocking calls fail with EINTR unless SA_RESTART is set.")
        _, _, _, rec = self.run_cli("page", "1.1", "--edit", "--section", "eintr", editor=run)
        self.assertTrue(seen["shown"].startswith("Blocking calls return EINTR."))
        body = rec.last("koro_page_write")
        self.assertEqual((body["p_op"], body["p_section"], body["p_if_version"]), ("replace_section", "EINTR", 5))

    def test_editor_unchanged_or_emptied_changes_nothing(self):
        run, _ = fake_editor(lambda shown: shown.rstrip("\n"))
        _, out, _, rec = self.run_cli("page", "1.1", "--edit", editor=run)
        self.assertNotIn("koro_page_write", rec.names())
        self.assertIn("No changes", out)
        run, _ = fake_editor("")
        code, _, err, rec = self.run_cli("page", "1.1", "--edit", editor=run)
        self.assertNotIn("koro_page_write", rec.names())
        self.assertIn("--write", err)

    # -- saving onto pages, long text in an editor --------------------------------
    def test_save_onto_a_page(self):
        _, out, _, rec = self.run_cli("save", "pthread_kill targets one thread", "-p", "1.1.2")
        self.assertEqual(rec.last("koro_capture")["p_page"], "1.1.2")
        self.assertIn("on page 1.1 Signals", out)
        _, _, _, rec = self.run_cli("save", "a topic", "-n", "-", stdin="a long note\nwith lines")
        self.assertEqual(rec.last("koro_capture")["p_body"], "a long note\nwith lines")

    def test_save_with_editor(self):
        run, seen = fake_editor("EINTR retry loop\n\nWhy: ioctl returns early.\n\n```c\n#define RETRY 1\n```")
        _, out, _, rec = self.run_cli("save", "--edit", "-p", "signals", editor=run)
        body = rec.last("koro_capture")
        self.assertEqual(body["p_title"], "EINTR retry loop")
        self.assertTrue(body["p_body"].startswith("Why: ioctl returns early."))
        self.assertIn("#define RETRY 1", body["p_body"])
        self.assertEqual(body["p_kind"], "note")
        run, _ = fake_editor("")
        _, out, _, rec = self.run_cli("save", "--edit", editor=run)
        self.assertNotIn("koro_capture", rec.names())
        self.assertIn("Nothing saved", out)

    def test_edit_a_saved_item_in_editor(self):
        run, seen = fake_editor("Unix signals, in depth\n\nnew longer note")
        _, out, _, rec = self.run_cli("saved", "edit", "7", "--edit", editor=run)
        self.assertTrue(seen["shown"].startswith("Unix signals\n\nold note"))
        self.assertEqual(rec.last("koro_set_saved_text"), {"p_learner": L, "p_ref": "7",
                                                           "p_title": "Unix signals, in depth", "p_body": "new longer note"})

    def test_render_page_without_write_up(self):
        text = render.page({**PAGE, "body": None, "headings": [], "subpages": [], "item_list": []})
        self.assertIn("--edit", text)


if __name__ == "__main__":
    unittest.main()
