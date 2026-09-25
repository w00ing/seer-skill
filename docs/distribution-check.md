# Distribution check

`tests/manual/verify_distribution.py` runs an opt-in, local distribution evaluation. It stages the v0.8 skill from the read-only Git archive at `097198e26a114dafd6b160a5e31b8b9fbf4eab92`, checks CLI help and imports, replaces the staged package with the current `skills/seer` tree, and checks the updated Codex skill path and Claude plugin metadata. It then carries a synthetic baseline from the v0.8 install into the updated package and runs five deterministic PNG comparisons.

The first comparison checks that a missing baseline returns `needs_baseline` without creating verification storage. The scenarios cover an identical pass, a changed pixel outside an ignore rectangle, a change inside the ignored rectangle, a dimension error, and another missing-baseline verdict. The default is three repetitions. The report gives exact-status correctness across all scenario runs and reports false passes as a separate count over the expected-nonpass cases (`fail`, `error`, and `needs_baseline`). Durations use `time.monotonic`; reports also include Python, Pillow, operating system, source commit, and candidate skill tree hash.

The evaluator mirrors the documented package paths with copies under `.seer/qa/`. It does not use the network, invoke `$skill-installer`, install a Claude plugin, or access `~/.codex` or `~/.claude`. It checks package contents and locally staged CLI behavior; it does not establish compatibility with the real Codex or Claude host installers. All comparison inputs are generated synthetic images.

Run it with the Python 3.9 base dependencies:

```bash
python3.9 -m venv .local/qa-py39
.local/qa-py39/bin/python -m pip install "Pillow>=11,<12" "jsonschema>=4,<5"
.local/qa-py39/bin/python tests/manual/verify_distribution.py --repetitions 3
```

Or use the Python 3.13 MCP environment used by CI:

```bash
python3.13 -m venv .local/qa-py313
.local/qa-py313/bin/python -m pip install -r skills/seer/scripts/requirements-mcp.txt
.local/qa-py313/bin/python tests/manual/verify_distribution.py --repetitions 3
```

The script prints the generated `report.json` and `report.md` paths; both are kept with the staged packages and synthetic evidence under `.seer/qa/distribution-check-*`. CI runs the automated suite on explicit `macos-26` runners with Python 3.9 and 3.13. The Python 3.9 base lane installs Pillow 11.x and `jsonschema` and skips MCP protocol tests; Python 3.13 installs the pinned optional MCP requirements and runs those tests.
