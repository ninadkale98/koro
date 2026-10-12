---
description: Show your koro notebook (pages, numbers, items), or open, organize and edit one page.
argument-hint: "[page number or name] [what to do]"
disable-model-invocation: true
allowed-tools: Bash(koro *), Read(~/.cache/koro/**)
---
Load the `koro:learn` skill with the Skill tool and follow it for this request:

    notebook $ARGUMENTS

If the Skill tool can't load it, the same instructions are in `${CLAUDE_PLUGIN_ROOT}/skills/learn/SKILL.md`.
