"""Validate a node payload before it is saved.

Checks the shape described in docs/node-payload.md and, when the source text is
available, rejects any run of 10+ words copied from it.

Usable as a library (``validate``) or a script:
    python -m koro.payload out/5.3.1.json --source-text section.txt
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Iterable

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FULL_KEY = re.compile(r"^[^\s/]+/[a-z0-9]+(?:-[a-z0-9]+)*$")
MODALITIES = {"read", "code", "exercise"}
CHECK_KINDS = {"explain", "predict", "calculate", "compare", "apply"}
COPY_WINDOW = 10


def _words(text: str) -> list[str]:
    text = re.sub(r"-\s*\n\s*", "", text)  # undo end-of-line hyphenation
    text = text.lower().replace("’", "'")
    return re.findall(r"[a-z0-9']+", text)


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def copied_runs(text: str, source_grams: set[tuple[str, ...]], n: int = COPY_WINDOW) -> list[str]:
    words = _words(text)
    hits = []
    for i in range(len(words) - n + 1):
        gram = tuple(words[i:i + n])
        if gram in source_grams:
            hits.append(" ".join(gram))
    return hits


def validate(payload: Any, source_text: str | None = None) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors block saving; warnings are advice."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be a JSON object"], warnings

    summary = payload.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 20:
        errors.append("summary: 2-4 sentences required")
    teachable = payload.get("teachable", True)
    if not isinstance(teachable, bool):
        errors.append("teachable must be true or false")
    concepts = payload.get("concepts", [])
    if not isinstance(concepts, list):
        return errors + ["concepts must be a list"], warnings
    if teachable is False and concepts:
        errors.append("a non-teachable node must not have concepts")
    if teachable is not False and not concepts:
        warnings.append("teachable node with no concepts (fine for a pure lead-in section)")
    if len(concepts) > 4:
        warnings.append(f"{len(concepts)} concepts; aim for 1-4 per node")

    source_grams = _ngrams(_words(source_text), COPY_WINDOW) if source_text else set()
    if source_grams:
        for hit in copied_runs(summary or "", source_grams)[:3]:
            errors.append(f"summary copies the source: \"{hit}\"")

    slugs: set[str] = set()
    for idx, c in enumerate(concepts, 1):
        where = f"concept {idx}"
        if not isinstance(c, dict):
            errors.append(f"{where}: must be an object")
            continue
        key = c.get("key", "")
        where = f"concept {key or idx}"
        if not isinstance(key, str) or not SLUG.match(key):
            errors.append(f"{where}: key must be a lowercase kebab-case slug")
        elif key in slugs:
            errors.append(f"{where}: duplicate key")
        slugs.add(key)
        for field in ("name", "summary"):
            if not isinstance(c.get(field), str) or not c[field].strip():
                errors.append(f"{where}: {field} is required")
        modality = c.get("modality", "read")
        if modality not in MODALITIES:
            errors.append(f"{where}: modality must be one of {sorted(MODALITIES)}")

        objectives = c.get("objectives", [])
        okeys: set[str] = set()
        if not isinstance(objectives, list) or not 1 <= len(objectives) <= 3:
            errors.append(f"{where}: 1-3 objectives required")
            objectives = objectives if isinstance(objectives, list) else []
        for o in objectives:
            if not isinstance(o, dict) or not SLUG.match(str(o.get("key", ""))) \
                    or not str(o.get("description", "")).strip():
                errors.append(f"{where}: each objective needs a kebab-case key and a description")
                continue
            if o["key"] in okeys:
                errors.append(f"{where}: duplicate objective key {o['key']}")
            okeys.add(o["key"])

        tc = c.get("teaching_content")
        if not isinstance(tc, dict):
            errors.append(f"{where}: teaching_content object required")
            continue
        if not str(tc.get("hook", "")).strip():
            errors.append(f"{where}: teaching_content.hook required")
        if not str(tc.get("takeaway", "")).strip():
            errors.append(f"{where}: teaching_content.takeaway required")
        beats = tc.get("beats", [])
        if not isinstance(beats, list) or not 2 <= len(beats) <= 4:
            errors.append(f"{where}: 2-4 beats required")
            beats = beats if isinstance(beats, list) else []
        for b in beats:
            if not isinstance(b, dict) or not str(b.get("title", "")).strip() \
                    or len(str(b.get("explain", "")).strip()) < 80:
                errors.append(f"{where}: each beat needs a title and a substantial explain (3-6 sentences)")
                continue
            diagram = b.get("diagram")
            if diagram:
                lines = str(diagram).splitlines()
                if len(lines) > 14 or any(len(line) > 72 for line in lines):
                    warnings.append(f"{where}: diagram in beat '{b['title']}' exceeds 14 lines or 72 columns")
        checks = tc.get("checks", [])
        covered = set()
        if not isinstance(checks, list) or not checks:
            errors.append(f"{where}: at least one check required")
            checks = []
        for ch in checks:
            if not isinstance(ch, dict) or not str(ch.get("question", "")).strip() \
                    or not str(ch.get("answer_guide", "")).strip():
                errors.append(f"{where}: each check needs a question and an answer_guide")
                continue
            if ch.get("objective") not in okeys:
                errors.append(f"{where}: check objective '{ch.get('objective')}' is not one of its objectives")
            covered.add(ch.get("objective"))
            if ch.get("kind") and ch["kind"] not in CHECK_KINDS:
                warnings.append(f"{where}: check kind '{ch['kind']}' not in {sorted(CHECK_KINDS)}")
        missing = okeys - covered
        if missing:
            errors.append(f"{where}: objectives without a check: {sorted(missing)}")
        if modality in ("code", "exercise"):
            ex = tc.get("exercise")
            if not isinstance(ex, dict) or not str(ex.get("prompt", "")).strip():
                errors.append(f"{where}: modality {modality} needs teaching_content.exercise.prompt")

        for dep in c.get("depends_on", []) or []:
            dkey = dep if isinstance(dep, str) else (dep.get("key") if isinstance(dep, dict) else None)
            if not isinstance(dkey, str) or not (SLUG.match(dkey) or FULL_KEY.match(dkey)):
                errors.append(f"{where}: depends_on entry {dep!r} must be a slug or '<node key>/<slug>'")

        if source_grams:
            text = " \n ".join(_strings({k: v for k, v in c.items() if k not in ("key", "depends_on")}))
            for hit in copied_runs(text, source_grams)[:3]:
                errors.append(f"{where}: copies the source: \"{hit}\"")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Validate a koro node payload")
    p.add_argument("payload", help="payload JSON file")
    p.add_argument("--source-text", help="plain-text file with the node's source text (enables the copy check)")
    args = p.parse_args(argv)
    try:
        payload = json.load(open(args.payload, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR cannot read payload: {exc}")
        return 2
    source = open(args.source_text, encoding="utf-8").read() if args.source_text else None
    errors, warnings = validate(payload, source)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        print(f"FAIL  {len(errors)} error(s)")
        return 1
    print(f"OK    {len(payload.get('concepts', []))} concept(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
