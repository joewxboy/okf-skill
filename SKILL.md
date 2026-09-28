---
name: okf
description: |
  Use this skill for any work on an Open Knowledge Format (OKF) bundle, even if the user never says "OKF". A bundle is a folder (often `knowledge/` or `docs/okf/`) of markdown "concepts". Each concept has YAML frontmatter with a `type:` key and fields like `sources`, `generated`, `verified`, `status` and `stale_after`, and the folder has `index.md`/`log.md` files. Use it to:

  - audit or list which concepts are stale, expired, unverified, draft or deprecated, or lack human review or sign-off
  - answer questions from the bundle's knowledge
  - add or edit concepts that document tables, metrics, datasets or playbooks
  - cite sources with footnotes
  - regenerate index files
  - check whether a bundle is valid
  - write Attested Computations
  - migrate an OKF bundle from v0.1 to v0.2
  - start from Google's open-knowledge-format or knowledge-catalog repos

  Do not use it for generic markdown, blog frontmatter, doc sites, API docs or markdown linting.
---

# Open Knowledge Format (OKF) v0.2

OKF represents knowledge as **a directory of markdown files with YAML frontmatter**. A
directory tree is a **bundle**; each non-reserved `.md` file is a **concept**; its
**concept ID** is its bundle-relative path without `.md` (e.g. `tables/orders`).

The format is deliberately permissive: the only hard requirement is that every concept
has parseable frontmatter with a non-empty `type`. Everything else is SHOULD-level
guidance, and consumers must never reject a bundle for missing optional fields, unknown
types or keys, broken links, or missing `index.md`. Keep that asymmetry in mind: be
**strict when writing** (produce everything the spec recommends) and **lenient when
reading** (degrade gracefully, report rather than refuse).

The full spec is bundled at `references/SPEC.md` (v0.2, Apache 2.0). This file covers
what you need for everyday work; open the spec for edge cases, exact wording, or when a
user asks "what does the spec say about X" — cite section numbers (§5.1 etc.).
Canonical upstream: https://github.com/GoogleCloudPlatform/open-knowledge-format (the
`knowledge-catalog/okf/` copy is a frozen snapshot of the same v0.2 text).

## Helper script

`scripts/okf.py` (needs PyYAML) does the mechanical parts deterministically. Prefer it
over hand-rolling parsers — it preserves timestamp strings exactly (PyYAML would
otherwise silently rewrite `2026-06-30T14:00:00Z` as `2026-06-30 14:00:00+00:00`).

```bash
OKF=~/.claude/skills/okf/scripts/okf.py
python3 $OKF validate <bundle>            # conformance + SHOULD-level checks; exit 1 on errors
python3 $OKF validate <bundle> --min-level warning   # hide info lines
python3 $OKF summary  <bundle> [--type Metric] [--tag finance] [--json]
python3 $OKF show     <bundle>/tables/orders.md --bundle <bundle>   # JSON: frontmatter, derived trust/staleness, links, claims per source
python3 $OKF index    <bundle> --dry-run   # preview regenerated index.md files
python3 $OKF index    <bundle> [--okf-version 0.2]   # write them
```

Validator levels: **error** = violates §11 conformance (the bundle is not OKF);
**warning** = breaks a SHOULD/REQUIRED-within-a-family rule; **info** = legitimate but
worth knowing (broken links, stale concepts, legacy v0.1 fields, stale indexes). When
reporting to the user, lead with errors, summarize warnings, and mention info only when
relevant to their question.

## Reading / consuming a bundle

1. **Orient via progressive disclosure.** Read the root `index.md` first, then descend
   into the relevant subdirectory's `index.md`, and only then open individual concepts.
   If there is no index, run `okf.py summary`. Don't slurp every file into context for a
   large bundle.
2. **Parse each concept** as frontmatter + body. Tolerate unknown `type` values (treat as
   generic) and unknown keys (preserve them).
3. **Derive, don't guess, trust and freshness** — only from these fields:
   - **Trust tier** from `verified`: absent ⇒ *unverified*; only non-`human:` actors ⇒
     *machine-confirmed*; any `human:<id>` actor ⇒ *human-reviewed*. A bare
     `verified: { by, at }` mapping counts as a one-element list.
   - **Status**: `draft | stable | deprecated`; absent ⇒ `stable`.
   - **Stale** when `now >= stale_after` (an absolute instant).
   - **Last content change**: `generated.at` (fallback: legacy v0.1 `timestamp`).
   - "How recently verified" = latest `verified[].at`. `verified` and `generated.at`
     are independent — content can change without re-verification.
