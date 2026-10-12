---
description: Add a PDF (book or paper) to koro and offer to parse it into lessons.
argument-hint: "<path to pdf>"
disable-model-invocation: true
allowed-tools: Bash(koro *), Read(~/.cache/koro/**)
---
Load the `koro:learn` skill with the Skill tool and follow it for this request:

    add $ARGUMENTS

If the Skill tool can't load it, the same instructions are in `${CLAUDE_PLUGIN_ROOT}/skills/learn/SKILL.md`.
