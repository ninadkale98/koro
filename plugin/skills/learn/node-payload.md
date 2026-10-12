# Node payload: how a section of a source becomes teachable

Ingest works one outline node at a time. The coding agent (Claude Code, Codex, ...) reads
the node's text, writes one JSON payload, checks it with `koro ingest check`, and saves it
with `koro ingest save`. The database never stores the source's raw text: everything here
is written in the agent's own words, plus page numbers that point back to the original.

## Shape

```json
{
  "summary": "2-4 sentences on what this node covers, in your own words.",
  "teachable": true,
  "concepts": [
    {
      "key": "kv-cache-reuse",
      "name": "KV cache reuse across requests",
      "summary": "One sentence: what the concept is.",
      "modality": "read",
      "objectives": [
        {"key": "explain-reuse", "description": "Explain why a shared prompt prefix lets requests skip prefill work"},
        {"key": "estimate-savings", "description": "Estimate the time saved for a given shared-prefix length"}
      ],
      "teaching_content": {
        "hook": "1-2 sentences: the question this concept answers and why it matters.",
        "beats": [
          {
            "title": "Short beat title",
            "explain": "3-6 sentences, concrete, in your own words.",
            "diagram": "optional plain-text diagram, <= 14 lines, <= 72 chars wide",
            "example": "optional worked example, with the source's numbers where it has them"
          }
        ],
        "checks": [
          {
            "objective": "explain-reuse",
            "kind": "explain",
            "question": "A question that makes the learner reason, not recite.",
            "answer_guide": "What a strong answer must contain, and the common wrong answer."
          }
        ],
        "exercise": {
          "prompt": "Only for modality code/exercise: a small hands-on task.",
          "language": "python",
          "starter": "optional short starter code",
          "solution_sketch": "How a correct solution works"
        },
        "takeaway": "One sentence to remember.",
        "source_pages": [138, 140]
      },
      "depends_on": ["5.3/prefix-caching", {"key": "2.2.3/attention-kv", "confidence": 0.6}]
    }
  ]
}
```

## Rules

- **Own words only.** Never copy sentences from the source. No run of 10 or more words may
  match the source text (`koro ingest check` rejects it). Facts, numbers, names, and
  standard technical terms are fine.
- **Concepts**: 1-4 per node, each one idea a learner can be checked on. A parent node whose
  own text is only a lead-in to its subsections gets 0-1 concepts; don't repeat what a
  child node teaches. Non-teachable nodes (preface, glossary, reading lists,
  acknowledgements) get `"teachable": false`, a summary, and no concepts.
- **Keys**: lowercase kebab-case slugs, unique within the node. The stored key becomes
  `<node key>/<slug>`, e.g. `5.3.1/kv-cache-reuse`. Keep slugs stable: re-ingesting with the
  same slug updates the concept and keeps the learner's history.
- **Objectives**: 1-3 per concept, verb-led and testable ("Explain why...", "Calculate...",
  "Choose between... given..."). Every objective needs at least one check.
- **Beats**: 2-4 per concept, ordered from intuition to detail. Use a diagram when the idea
  is spatial (memory hierarchy, pipeline stages, request flow).
- **Checks**: questions that require reasoning: predict what happens, compare two options,
  calculate with given numbers, apply to a scenario. `answer_guide` says what a correct
  answer contains so the teacher can judge free-form answers.
- **Modality**: `read` (default), `code` (the learner should write or read code), or
  `exercise` (a hands-on task, e.g. a GPU kernel or a benchmark). `code`/`exercise`
  concepts include an `exercise`.
- **depends_on**: real prerequisites only, by full key (`<node key>/<slug>`) of a concept
  that comes earlier, or a bare slug for one in the same node. Optional confidence (0-1,
  default 0.8). Cross-chapter links can be added later with `koro ingest link`.
- **Accuracy**: teach what the source says. Small clarifying context is fine; never invent
  numbers.