4. **Attribute claims** through footnotes: a footnote label like `[^rev-policy]` is a join
   key into `sources[].id`. Resolve via the matching `sources` entry, not the footnote
   prose. `okf.py show` returns `claims_by_source` for this.
5. **Follow links** as untyped, directed relationships; the prose says what kind. Links
   starting with `/` are bundle-root-relative; others are relative to the file. A broken
   link is not an error — it may be knowledge not yet written.
6. **Surface caveats when answering from a bundle**: say when a concept you relied on is
   `deprecated`, `draft`, stale, or unverified, and prefer the non-deprecated concept a
   deprecated one points to. Users building on agent-generated knowledge need to know
   how much to trust it; that is the point of OKF v0.2.

## Writing / authoring concepts

Use this template and drop the families that don't apply. Key order below is the
conventional one used by the reference bundles.

````markdown
---
type: BigQuery Table                 # REQUIRED. Descriptive, self-explanatory, Title Case.
title: Customer Orders
description: One row per completed customer order across all channels.   # one sentence
resource: https://console.cloud.google.com/bigquery?p=acme&d=sales&t=orders  # only for concrete assets
tags: [sales, orders]
generated: { by: claude-code/claude-opus-5-5, at: 2026-09-27T14:30:00Z }
verified: { by: human:jdoe, at: 2026-09-27T15:00:00Z }   # only if someone actually verified it
status: stable                       # draft | stable | deprecated
stale_after: 2027-03-31T00:00:00Z
sources:
  - id: orders-ddl
    resource: https://wiki.acme/data/orders
    title: Orders table design doc
    author: human:jdoe
    last_modified: 2026-08-01T00:00:00Z
---

# Schema

| Column        | Type    | Description                                   |
|---------------|---------|-----------------------------------------------|
| `order_id`    | STRING  | Globally unique order identifier.[^orders-ddl] |
| `customer_id` | STRING  | Foreign key into [customers](/tables/customers.md). |

# Examples

```sql
SELECT COUNT(*) FROM `acme.sales.orders` WHERE DATE(placed_at) = CURRENT_DATE();
```

[^orders-ddl]: Orders table design doc
````

Rules worth internalizing (and why):

- **`type` is the only required key**, but always add `title` and a one-sentence
  `description` — index files, search snippets and agents browsing the bundle depend on
  them.
- **Timestamps** are ISO 8601 datetimes with an explicit offset (`...Z` or `+02:00`),
  never bare dates: a date names a different instant in every timezone. Use the real
  current time for `generated.at`.
- **Actors** (`generated.by`, `verified[].by`): `<producer>/<version>` for agents/tools,
  `human:<id>` for people, `process:<id>` for automation. When *you* write content,
  record yourself honestly, e.g. `claude-code/<model-id>`. Use `human:` only for content a
  person wrote or confirmed — trust tiers key off that prefix.
- **Never fabricate `verified`.** Writing a concept is not verifying it. Add a `verified`
  entry only when the user (or a named process) actually confirmed it, and use their
  identity. When you edit a concept's content, update `generated` and leave existing
  `verified` entries alone unless asked — the gap between them is meaningful signal.
- **Keep your edits to the concepts the user asked about.** Touching any other concept
  has two costs: rewriting its `generated.by` re-attributes the *whole* document to you
  for a one-line change, and if it carries `verified`, its sign-off now silently covers
  text the reviewer never saw. So when a neighbouring concept would benefit from a
  change (a backlink, a cross-reference, a corrected fact), don't make it; name the
  concept and the suggested change in your reply, and note when it would need re-review
  because it is verified. The new concept can link *out* to its neighbours freely; that
  costs nothing. Make the neighbouring edit only when the user asked for it (e.g.
  "update whatever else needs updating" covers `index.md` and `log.md`, not the
  content of other concepts, especially verified ones).
