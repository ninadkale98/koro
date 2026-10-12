"""koro guide: the manual agents read. It must cover every command and match the CLI."""
import contextlib
import io
import json
import re
import unittest

from koro import cli


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv), client=object())  # the guide never touches the database
    return code, out.getvalue(), err.getvalue()


class GuideTest(unittest.TestCase):
    def test_topics_listed_in_the_intro_all_exist(self):
        intro, sections = cli.guide_sections()
        topics = [t for t, _, _ in sections]
        listed = re.search(r"Topics \(`koro guide <topic>`\): (.*?)\.", intro, re.S).group(1)
        self.assertEqual([t.strip() for t in listed.replace("\n", " ").split(",")], topics)

    def test_every_command_is_covered_by_the_guide(self):
        text = (cli.DATA / "guide.md").read_text(encoding="utf-8")
        missing = [c["command"] for c in cli.command_catalog()
                   if not re.search(rf"(koro |`|/ ){re.escape(c['command'].split()[1])}\b", text)]
        self.assertEqual(missing, [], "add these commands to koro/data/guide.md")

    def test_every_option_has_a_description(self):
        def walk(c):
            for a in c["args"]:
                yield c["command"], a
            for s in c.get("subcommands") or []:
                yield from walk(s)
        missing = [(cmd, a.get("flags") or a.get("name")) for c in cli.command_catalog() for cmd, a in walk(c)
                   if not a.get("help")]
        self.assertEqual(missing, [])

    def test_full_guide_topic_and_prefix(self):
        code, out, _ = run("guide")
        self.assertEqual(code, 0)
        self.assertIn("## notebook", out)
        self.assertIn("koro page 1.1 --append", out)
        self.assertIn("koro saved", out)                 # generated catalog at the end
        self.assertIn('"concepts"', out)                 # the payload spec is included
        _, out, _ = run("guide", "notebook")
        self.assertTrue(out.startswith("## notebook"))
        self.assertNotIn("## save", out)
        _, out, _ = run("guide", "comm")                 # a prefix is enough
        self.assertTrue(out.startswith("## commands"))
        self.assertIn("koro page  —", out)
        self.assertIn("--if-version", out)
        code, _, err = run("guide", "nope")
        self.assertEqual(code, 1)
        self.assertIn("Topics:", err)

    def test_json_has_topics_text_and_command_catalog(self):
        _, out, _ = run("guide", "--json")
        r = json.loads(out)
        self.assertIn("notebook", r["topics"])
        names = [c["command"] for c in r["commands"]]
        for name in ("koro save", "koro notebook", "koro page", "koro file", "koro teach", "koro guide"):
            self.assertIn(name, names)
        page = next(c for c in r["commands"] if c["command"] == "koro page")
        self.assertIn("--append", [f for a in page["args"] for f in a.get("flags") or []])
        self.assertTrue(page["examples"])
        ingest = next(c for c in r["commands"] if c["command"] == "koro ingest")
        self.assertIn("koro ingest next", [s["command"] for s in ingest["subcommands"]])
        _, out, _ = run("guide", "save", "--json")
        self.assertEqual(json.loads(out)["topic"], "save")
        _, out, _ = run("guide", "--list", "--json")
        self.assertIn("teach", json.loads(out)["topics"])


if __name__ == "__main__":
    unittest.main()
