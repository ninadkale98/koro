"""Keep the agent instructions in one place.

koro/data/koro-skill.md is the source of truth. This writes:
  plugin/skills/learn/SKILL.md       (Claude Code plugin skill, invoked as /koro:learn)
  plugin/skills/learn/node-payload.md
  koro/data/node-payload.md           (from docs/node-payload.md)
  AGENTS.md                           (Codex and other agents)
Run it after editing either source file; tests fail if the copies drift.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def frontmatter_split(text: str) -> tuple[str, str]:
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    return text[: end + 5], text[end + 5:]


def render() -> dict:
    skill = (ROOT / "koro/data/koro-skill.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs/node-payload.md").read_text(encoding="utf-8")
    _, body = frontmatter_split(skill)
    agents_body = body.replace("Arguments: $ARGUMENTS\n\n", "")
    agents = (
        "# AGENTS.md: teaching with koro\n\n"
        "These are the instructions for any coding agent (Codex, Claude Code, Cursor, ...) that can run shell\n"
        "commands. Claude Code users can install the plugin instead (see README). The user's request takes\n"
        "the place of the slash-command arguments below; the payload spec for parsing is in\n"
        "docs/node-payload.md.\n"
        + "\n" + agents_body.replace("# Koro: teaching sessions\n", "", 1).lstrip("\n")
    )
    return {
        "plugin/skills/learn/SKILL.md": skill.replace("name: koro\n", "name: learn\n", 1),
        "plugin/skills/learn/node-payload.md": spec,
        "koro/data/node-payload.md": spec,
        "AGENTS.md": agents,
    }


if __name__ == "__main__":
    for rel, content in render().items():
        (ROOT / rel).write_text(content, encoding="utf-8")
        print("wrote", rel)
