"""Complete ingest: the PDF, section texts and figures go to private storage; figures come back."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from koro import cli, figures, pdf
from koro.client import Client

from .fakes import BOOK_PAGES, BOOK_TOC, E, FIGURE, L, PNG, FakeStorage, Recorder, make_pdf


class FigureDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = make_pdf(os.path.join(self.tmp.name, "book.pdf"), BOOK_PAGES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_caption_and_graphic_on_a_shifted_page_box(self):
        found = figures.detect_figures(self.pdf)
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["label"], f["page"]), ("1.1", 2))
        self.assertEqual(f["caption"], "A blue box shows the idea.")
        x0, top, x1, bottom = f["bbox"]
        # The box is drawn at (128, 328)-(328, 478) on a page whose box starts at (28, 28):
        # page-relative that is x 100-300, top 150-300, plus the caption below and 4pt padding.
        self.assertAlmostEqual(x0, 96, delta=1)
        self.assertAlmostEqual(top, 146, delta=1)
        self.assertAlmostEqual(x1, 304, delta=1)
        self.assertTrue(300 < bottom < 335)

    def test_render_crops_the_figure(self):
        from PIL import Image

        f = figures.detect_figures(self.pdf)[0]
        png, w, h = figures.render(self.pdf, f["page"], f["bbox"], dpi=144)
        self.assertTrue(png.startswith(b"\x89PNG"))
        im = Image.open(io.BytesIO(png)).convert("RGB")
        self.assertEqual(im.size, (w, h))
        self.assertEqual(im.getpixel((w // 2, int(h * 0.3))), (0, 0, 255))  # inside the blue box
        self.assertEqual(im.getpixel((1, 1)), (255, 255, 255))               # padding

    def test_assign_nodes_and_paths(self):
        found = figures.detect_figures(self.pdf)
        _, pages, _ = pdf.read_pdf(self.pdf)
        figures.assign_nodes(found, pages, [{"key": "1", "title": "1 Intro", "page_start": 1}])
        self.assertEqual(found[0]["node_key"], "1")
        self.assertEqual(figures.figure_path(E, "2.13"), f"{E}/figures/fig-2.13.png")
        self.assertEqual(figures.source_path(E), f"{E}/source.pdf")

    def test_caption_cleanup(self):
        self.assertEqual(figures._clean("The defi ned  fl ow of con-  tent"), "The defined flow of content")


class AssetCommandsTest(unittest.TestCase):
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
        self.pdf = make_pdf(os.path.join(self.tmp.name, "book.pdf"), BOOK_PAGES)
        self.digest = pdf.sha256_file(self.pdf)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv, recorder=None, storage=None):
        rec = recorder or Recorder({"koro_toc": BOOK_TOC})
        store = storage or FakeStorage()
        client = Client("https://example.supabase.co", "sb_secret_test", transport=rec, storage_transport=store)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), client=client)
        return code, out.getvalue(), err.getvalue(), rec, store

    def status_sequence(self):
        """Nothing stored at first; after figures are registered, they show up as not uploaded."""
        registered = []

        def asset_status(body, n):
            figs = [dict(f, asset_id=f"a{i}", bucket="koro-private", uploaded=False) for i, f in enumerate(registered)]
            return {"entity": {"id": E, "title": "Tiny Book", "source_hash": self.digest}, "source": None,
                    "sections": {"total": 1, "with_text": 0, "chars": 0}, "figures": figs, "figures_uploaded": 0}

        def register(body, n):
            registered.extend(body["p_figures"])
            return {"registered": len(body["p_figures"]), "pending_upload": len(body["p_figures"])}

        return Recorder({"koro_toc": BOOK_TOC, "koro_asset_status": asset_status, "koro_register_figures": register,
                         "koro_save_node_sources": {"saved": 1, "unknown_keys": []}})

    def test_ingest_assets_stores_everything_privately(self):
        code, out, err, rec, store = self.run_cli("ingest", "assets", "--pdf", self.pdf, "--json",
                                                  recorder=self.status_sequence())
        self.assertEqual(code, 0, out + err)
        r = json.loads(out)
        self.assertEqual(r["source"], "uploaded")
        self.assertEqual(r["figures"], {"total": 1, "uploaded_now": 1})
        self.assertEqual(r["figures_detected"], 1)
        uploads = store.uploads()
        self.assertEqual(uploads[0][:2], (f"object/koro-private/{E}/source.pdf", "application/pdf"))
        self.assertEqual(uploads[0][2], os.path.getsize(self.pdf))
        self.assertEqual(uploads[1][:2], (f"object/koro-private/{E}/figures/fig-1.1.png", "image/png"))
        self.assertTrue(all(h.get("x-upsert") == "true" for m, p, b, h in store.calls if m == "POST"))
        src = rec.last("koro_register_source")
        self.assertEqual((src["p_sha256"], src["p_storage_path"]), (self.digest, f"{E}/source.pdf"))
        texts = rec.last("koro_save_node_sources")["p_texts"]
        self.assertIn("main idea", texts["1"])
        fig = rec.last("koro_register_figures")["p_figures"][0]
        self.assertEqual((fig["label"], fig["node_key"], fig["storage_path"]), ("1.1", "1", f"{E}/figures/fig-1.1.png"))
        up = rec.last("koro_mark_uploaded")
        self.assertEqual(up["p_asset"], "a0")
        self.assertGreater(up["p_width"], 100)
        self.assertEqual(len(up["p_sha256"]), 64)
        # the image is cached locally and the PDF path is remembered for next time
        self.assertTrue(cli.figure_cache_path(E, "1.1").exists())
        self.assertEqual(json.loads(Path(os.environ["KORO_CONFIG"]).read_text())["sources"][E], self.pdf)
        code, out, _, _, _ = self.run_cli("ingest", "assets", recorder=self.status_sequence())
        self.assertEqual(code, 0)
        self.assertIn("stored privately", out)

    def test_ingest_assets_only_uploads_what_is_missing(self):
        done = {"entity": {"id": E, "title": "Tiny Book", "source_hash": self.digest},
                "source": {"sha256": self.digest}, "sections": {"total": 1, "with_text": 1},
                "figures": [dict(FIGURE, uploaded=True)], "figures_uploaded": 1}
        code, out, _, rec, store = self.run_cli("ingest", "assets", "--pdf", self.pdf, "--json",
                                                recorder=Recorder({"koro_toc": BOOK_TOC, "koro_asset_status": done}))
        self.assertEqual(code, 0)
        self.assertEqual(store.calls, [])
        self.assertEqual(rec.names(), ["koro_library", "koro_asset_status"])
        self.assertEqual(json.loads(out)["figures"], {"total": 1, "uploaded_now": 0})

    def test_ingest_assets_refuses_a_different_file(self):
        other = {"entity": {"id": E, "title": "Tiny Book", "source_hash": "0" * 64}, "source": None,
                 "sections": {"total": 1, "with_text": 0}, "figures": []}
        code, out, err, _, store = self.run_cli("ingest", "assets", "--pdf", self.pdf,
                                                recorder=Recorder({"koro_toc": BOOK_TOC, "koro_asset_status": other}))
        self.assertEqual(code, 1)
        self.assertIn("SHA-256 differs", err)
        self.assertEqual(store.calls, [])

    def test_ingest_assets_needs_the_pdf(self):
        code, _, err, _, _ = self.run_cli("ingest", "assets")
        self.assertEqual(code, 1)
        self.assertIn("--pdf", err)

    def test_add_stores_assets_and_survives_an_asset_problem(self):
        rec = self.status_sequence()
        rec.responses["koro_create_entity"] = {"entity_id": E, "existed": False, "title": "Tiny Book"}
        code, out, err, rec, store = self.run_cli("add", self.pdf, "--json", recorder=rec)
        self.assertEqual(code, 0, out + err)
        r = json.loads(out)
        self.assertEqual(r["assets"]["figures"]["uploaded_now"], 1)
        self.assertIn("koro_build_outline", rec.names())
        mismatch = Recorder({"koro_toc": BOOK_TOC, "koro_create_entity": {"entity_id": E, "existed": True, "title": "T"},
                             "koro_asset_status": {"entity": {"id": E, "source_hash": "0" * 64}, "sections": {}, "figures": []}})
        code, out, _, _, store = self.run_cli("add", self.pdf, recorder=mismatch)
        self.assertEqual(code, 0)
        self.assertIn("Not stored:", out)
        self.assertEqual(store.calls, [])
        code, out, _, rec, store = self.run_cli("add", self.pdf, "--no-assets", "--json",
                                                recorder=Recorder({"koro_create_entity": {"entity_id": E, "existed": True, "title": "T"}}))
        self.assertNotIn("koro_asset_status", rec.names())

    def test_figure_downloads_caches_and_links(self):
        code, out, _, rec, store = self.run_cli("figure", "2.13", "--url", "--json")
        self.assertEqual(code, 0, out)
        r = json.loads(out)
        self.assertEqual(rec.last("koro_figure"), {"p_entity": E, "p_label": "2.13"})
        self.assertEqual(Path(r["path"]).read_bytes(), PNG)
        self.assertEqual(r["url"], f"https://example.supabase.co/storage/v1/object/sign/koro-private/{E}/figures/"
                                   "fig-2.13.png?token=tok")
        sign = [c for c in store.calls if c[1].startswith("object/sign/")][0]
        self.assertEqual(json.loads(sign[2]), {"expiresIn": 3600})
        # second time: served from the local copy, no download
        code, out, _, _, store2 = self.run_cli("figure", "2.13")
        self.assertEqual(store2.calls, [])
        self.assertIn("Figure 2.13 (p.65): A roofline chart.", out)

    def test_figure_falls_back_to_the_local_pdf(self):
        fig = dict(FIGURE, label="1.1", page=2, bbox=[96, 146, 304, 326], uploaded=False)
        code, out, _, _, store = self.run_cli("figure", "1.1", "--pdf", self.pdf, "--json",
                                              recorder=Recorder({"koro_figure": fig}))
        self.assertEqual(code, 0, out)
        self.assertTrue(Path(json.loads(out)["path"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(store.calls, [])

    def test_figure_not_stored_and_no_pdf_is_a_clear_error(self):
        code, _, err, _, _ = self.run_cli("figure", "2.13", recorder=Recorder({"koro_figure": dict(FIGURE, uploaded=False)}))
        self.assertEqual(code, 1)
        self.assertIn("koro ingest assets", err)

    def test_figure_open_uses_the_os_viewer(self):
        with mock.patch("subprocess.Popen") as popen, mock.patch.object(cli.sys, "platform", "linux"):
            code, out, _, _, _ = self.run_cli("figure", "2.13", "--open", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(popen.call_args[0][0][0], "xdg-open")
        self.assertTrue(json.loads(out)["opened"])

    def test_figures_and_read(self):
        code, out, _, rec, _ = self.run_cli("figures", "2.4.1")
        self.assertEqual(rec.last("koro_figures"), {"p_entity": E, "p_ref": "2.4.1"})
        self.assertIn("2.13", out)
        code, out, _, rec, _ = self.run_cli("read", "2.4.1")
        self.assertEqual(rec.last("koro_read_section"), {"p_entity": E, "p_node_key": "2.4.1"})
        self.assertIn("Stored words.", out)
        self.assertIn("koro figure 2.13 --open", out)
        empty = Recorder({"koro_toc": BOOK_TOC, "koro_read_section": {"section": {"key": "1"}, "text": None}})
        code, out, _, _, _ = self.run_cli("read", "1", "--pdf", self.pdf, "--json", recorder=empty)
        self.assertIn("main idea", json.loads(out)["text"])

    def test_lesson_shows_figures_at_their_beat(self):
        lesson = json.loads(json.dumps(Recorder().responses["koro_teach"]))
        lesson["concept"]["teaching_content"]["beats"][0]["figures"] = ["2.13"]
        lesson["figures"] = [FIGURE, dict(FIGURE, label="2.12", caption="The equation.")]
        code, out, _, _, _ = self.run_cli("teach", "2.4.1/x", recorder=Recorder({"koro_teach": lesson}))
        self.assertEqual(code, 0)
        beat = out.split("1. B1")[1].split("Figures:")[0]
        self.assertIn("Figure 2.13 (p.65): A roofline chart.", beat)
        self.assertIn("Figure 2.12", out.split("Figures:")[1])

    def test_storage_client(self):
        store = FakeStorage()
        c = Client("https://p.supabase.co/", "sb_secret_k", transport=Recorder(), storage_transport=store)
        c.upload("b", "dir/a b.png", PNG, "image/png")
        self.assertEqual(store.calls[0][1], "object/b/dir/a%20b.png")
        self.assertEqual(c.sign("b", "x.png", 60), "https://p.supabase.co/storage/v1/object/sign/b/x.png?token=tok")
        self.assertEqual(c.download("b", "x.png"), PNG)

    def test_storage_http_sends_the_secret_key(self):
        seen = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"Key": "k"}'

        def fake_urlopen(req, timeout=None):
            seen.append(req)
            return Resp()

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            Client("https://p.supabase.co", "sb_secret_k").upload("koro-private", "e/fig-1.png", PNG, "image/png")
        req = seen[0]
        self.assertEqual(req.full_url, "https://p.supabase.co/storage/v1/object/koro-private/e/fig-1.png")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Apikey"), "sb_secret_k")
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(req.get_header("X-upsert"), "true")
        self.assertEqual(req.data, PNG)


if __name__ == "__main__":
    unittest.main()