- **Provenance goes in `sources`**, not a `# Citations` body list (that's v0.1). Every
  entry needs `resource` (URL, bundle path, or a scope descriptor such as `all queries in
  BigQuery project X`). Give an `id` to any source the body cites, and cite with
  `[^id]` footnotes — keyed, not numbered, so reordering never misattributes. If you
  include `usage_count`, frame it with a `usage_window: { from, to }` sibling.
- **Only record credibility signals you actually know** (`author`, `usage_count`,
  `last_modified`). Don't invent numbers; OKF deliberately stores signals, never scores.
- **Body**: prefer structure (headings, tables, lists, fenced code) over prose. Use the
  conventional headings `# Schema`, `# Examples`, `# Computation` when they apply.
- **Links**: prefer bundle-root-relative (`/tables/customers.md`); they survive moves.
- **Filenames**: never name a concept `index.md` or `log.md` (reserved at every level).
  Use lowercase kebab- or snake-case matching the bundle's existing convention.
- **Preserve unknown keys** when editing someone else's concept. Edit frontmatter
  surgically (Edit tool on the specific lines) rather than re-serializing the whole
  YAML block, so formatting, flow-style mappings and comments survive.
- **Deprecating**: set `status: deprecated`, keep the file (links and history depend on
  it), and link to the replacement in the body. Don't delete.

### Index files (`index.md`)

No frontmatter — except the bundle-root `index.md`, which may carry only
`okf_version: "0.2"`. Body: `# Section` headings with bullet entries:

```markdown
# Subdirectories

* [tables](tables/index.md) - BigQuery tables the bundle grounds against.

# BigQuery Table

* [Customer Orders](orders.md) - One row per completed customer order across all channels.
```

After adding, renaming or re-describing concepts, regenerate with `okf.py index`. It
groups by `type`, sorts by title, pulls descriptions from frontmatter, keeps existing
subdirectory descriptions, and keeps hand-written entries for non-markdown files (e.g.
attester `.py` scripts). It **does** replace hand-curated ordering and shortened
descriptions, so on a bundle with a curated index run `--dry-run` first and, if the
curation matters, edit the index by hand instead. A new subdirectory gets an empty
description — fill it in.

### Log files (`log.md`)

Optional, at any level, newest first, date headings in `YYYY-MM-DD`:

```markdown
# Bundle history

## 2026-09-27
* **Creation**: Added [Customer Orders](/tables/orders.md).
* **Deprecation**: Retired [legacy margin](/metrics/gross-margin-legacy.md).
```

When you make a meaningful change to a bundle that already has a `log.md`, add an entry
under today's date (create the heading if needed, at the top).

### Generated side files (`viz.html` etc.)

Bundles produced by Google's reference agent often carry a `viz.html` graph viewer at the
root. It is **not part of OKF** — it is a rendered snapshot generated by that tool, so it
goes stale whenever concepts change. Don't hand-edit it (it embeds the bundle as data and
is easy to corrupt). After changing a bundle that has one, tell the user it no longer
reflects the new concepts and that it is regenerated with the reference agent's viewer
(`reference_agent.viewer` in github.com/GoogleCloudPlatform/open-knowledge-format).

## Attested Computations

Use `type: Attested Computation` when a value must be produced a sanctioned way (a
headline metric, a finance figure) and a consumer should be able to prove it was. Each
computation is its **own concept**; metrics and reports link to it. One figure = one
computation concept, because trust state (`verified`, `stale_after`, attester) is per
computation.

````markdown
---
type: Attested Computation
title: Revenue for fiscal year
description: Recognized revenue for a fiscal year, per Finance's definition.
status: stable
runtime: bigquery                    # REQUIRED for this type; defines what parameters mean
parameters:
  - { name: year, type: integer, required: true }
executor:
  resource: references/skills/run-on-bq.md   # run instructions/code
  receipt: [job_id, executed_sql, result]    # evidence a run must return
attester:
  resource: references/attesters/sql-equality.py   # deterministic, no-LLM check
generated: { by: claude-code/claude-opus-5-5, at: 2026-09-27T14:00:00Z }
stale_after: 2026-12-31T00:00:00Z
sources:
  - id: rev-policy
    resource: https://wiki.acme/finance/revenue-recognition
    title: Revenue recognition policy
---

# Computation

```sql
SELECT SUM(amount) AS revenue
FROM finance.recognized_revenue
WHERE fiscal_year = @year
```

Binds only the declared `parameters`, per the recognition policy.[^rev-policy]

[^rev-policy]: Revenue recognition policy
````

- Put the computation **either** in one code block under `# Computation` **or** in a file
  named by `computation:` (then omit the body block) — not both.
