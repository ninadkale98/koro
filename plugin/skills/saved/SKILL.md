---
description: Show what you've saved in koro to learn later, grouped by theme.
argument-hint: "[tag or search words]"
disable-model-invocation: true
allowed-tools: Bash(koro *), Read(~/.cache/koro/**)
---
Load the `koro:learn` skill with the Skill tool and follow it for this request:

    saved $ARGUMENTS

If the Skill tool can't load it, the same instructions are in `${CLAUDE_PLUGIN_ROOT}/skills/learn/SKILL.md`.
