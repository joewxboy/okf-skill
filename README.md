# okf-skill

An [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
that teaches Claude to read, write, validate, and maintain
[Open Knowledge Format (OKF)](https://github.com/GoogleCloudPlatform/open-knowledge-format)
bundles: directories of markdown "concepts" with YAML frontmatter that describe tables,
metrics, datasets, and playbooks, with first-class provenance, trust, lifecycle, and
Attested Computations.

Targets **OKF v0.2**.

## What it does

- **Reads bundles** through their `index.md` files, and derives trust tiers (`verified`),
  status, and staleness (`stale_after`) only from the fields the spec defines.
- **Writes concepts** that follow the spec: keyed `sources` footnotes, honest
  `generated` actors, and no invented `verified` entries. It doesn't edit neighbouring
  concepts, especially human-verified ones, unless asked.
- **Validates** bundles against the spec's conformance rules (§11) and its
  recommended-practice rules.
- **Authors Attested Computations**, including draft executor and attester stubs.
- **Migrates v0.1 → v0.2** without changing what a query means: the SQL text and its
  engine are kept as written.

## Contents

| Path | Purpose |
|---|---|
| `SKILL.md` | The skill: instructions Claude follows when it's triggered. |
| `references/SPEC.md` | Verbatim copy of the OKF v0.2 specification (Apache 2.0, see `NOTICE`). |
| `scripts/okf.py` | Helper CLI: `validate`, `summary`, `show`, `index`. |
| `evals/evals.json` | Test prompts and checks used during development. |

## Install

**Claude Code**

```bash
git clone https://github.com/joewxboy/okf-skill ~/.claude/skills/okf
```

**claude.ai / Claude desktop:** download the repository as a zip (or package it as a
`.skill` file) and upload it under *Settings → Capabilities → Skills*.

Requires Python 3.9+ with PyYAML (`pip install pyyaml`) for `scripts/okf.py`.

## Using the helper script directly

```bash
python3 scripts/okf.py validate path/to/bundle            # exit 1 on conformance errors
python3 scripts/okf.py summary  path/to/bundle --json     # id, type, status, trust tier, stale
python3 scripts/okf.py show     path/to/bundle/tables/orders.md --bundle path/to/bundle
python3 scripts/okf.py index    path/to/bundle --dry-run  # preview regenerated index.md files
```

The parser keeps timestamps exactly as written, so round-tripping a file doesn't
rewrite `2026-06-30T14:00:00Z` as `2026-06-30 14:00:00+00:00`.

## Development notes

The skill was built and tested with Anthropic's skill-creator workflow:

- Each test prompt was run with and without the skill, against copies of the official
  sample bundles.
- The description was tuned on 20 trigger queries (10 should-trigger, 10 near-miss
  should-not).

The upstream spec lives at
[GoogleCloudPlatform/open-knowledge-format](https://github.com/GoogleCloudPlatform/open-knowledge-format).
The `okf/` folder in `GoogleCloudPlatform/knowledge-catalog` is a frozen snapshot of the
same v0.2 text.

## License

Apache License 2.0; see [`LICENSE`](LICENSE). `references/SPEC.md` is by the Open Knowledge Format authors (GoogleCloudPlatform),
redistributed under Apache 2.0; see [`NOTICE`](NOTICE).