- Parameter placeholders follow the runtime's native binding (`@year` for BigQuery,
  `{{ var('year') }}` for dbt, function args for Python). Never string-interpolate.
- **When consuming one**: supply only parameter *values*; never edit or rewrite the
  computation, even to "fix" or extend it — a rewritten query is exactly what the
  attester exists to catch. If the user needs a different cut, do it downstream of the
  result or propose a new computation concept for human review. Run the executor,
  collect the receipt fields, run the attester, and refuse to present a value whose
  attestation fails; warn when the computation is stale.
- `verified` (the *definition* still matches policy, recorded in the bundle) and
  attestation (this *run* was the sanctioned one, runtime-only, never stored in the
  bundle) are different things; don't conflate them.

## Creating a new bundle

1. Lay out directories by whatever grouping fits the domain (`tables/`, `metrics/`,
   `computations/`, `playbooks/`, `references/` for mirrored external material,
   executor skills and attester code). There is no fixed taxonomy.
2. Write concepts per the template above.
3. `python3 $OKF index <bundle> --okf-version 0.2`, then fill in subdirectory descriptions.
4. Optionally add a root `log.md` with an `**Initialization**` entry.
5. `python3 $OKF validate <bundle>` — fix all errors and warnings you introduced.

## Migrating v0.1 → v0.2

v0.1 bundles remain consumable, but to upgrade:

- `timestamp: '...'` → `generated: { by: <actor>, at: <ISO datetime> }`. If the original
  producer is unknown, ask the user rather than guessing; don't mark it `human:` unless
  a human wrote it.
- `# Citations` body list → `sources` entries with ids; replace inline references with
  `[^id]` footnotes and add the footnote definitions. Numbered footnotes (`[^1]`) should
  become keyed ones.
- SQL embedded in prose for headline figures → split into `Attested Computation`
  concepts, one per figure, and link to them from the narrative concept.
- Add `status`/`stale_after` where the user can tell you the lifecycle.
- Set `okf_version: "0.2"` on the root `index.md` and validate.

**A migration changes the format, not the meaning.** The whole point of an Attested
Computation is that the query is the sanctioned one, so migrating one must not quietly
become a rewrite:

- Keep each query's text as written. The only change is turning informal placeholders
  (`<year>`) into the runtime's native parameter binding (`@year` for BigQuery).
- Keep the query engine the source implies. Plain SQL stays plain SQL — use the runtime
  the rest of the document or bundle uses (e.g. `bigquery`). Don't switch to dbt,
  qualify table names, or restructure the query because an example did; if something
  is ambiguous (an unqualified table, a name that looks like a dbt model), keep it as-is
  and flag the question in your reply.
- Spec Appendix A migrates a similar income statement, but its values (dbt runtime,
  `verified` entries, `stale_after` dates, `usage_count`s, file names) are
  *illustrations*. Never copy them into a real bundle; every value you write must come
  from the source document or the user.

**Offer runnable executor and attester stubs.** A computation without them can't
actually be attested, so leaving them out produces a half-finished migration. By
default, create them under `references/` and point `executor.resource` /
`attester.resource` at them:

- `references/skills/run-on-<runtime>.md` — a concept (`type: Skill`, `status: draft`)
  with run instructions: load the computation, bind the declared parameters natively
  (never string-interpolate), run it, and return exactly the fields listed in
  `executor.receipt` (for SQL runtimes typically `[job_id, executed_sql, result]`).
- `references/attesters/sql-equality.py` — deterministic, no-LLM Python: load the
  concept's computation, canonicalize it and the receipt's `executed_sql` the same way
  (strip comments, collapse whitespace, normalize keyword case), require equality and
  that the bound parameters match the declared names and types; print a JSON verdict and
  exit non-zero on failure. Smoke-test it on one passing and one tampered receipt.

Mark the stubs `status: draft` and say in your reply that they need a human's review
before anyone relies on them. If the user said not to create code, or the runtime is one
you can't write a sensible check for, leave `executor`/`attester` out, mark the
computation `draft`, and list what is missing instead.

## Reporting results

When asked whether a bundle is valid, answer with the verdict first ("Conformant with
OKF v0.2 — 0 errors, 3 warnings"), then the concrete issues with file paths and the
spec section, then offer fixes. When you change a bundle, finish by running `validate`
and state the outcome.
