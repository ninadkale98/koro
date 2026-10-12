---
description: Save something to learn later in koro (a topic, question, link, PDF or snippet), without leaving what you're doing.
argument-hint: "<topic, question, URL or file path> [why it caught your eye]"
disable-model-invocation: true
allowed-tools: Bash(koro *), Read(~/.cache/koro/**)
---
Load the `koro:learn` skill with the Skill tool and follow it for this request:

    save $ARGUMENTS

If the Skill tool can't load it, the same instructions are in `${CLAUDE_PLUGIN_ROOT}/skills/learn/SKILL.md`.
