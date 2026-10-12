import json
import unittest

from koro import pdf
from koro.payload import validate

from .test_cli import PAYLOAD


class OutlineTest(unittest.TestCase):
    RAW = [pdf.RawItem(0, "Table of Contents", 3),
           pdf.RawItem(1, "Preface", 5),
           pdf.RawItem(1, "Chapter 1: Basics", 8),
           pdf.RawItem(2, "1.1 Tokens", 9),
           pdf.RawItem(3, "1.1.1 Tokenizers", 10),
           pdf.RawItem(2, "1.2 Attention", 12),
           pdf.RawItem(1, "Appendix A: Glossary", 20),
           pdf.RawItem(1, "Appendix B: Recommended Reading", 25),
           pdf.RawItem(2, "Architecture", 26)]

    def test_build_nodes(self):
        nodes = pdf.build_nodes(self.RAW, 30)
        keys = [n["key"] for n in nodes]
        self.assertEqual(keys, ["preface", "1", "1.1", "1.1.1", "1.2", "appA", "appB", "appB.architecture"])
        by = {n["key"]: n for n in nodes}
        self.assertEqual(by["1.1.1"]["parent_key"], "1.1")
        self.assertEqual(by["1.1"]["kind"], "section")
        self.assertEqual(by["1.1.1"]["kind"], "subsection")
        self.assertEqual(by["appA"]["kind"], "appendix")
        self.assertFalse(by["preface"]["teachable"])
        self.assertFalse(by["appA"]["teachable"])           # glossary
        self.assertFalse(by["appB.architecture"]["teachable"])  # child of a reading list
        self.assertTrue(by["1.2"]["teachable"])
        self.assertEqual(by["1"]["page_end"], 20)            # covers its subtree
        self.assertEqual(by["appB.architecture"]["page_end"], 30)

    def test_duplicate_titles_get_unique_keys(self):
        raw = [pdf.RawItem(0, "Intro", 1), pdf.RawItem(1, "Setup", 2), pdf.RawItem(1, "Setup", 3)]
        keys = [n["key"] for n in pdf.build_nodes(raw, 5)]
        self.assertEqual(len(keys), len(set(keys)))

    def test_no_bookmarks_falls_back_to_page_chunks(self):
        nodes = pdf.build_nodes([], 25)
        self.assertEqual([n["key"] for n in nodes], ["p001", "p011", "p021"])
        self.assertEqual(nodes[-1]["page_end"], 25)

    def test_segment_by_headings(self):
        pages = ["1 Chapter 1: Basics\nIntro text for chapter one.\n1.1 Tokens\nTokens are pieces.",
                 " 1.1 Tokens 2\nMore about tokens.\n1.2 Attention\nAttention mixes tokens."]
        nodes = [{"key": "1", "title": "Chapter 1: Basics", "page_start": 1},
                 {"key": "1.1", "title": "1.1 Tokens", "page_start": 1},
                 {"key": "1.2", "title": "1.2 Attention", "page_start": 2}]
        texts = pdf.segment(pages, nodes)
        self.assertIn("Intro text", texts["1"])
        self.assertNotIn("Tokens are pieces", texts["1"])
        self.assertIn("More about tokens", texts["1.1"])   # running header stripped, text continues
        self.assertTrue(texts["1.2"].startswith("1.2 Attention"))

    def test_canonical_ids(self):
        self.assertEqual(pdf.canonical_id(["ISBN: 979-8-9943597-2-3"], "x.pdf"), "isbn:9798994359723")
        self.assertEqual(pdf.canonical_id(["arXiv:2309.06180v1 [cs.LG]"], "x.pdf"), "arxiv:2309.06180")
        self.assertEqual(pdf.canonical_id([""], "/tmp/2401.12345v2.pdf"), "arxiv:2401.12345")
        self.assertIsNone(pdf.canonical_id(["nothing here"], "notes.pdf"))


class PayloadTest(unittest.TestCase):
    def test_valid_payload(self):
        errors, warnings = validate(PAYLOAD)
        self.assertEqual(errors, [])

    def test_structure_errors(self):
        bad = json.loads(json.dumps(PAYLOAD))
        c = bad["concepts"][0]
        c["key"] = "Not A Slug"
        c["objectives"].append({"key": "second", "description": "Calculate something"})
        c["teaching_content"]["beats"] = c["teaching_content"]["beats"][:1]
        c["modality"] = "code"
        errors, _ = validate(bad)
        joined = " | ".join(errors)
        self.assertIn("kebab-case", joined)
        self.assertIn("objectives without a check", joined)
        self.assertIn("2-4 beats", joined)
        self.assertIn("exercise.prompt", joined)

    def test_non_teachable(self):
        self.assertEqual(validate({"summary": "A glossary of about two hundred terms.", "teachable": False,
                                   "concepts": []})[0], [])
        errors, _ = validate({"summary": "A glossary of terms used in the book.", "teachable": False,
                              "concepts": PAYLOAD["concepts"]})
        self.assertTrue(any("non-teachable" in e for e in errors))

    def test_copy_check(self):
        source = ("Dedicated hardware is a fixed cost that gets cheaper per token as utilization rises "
                  "and that is the whole point of this section.")
        errors, _ = validate(PAYLOAD, source)
        self.assertTrue(any("copies the source" in e for e in errors), errors)
        errors, _ = validate(PAYLOAD, "Completely different words about GPUs and kernels and memory.")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
