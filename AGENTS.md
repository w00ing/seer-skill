# AGENTS.md

## Agent Protocol

- Do not spawn or delegate work to sub-agents unless the user explicitly requests sub-agents or parallel work for the current task. Treat requests such as "work in parallel" or "병렬로 작업해" as explicit authorization to use sub-agents. When explicitly requested, use the global `baton` skill for orchestration.
