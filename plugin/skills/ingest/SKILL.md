---
description: Parse an added source into koro lessons, section by section (resumable on any machine).
argument-hint: "[book title]"
disable-model-invocation: true
allowed-tools: Bash(koro *), Read(~/.cache/koro/**)
---
Load the `koro:learn` skill with the Skill tool and follow it for this request:

    ingest $ARGUMENTS

If the Skill tool can't load it, the same instructions are in `${CLAUDE_PLUGIN_ROOT}/skills/learn/SKILL.md`.
