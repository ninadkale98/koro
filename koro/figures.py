"""Find figures in a PDF and render them as images.

A figure is anchored on its caption line ("Figure 2.13: ..."). Its region is the
union of the graphics (raster images, vector shapes) between the previous caption
on the page and this caption (or, if there are none above, directly below it),
plus the caption itself. Rendering uses pypdfium2 (installed with pdfplumber).
"""
from __future__ import annotations

import io
import re
from typing import Optional

CAPTION = re.compile(r"^\s*(?:Figure|Fig\.)\s*(\d+(?:\s*\.\s*\d+)*)\s*[:.]\s*(.*)$")
LIGATURE = re.compile(r"(f|fi|fl|ff)\s{2}(?=[a-z])")
MIN_GRAPHIC = 2.0       # points; ignore hairline specks
MIN_FIGURE_AREA = 900   # points^2 (~0.4 in^2); smaller regions are not figures


def _clean(text: str) -> str:
    text = LIGATURE.sub(r"\1", text)
    # Ligature glyphs often extract as "fi t" / "defi ned"; "fi"/"fl" are not words on their own.
    text = re.sub(r"(fi|fl) (?=[a-z])", r"\1", text)
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)  # undo line-break hyphenation
    return re.sub(r"\s+", " ", text).strip()


def _offset(page) -> tuple[float, float]:
    """pdfplumber reports absolute coordinates; renders start at the page box origin."""
    return page.bbox[0], page.bbox[1]


def _graphics(page) -> list[tuple[float, float, float, float]]:
    ox, oy = _offset(page)
    boxes = []
    for obj in list(page.images) + list(page.rects) + list(page.lines) + list(page.curves):
        x0, top, x1, bottom = obj["x0"] - ox, obj["top"] - oy, obj["x1"] - ox, obj["bottom"] - oy
        if (x1 - x0) < MIN_GRAPHIC and (bottom - top) < MIN_GRAPHIC:
            continue
        # Skip full-page backgrounds.
        if (x1 - x0) > page.width * 0.98 and (bottom - top) > page.height * 0.98:
            continue
        boxes.append((max(0, x0), max(0, top), min(page.width, x1), min(page.height, bottom)))
    return boxes


def _caption_blocks(lines: list[dict]) -> list[dict]:
    """Caption line plus the lines that continue it (same block, no big gap)."""
    blocks = []
    lines = sorted(lines, key=lambda l: (round(l["top"]), l["x0"]))
    i = 0
    while i < len(lines):
        m = CAPTION.match(lines[i]["text"])
        if not m:
            i += 1
            continue
        first = lines[i]
        height = max(6.0, first["bottom"] - first["top"])
        text = [first["text"]]
        x0, top, x1, bottom = first["x0"], first["top"], first["x1"], first["bottom"]
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if CAPTION.match(nxt["text"]) or nxt["top"] - bottom > height * 0.8:
                break
            if abs(nxt["x0"] - x0) > 40:  # continuation lines start at the caption's left edge
                break
            text.append(nxt["text"])
            x0, x1, bottom = min(x0, nxt["x0"]), max(x1, nxt["x1"]), max(bottom, nxt["bottom"])
            j += 1
        caption = _clean(" ".join(text))
        cm = CAPTION.match(caption)
        blocks.append({"label": re.sub(r"\s+", "", m.group(1)), "caption": cm.group(2) if cm else caption,
                       "bbox": (x0, top, x1, bottom)})
        i = j
    return blocks


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def detect_figures(path: str) -> list[dict]:
    """[{label, caption, page, bbox: [x0, top, x1, bottom] in PDF points}] in reading order."""
    import pdfplumber

    found: list[dict] = []
    seen: set = set()
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            try:
                ox, oy = _offset(page)
                lines = [dict(l, x0=l["x0"] - ox, x1=l["x1"] - ox, top=l["top"] - oy, bottom=l["bottom"] - oy)
                         for l in page.extract_text_lines(return_chars=False)]
            except Exception:
                continue
            blocks = _caption_blocks(lines)
            if not blocks:
                continue
            graphics = _graphics(page)
            prev_bottom = 0.0
            for k, b in enumerate(blocks):
                cx0, ctop, cx1, cbottom = b["bbox"]
                above = [g for g in graphics if g[3] <= ctop + 3 and g[1] >= prev_bottom - 1]
                region = above
                if not region:
                    nxt_top = blocks[k + 1]["bbox"][1] if k + 1 < len(blocks) else page.height
                    # Graphics below the caption, up to the next text line that isn't inside them.
                    region = [g for g in graphics if g[1] >= cbottom - 3 and g[3] <= nxt_top + 1]
                if not region:
                    prev_bottom = cbottom
                    continue
                gx0, gtop, gx1, gbottom = _union(region)
                if (gx1 - gx0) * (gbottom - gtop) < MIN_FIGURE_AREA:
                    prev_bottom = cbottom
                    continue
                # Text labels inside the region (vector figures) are rendered with the crop.
                x0, top = min(gx0, cx0), min(gtop, ctop)
                x1, bottom = max(gx1, cx1), max(gbottom, cbottom)
                pad = 4.0
                bbox = [round(max(0, x0 - pad), 1), round(max(0, top - pad), 1),
                        round(min(page.width, x1 + pad), 1), round(min(page.height, bottom + pad), 1)]
                label = b["label"]
                if label in seen:  # a repeated caption (e.g. a figure continued): keep the first
                    prev_bottom = cbottom
                    continue
                seen.add(label)
                found.append({"label": label, "caption": b["caption"], "page": pno, "bbox": bbox})
                prev_bottom = cbottom
    return found


def assign_nodes(figures: list[dict], pages: list[str], nodes: list[dict]) -> list[dict]:
    """Attach each figure to the outline node whose text contains its caption."""
    from .pdf import _locate

    full, page_at, offsets = _locate(pages, nodes)
    order = sorted(range(len(nodes)), key=lambda i: offsets[i])
    norm = LIGATURE.sub(r"\1", full)
    for f in figures:
        parts = [re.escape(p) for p in f["label"].split(".")]
        pat = re.compile(r"(?:Figure|Fig\.)\s*" + r"\s*\.\s*".join(parts) + r"\s*[:.]")
        page_start = page_at[min(len(page_at) - 1, f["page"] - 1)] if page_at else 0
        m = pat.search(norm, max(0, page_start - 200)) or pat.search(norm)
        pos = m.start() if m else page_start
        key = None
        for i in order:
            if offsets[i] <= pos:
                key = nodes[i]["key"]
            else:
                break
        f["node_key"] = key
    return figures


def render(path: str, page: int, bbox, dpi: int = 200) -> tuple[bytes, int, int]:
    """PNG bytes, width, height for a region of a page (bbox in PDF points, top-left origin)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        pg = doc[page - 1]
        scale = dpi / 72.0
        image = pg.render(scale=scale).to_pil()
        x0, top, x1, bottom = bbox
        crop = image.crop((int(x0 * scale), int(top * scale), int(x1 * scale), int(bottom * scale)))
        if crop.mode not in ("RGB", "L"):
            crop = crop.convert("RGB")
        buf = io.BytesIO()
        crop.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), crop.width, crop.height
    finally:
        doc.close()


def figure_path(entity_id: str, label: str) -> str:
    return f"{entity_id}/figures/fig-{re.sub(r'[^0-9A-Za-z.]+', '-', label)}.png"


def source_path(entity_id: str, filename: Optional[str] = None) -> str:
    return f"{entity_id}/source.pdf"
