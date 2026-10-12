---
description: Start or jump into a book (and optionally a section) with koro.
argument-hint: "<book title> [section key]"
disable-model-invocation: true
allowed-tools: Bash(koro *)
---
Load the `koro:learn` skill with the Skill tool and follow it for this request:

    start $ARGUMENTS

If the Skill tool can't load it, the same instructions are in `${CLAUDE_PLUGIN_ROOT}/skills/learn/SKILL.md`.
