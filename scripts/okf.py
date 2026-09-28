#!/usr/bin/env python3
"""Tools for Open Knowledge Format (OKF) v0.2 bundles.

Subcommands:
  validate BUNDLE   Check conformance (§11) plus the SHOULD-level rules of §5-§10.
  summary  BUNDLE   One row per concept: id, type, title, status, trust tier, staleness.
  show     FILE     Parsed frontmatter plus derived fields for a single concept.
  index    BUNDLE   (Re)generate index.md files from concept frontmatter.

Run `okf.py <subcommand> -h` for options. Requires PyYAML.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("okf.py needs PyYAML: pip install pyyaml")

RESERVED = {"index.md", "log.md"}
STATUSES = {"draft", "stable", "deprecated"}
SKIP_DIRS = {".git", ".github", "node_modules", "__pycache__", ".venv"}


# ---------------------------------------------------------------- parsing


class _Loader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps as the strings the author wrote.

    PyYAML (YAML 1.1) would turn `2026-06-30T14:00:00Z` into a datetime and
    re-dump it as `2026-06-30 14:00:00+00:00`, silently rewriting the file.
    """


_Loader.yaml_implicit_resolvers = {
    ch: [(t, r) for t, r in rs if t != "tag:yaml.org,2002:timestamp"]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class ParseError(ValueError):
    pass


@dataclass
class Doc:
    path: Path
    frontmatter: dict[str, Any] | None  # None => no frontmatter block
    body: str


def parse_text(text: str, path: Path = Path("<string>")) -> Doc:
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return Doc(path, None, text)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            break
    else:
        raise ParseError("unterminated frontmatter block (no closing ---)")
    try:
        fm = yaml.load("\n".join(lines[1:i]), Loader=_Loader)
    except yaml.YAMLError as e:
        raise ParseError("invalid YAML in frontmatter: " + " ".join(str(e).split()))
    if fm is None:
        fm = {}
    if not isinstance(fm, dict):
        raise ParseError("frontmatter is not a YAML mapping")
    body = "\n".join(lines[i + 1:]).lstrip("\n")
    return Doc(path, fm, body)


def parse_file(path: Path) -> Doc:
    return parse_text(path.read_text(encoding="utf-8"), path)


def iter_md(root: Path):
    for p in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def concept_id(root: Path, path: Path) -> str:
    return path.relative_to(root).with_suffix("").as_posix()


# ---------------------------------------------------------------- derived fields


ISO_DT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$"
)
ACTOR = re.compile(r"^((human|process):\S+|[^\s/:]+/\S+)$")


def parse_dt(value: Any) -> datetime | None:
    s = str(value or "").strip()
    if not ISO_DT.match(s):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def verified_events(fm: dict) -> list[dict]:
    v = fm.get("verified")
    if v is None:
        return []
    if isinstance(v, dict):  # §5.2: bare mapping == one-element list
        return [v]
    if isinstance(v, list):
        return [e for e in v if isinstance(e, dict)]
    return []


def trust_tier(fm: dict) -> str:
    events = verified_events(fm)
    if not events:
        return "unverified"
    if any(str(e.get("by", "")).startswith("human:") for e in events):
        return "human-reviewed"
    return "machine-confirmed"


def last_verified(fm: dict) -> str | None:
    ats = [(parse_dt(e.get("at")), str(e.get("at"))) for e in verified_events(fm)]
    ats = [a for a in ats if a[0]]
    return max(ats)[1] if ats else None


def is_stale(fm: dict, now: datetime) -> bool | None:
    """True/False, or None when stale_after is absent or unparseable."""
    if "stale_after" not in fm:
        return None
    dt = parse_dt(fm.get("stale_after"))
    return None if dt is None else now >= dt


def status_of(fm: dict) -> str:
    return str(fm.get("status") or "stable")  # §5.4: absent => stable


def generated_at(fm: dict) -> str | None:
    g = fm.get("generated")
    if isinstance(g, dict) and g.get("at"):
        return str(g["at"])
    if fm.get("timestamp"):  # v0.1 fallback (§13.1)
        return str(fm["timestamp"])
    return None


def derived(fm: dict, now: datetime) -> dict:
    return {
        "status": status_of(fm),
        "trust_tier": trust_tier(fm),
        "last_verified": last_verified(fm),
        "generated_at": generated_at(fm),
        "stale": is_stale(fm, now),
    }


# ---------------------------------------------------------------- body helpers


LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FOOTNOTE_REF = re.compile(r"\[\^([^\]\s]+)\](?!:)")
FOOTNOTE_DEF = re.compile(r"^\[\^([^\]\s]+)\]:", re.M)
FENCE = re.compile(r"^(```|~~~)")


def strip_code(body: str) -> str:
    """Remove fenced code blocks so links/footnotes inside code are ignored."""
    out, in_fence = [], False
    for line in body.splitlines():
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def headings(body: str, level: int = 1) -> list[str]:
    prefix = "#" * level + " "
    return [
        l[len(prefix):].strip()
        for l in strip_code(body).splitlines()
        if l.startswith(prefix)
    ]


def section(body: str, name: str) -> str | None:
    """Text under a level-1 heading (fences included) up to the next level-1 heading."""
    lines, grab, out, in_fence = body.splitlines(), False, [], False
    for line in lines:
        if FENCE.match(line.strip()):
            in_fence = not in_fence
        if not in_fence and line.startswith("# "):
            if grab:
                break
            grab = line[2:].strip().lower() == name.lower()
            continue
        if grab:
            out.append(line)
    return "\n".join(out) if grab else None


def has_code_block(text: str) -> bool:
    if re.search(r"^\s*(```|~~~)", text, re.M):
        return True
    return bool(re.search(r"^(    |\t)\S", text, re.M))


def is_url(s: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", s))


def resolve(root: Path, base_dir: Path, target: str, also_root: bool = False) -> Path | None:
    """Resolve a bundle path. Returns the existing path, or None if not found."""
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return base_dir
    cands = [root / target.lstrip("/")] if target.startswith("/") else [base_dir / target]
    if also_root and not target.startswith("/"):
        cands.append(root / target)
    for c in cands:
        if c.exists():
            return c
    return None


# ---------------------------------------------------------------- validate


@dataclass
class Finding:
    level: str  # error | warning | info
    file: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, file: str, msg: str):
        self.findings.append(Finding(level, file, msg))


def _check_ts(rep, rel, key, value):
    if value is not None and parse_dt(value) is None:
        rep.add("warning", rel, f"`{key}` = {value!r} is not an ISO 8601 datetime with an explicit UTC offset (§5)")


def _check_actor(rep, rel, key, value):
    if not value:
        rep.add("warning", rel, f"`{key}` is missing an actor (§5.2)")
    elif not ACTOR.match(str(value)):
        rep.add("warning", rel, f"`{key}` = {value!r} does not follow the actor convention "
                                "`<producer>/<version>`, `human:<id>`, or `process:<id>` (§7)")


def _check_path_field(rep, root, path, rel, key, value, scope_ok=False):
    if not isinstance(value, str) or not value.strip():
        rep.add("warning", rel, f"`{key}` should be a path or URL string (§6.2)")
        return
    if is_url(value):
        return
    if scope_ok and re.search(r"\s", value):
        return  # a scope descriptor such as "all queries in project X" (§5.1)
    if resolve(root, path.parent, value, also_root=True) is None:
        rep.add("info", rel, f"`{key}` points at {value!r}, which is not in the bundle (allowed; may be external or unwritten)")


def validate_concept(rep: Report, root: Path, doc: Doc, now: datetime, ids: set[str]):
    rel = doc.path.relative_to(root).as_posix()
    fm = doc.frontmatter
    typ = fm.get("type")
    if typ is None or (isinstance(typ, str) and not typ.strip()) or typ == "":
        rep.add("error", rel, "missing or empty required `type` (§4.1, §11)")
    elif not isinstance(typ, str):
        rep.add("warning", rel, f"`type` should be a string, got {type(typ).__name__}")

    for k in ("title", "description"):
        if k in fm and not isinstance(fm[k], str):
            rep.add("warning", rel, f"`{k}` should be a string")
    if "description" not in fm:
        rep.add("info", rel, "no `description` (recommended; used by index.md and search)")
    if "tags" in fm and not (isinstance(fm["tags"], list) and all(isinstance(t, str) for t in fm["tags"])):
        rep.add("warning", rel, "`tags` should be a YAML list of strings (§4.1)")
    if "resource" in fm and not isinstance(fm["resource"], str):
        rep.add("warning", rel, "`resource` should be a URI string")

    # trust (§5.2)
    g = fm.get("generated")
    if g is not None:
        if not isinstance(g, dict):
            rep.add("warning", rel, "`generated` should be a mapping `{ by, at }`")
        else:
            _check_actor(rep, rel, "generated.by", g.get("by"))
            _check_ts(rep, rel, "generated.at", g.get("at"))
    elif "timestamp" in fm:
        rep.add("info", rel, "legacy v0.1 `timestamp`; v0.2 uses `generated: { by, at }` (§13.1)")
    v = fm.get("verified")
    if v is not None:
        if not isinstance(v, (dict, list)):
            rep.add("warning", rel, "`verified` should be a `{ by, at }` mapping or a list of them")
        else:
            for i, e in enumerate(v if isinstance(v, list) else [v]):
                if not isinstance(e, dict):
                    rep.add("warning", rel, f"`verified[{i}]` should be a `{{ by, at }}` mapping")
                    continue
                _check_actor(rep, rel, f"verified[{i}].by", e.get("by"))
                if e.get("at") is None:
                    rep.add("warning", rel, f"`verified[{i}].at` is missing")
                _check_ts(rep, rel, f"verified[{i}].at", e.get("at"))

    # lifecycle (§5.4, §5.5)
    if "status" in fm and fm["status"] not in STATUSES:
        rep.add("warning", rel, f"`status` = {fm['status']!r}; expected draft | stable | deprecated (§5.4)")
    if "stale_after" in fm:
        _check_ts(rep, rel, "stale_after", fm["stale_after"])
        if is_stale(fm, now):
            rep.add("info", rel, f"stale: now >= stale_after ({fm['stale_after']})")

    # provenance (§5.1)
    src_ids: list[str] = []
    srcs = fm.get("sources")
    if srcs is not None:
        if not isinstance(srcs, list):
            rep.add("warning", rel, "`sources` should be a list")
            srcs = []
        for i, s in enumerate(srcs):
            if not isinstance(s, dict):
                rep.add("warning", rel, f"`sources[{i}]` should be a mapping")
                continue
            if not s.get("resource"):
                rep.add("warning", rel, f"`sources[{i}]` is missing required `resource` (§5.1)")
            else:
                _check_path_field(rep, root, doc.path, rel, f"sources[{i}].resource", s["resource"], scope_ok=True)
            if "id" in s:
                src_ids.append(str(s["id"]))
            if "usage_count" in s and not isinstance(s["usage_count"], int):
                rep.add("warning", rel, f"`sources[{i}].usage_count` should be an integer")
            _check_ts(rep, rel, f"sources[{i}].last_modified", s.get("last_modified"))
            uw = s.get("usage_window")
            if isinstance(uw, dict):
                _check_ts(rep, rel, f"sources[{i}].usage_window.from", uw.get("from"))
                _check_ts(rep, rel, f"sources[{i}].usage_window.to", uw.get("to"))
        dups = {x for x in src_ids if src_ids.count(x) > 1}
        if dups:
            rep.add("warning", rel, f"duplicate `sources` ids: {sorted(dups)}")
        if any(isinstance(s, dict) and "usage_count" in s for s in srcs) and not (
            "usage_window" in fm or all(isinstance(s, dict) and ("usage_window" in s or "usage_count" not in s) for s in srcs)
        ):
            rep.add("warning", rel, "`usage_count` present but no `usage_window` frames it (§5.1)")
    uw = fm.get("usage_window")
    if uw is not None:
        if not isinstance(uw, dict):
            rep.add("warning", rel, "`usage_window` should be a `{ from, to }` mapping")
        else:
            _check_ts(rep, rel, "usage_window.from", uw.get("from"))
            _check_ts(rep, rel, "usage_window.to", uw.get("to"))

    text = strip_code(doc.body)
    refs = set(FOOTNOTE_REF.findall(text))
    defs = set(FOOTNOTE_DEF.findall(text))
    if src_ids:
        for label in sorted(refs - set(src_ids)):
            rep.add("warning", rel, f"footnote [^{label}] has no matching `sources[].id` (§5.1)")
    elif refs and srcs is None:
        rep.add("info", rel, "body has footnotes but no `sources`; per-claim attribution keys footnotes to `sources[].id` (§5.1)")
    for label in sorted(refs - defs):
        rep.add("warning", rel, f"footnote [^{label}] is referenced but never defined")
    if "Citations" in headings(doc.body):
        rep.add("info", rel, "legacy v0.1 `# Citations` section; v0.2 records provenance in `sources` (§13.1)")

    # attested computation (§10)
    if typ == "Attested Computation":
        if not fm.get("runtime"):
            rep.add("warning", rel, "Attested Computation is missing REQUIRED `runtime` (§10.2)")
        params = fm.get("parameters")
        if params is not None:
            if not isinstance(params, list):
                rep.add("warning", rel, "`parameters` should be a list of `{ name, type, required }`")
            else:
                for i, p in enumerate(params):
                    if not isinstance(p, dict) or not p.get("name") or not p.get("type"):
                        rep.add("warning", rel, f"`parameters[{i}]` should have `name` and `type` (§10.2)")
        comp_sec = section(doc.body, "Computation")
        body_fence = comp_sec is not None and has_code_block(comp_sec)
        if fm.get("computation"):
            _check_path_field(rep, root, doc.path, rel, "computation", fm["computation"])
            if body_fence:
                rep.add("warning", rel, "both `computation` and a body `# Computation` code block; "
                                        "with a file, omit the body fence (§10.3)")
        elif not body_fence:
            rep.add("warning", rel, "no computation: add a code block under `# Computation` or set `computation:` (§10.3)")
        for key in ("executor", "attester"):
            val = fm.get(key)
            if val is None:
                rep.add("info", rel, f"no `{key}` (optional, but needed for attestation §10.2)")
            elif not isinstance(val, dict) or not val.get("resource"):
                rep.add("warning", rel, f"`{key}` should be a mapping with `resource`")
            else:
                _check_path_field(rep, root, doc.path, rel, f"{key}.resource", val["resource"])
        ex = fm.get("executor")
        if isinstance(ex, dict) and "receipt" in ex and not isinstance(ex["receipt"], list):
            rep.add("warning", rel, "`executor.receipt` should be a list of field names")

    _check_links(rep, root, doc, rel)


def _check_links(rep, root, doc, rel):
    for _, target in LINK.findall(strip_code(doc.body)):
        if is_url(target) or target.startswith("#"):
            continue
        if resolve(root, doc.path.parent, target) is None:
            rep.add("info", rel, f"broken link -> {target} (tolerated per §6.1; may be unwritten knowledge)")


def validate_index(rep: Report, root: Path, doc: Doc):
    rel = doc.path.relative_to(root).as_posix()
    at_root = doc.path.parent == root
    if doc.frontmatter is not None:
        extra = set(doc.frontmatter) - {"okf_version"}
        if not at_root:
            rep.add("error", rel, "index.md must not have frontmatter below the bundle root (§8)")
        elif extra:
            rep.add("error", rel, f"root index.md frontmatter may only carry `okf_version`; found {sorted(extra)} (§8, §12)")
        elif str(doc.frontmatter.get("okf_version")) not in ("0.1", "0.2"):
            rep.add("info", rel, f"okf_version {doc.frontmatter.get('okf_version')!r} is not 0.2; consume best-effort (§12)")
    if not headings(doc.body):
        rep.add("warning", rel, "index.md should group entries under `# Heading` sections (§8)")
    listed: set[Path] = set()
    for _, target in LINK.findall(strip_code(doc.body)):
        if is_url(target):
            continue
        hit = resolve(root, doc.path.parent, target)
        if hit is None:
            rep.add("info", rel, f"index entry -> {target} does not exist")
        else:
            listed.add(hit.resolve())
            if hit.is_dir():
                listed.add((hit / "index.md").resolve())
            elif hit.name == "index.md":
                listed.add(hit.parent.resolve())
    missing = []
    for child in sorted(doc.path.parent.iterdir()):
        if child.name in RESERVED or child.name in SKIP_DIRS or child.name.startswith("."):
            continue
        if (child.is_file() and child.suffix == ".md") or (child.is_dir() and any(child.rglob("*.md"))):
            if child.resolve() not in listed:
                missing.append(child.name + ("/" if child.is_dir() else ""))
    if missing:
        rep.add("info", rel, f"index.md does not list: {', '.join(missing)} (regenerate with `okf.py index`)")


LOG_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_log(rep: Report, root: Path, doc: Doc):
    rel = doc.path.relative_to(root).as_posix()
    dates = headings(doc.body, level=2)
    for h in dates:
        if not LOG_DATE.match(h):
            rep.add("error", rel, f"log date heading `## {h}` must be ISO 8601 YYYY-MM-DD (§9)")
    good = [h for h in dates if LOG_DATE.match(h)]
    if good != sorted(good, reverse=True):
        rep.add("warning", rel, "log entries should be newest first (§9)")


def validate(root: Path, now: datetime) -> Report:
    rep = Report()
    files = list(iter_md(root))
    if not files:
        rep.add("error", ".", "no .md files found; is this an OKF bundle?")
    ids = {concept_id(root, p) for p in files if p.name not in RESERVED}
    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            doc = parse_file(p)
        except UnicodeDecodeError:
            rep.add("error", rel, "not valid UTF-8 (§4)")
            continue
        except ParseError as e:
            rep.add("error", rel, str(e) + " (§11)")
            continue
        if p.name == "index.md":
            validate_index(rep, root, doc)
        elif p.name == "log.md":
            validate_log(rep, root, doc)
        elif doc.frontmatter is None:
            rep.add("error", rel, "concept has no YAML frontmatter block (§4, §11)")
        else:
            validate_concept(rep, root, doc, now, ids)
    return rep


# ---------------------------------------------------------------- index generation


INDEX_ENTRY = re.compile(r"^\s*[*-]\s+\[([^\]]*)\]\(([^)\s]+)\)\s*(?:[-–—:]\s*(.*))?$")


def _existing_index(path: Path) -> tuple[dict | None, list[tuple[str, str, str, str]]]:
    """Return (frontmatter, [(heading, title, link, desc)]) of an existing index.md."""
    if not path.exists():
        return None, []
    try:
        doc = parse_file(path)
    except ParseError:
        return None, []
    entries, heading = [], ""
    for line in doc.body.splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
        m = INDEX_ENTRY.match(line)
        if m:
            entries.append((heading, m.group(1), m.group(2), (m.group(3) or "").strip()))
    return doc.frontmatter, entries


def build_index(root: Path, directory: Path, okf_version: str | None) -> str | None:
    fm, old = _existing_index(directory / "index.md")
    old_desc = {link.rstrip("/"): desc for _, _, link, desc in old}
    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    covered: set[str] = set()
    for child in sorted(directory.iterdir()):
        if child.name in RESERVED or child.name.startswith(".") or child.name in SKIP_DIRS:
            continue
        if child.is_file() and child.suffix == ".md":
            try:
                doc = parse_file(child)
            except (ParseError, UnicodeDecodeError):
                continue
            cfm = doc.frontmatter or {}
            title = str(cfm.get("title") or child.stem)
            desc = " ".join(str(cfm.get("description") or old_desc.get(child.name, "")).split())
            groups[str(cfm.get("type") or "Other")].append((title, child.name, desc))
            covered.add(child.name)
        elif child.is_dir() and any(child.rglob("*.md")):
            link = f"{child.name}/index.md"
            desc = old_desc.get(link) or old_desc.get(child.name) or ""
            groups["Subdirectories"].append((child.name, link, desc))
            covered.update({link, child.name})
    # Keep hand-written entries for non-concept files (scripts, data) under their old heading.
    for heading, title, link, desc in old:
        key = link.rstrip("/")
        if key in covered or is_url(link) or link.endswith(".md"):
            continue
        if (directory / link).exists():
            groups[heading or "Other"].append((title, link, desc))
            covered.add(key)
    if not groups:
        return None
    order = sorted(k for k in groups if k != "Subdirectories")
    if "Subdirectories" in groups:
        order = ["Subdirectories"] + order
    parts = []
    for h in order:
        lines = [f"# {h}", ""]
        for title, link, desc in sorted(groups[h], key=lambda e: e[0].lower()):
            lines.append(f"* [{title}]({link})" + (f" - {desc}" if desc else ""))
        parts.append("\n".join(lines))
    text = "\n\n".join(parts) + "\n"
    if directory == root:
        ver = okf_version or (fm or {}).get("okf_version")
        if ver:
            text = f'---\nokf_version: "{ver}"\n---\n\n' + text
    return text


def cmd_index(args) -> int:
    root = Path(args.bundle).resolve()
    dirs = sorted({p.parent for p in iter_md(root)} | {root}, key=lambda p: (-len(p.parts), str(p)))
    # ancestors of any concept dir also need an index
    for d in list(dirs):
        while d != root and d.parent not in dirs:
            d = d.parent
            dirs.append(d)
    changed = 0
    for d in sorted(set(dirs), key=lambda p: (-len(p.parts), str(p))):
        text = build_index(root, d, args.okf_version)
        if text is None:
            continue
        target = d / "index.md"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            continue
        changed += 1
        rel = target.relative_to(root).as_posix()
        if args.dry_run or args.check:
            print(f"--- would {'update' if current is not None else 'create'} {rel}")
            if args.dry_run:
                print(text)
        else:
            target.write_text(text, encoding="utf-8")
            print(f"wrote {rel}")
    if changed == 0:
        print("all index.md files up to date")
    return 1 if (args.check and changed) else 0


# ---------------------------------------------------------------- commands


def _now(args) -> datetime:
    if getattr(args, "now", None):
        dt = parse_dt(args.now)
        if dt is None:
            sys.exit(f"--now must be an ISO 8601 datetime with offset, e.g. 2026-07-01T00:00:00Z")
        return dt
    return datetime.now(timezone.utc)


def cmd_validate(args) -> int:
    root = Path(args.bundle).resolve()
    if not root.is_dir():
        sys.exit(f"{root} is not a directory")
    rep = validate(root, _now(args))
    levels = {"error": 0, "warning": 1, "info": 2}
    shown = [f for f in rep.findings if levels[f.level] <= levels[args.min_level]]
    errors = sum(f.level == "error" for f in rep.findings)
    warnings = sum(f.level == "warning" for f in rep.findings)
    if args.json:
        print(json.dumps({
            "bundle": str(root), "conformant": errors == 0,
            "errors": errors, "warnings": warnings,
            "findings": [f.__dict__ for f in shown],
        }, indent=2))
    else:
        for f in sorted(shown, key=lambda f: (levels[f.level], f.file)):
            print(f"{f.level.upper():8} {f.file}: {f.message}")
        n = sum(1 for p in iter_md(root) if p.name not in RESERVED)
        verdict = "CONFORMANT" if errors == 0 else "NOT CONFORMANT"
        print(f"\n{verdict} with OKF v0.2 §11 — {n} concepts, {errors} errors, {warnings} warnings"
              f", {len(rep.findings) - errors - warnings} info")
    if errors or (args.strict and warnings):
        return 1
    return 0


def cmd_summary(args) -> int:
    root = Path(args.bundle).resolve()
    now = _now(args)
    rows = []
    for p in iter_md(root):
        if p.name in RESERVED:
            continue
        try:
            doc = parse_file(p)
        except (ParseError, UnicodeDecodeError) as e:
            rows.append({"id": concept_id(root, p), "error": str(e)})
            continue
        fm = doc.frontmatter or {}
        row = {"id": concept_id(root, p), "type": fm.get("type"), "title": fm.get("title") or p.stem,
               "description": fm.get("description"), "tags": fm.get("tags") or []}
        row.update(derived(fm, now))
        if args.type and row["type"] != args.type:
            continue
        if args.tag and args.tag not in row["tags"]:
            continue
        rows.append(row)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
        return 0
    if not rows:
        print("no concepts")
        return 0
    hdr = ["id", "type", "status", "trust_tier", "stale", "title"]
    table = [[str(r.get(h, r.get("error", ""))) if r.get(h) is not None else "-" for h in hdr] for r in rows]
    widths = [max(len(h), *(len(t[i]) for t in table)) for i, h in enumerate(hdr)]
    widths[-1] = min(widths[-1], 60)
    print("  ".join(h.ljust(w) for h, w in zip(hdr, widths)))
    for t in table:
        print("  ".join(c[:w].ljust(w) for c, w in zip(t, widths)))
    return 0


def cmd_show(args) -> int:
    path = Path(args.file).resolve()
    doc = parse_file(path)
    fm = doc.frontmatter or {}
    root = Path(args.bundle).resolve() if args.bundle else None
    srcs = {str(s.get("id")): s for s in fm.get("sources") or [] if isinstance(s, dict) and s.get("id")}
    body = strip_code(doc.body)
    attrib = defaultdict(list)
    for para in re.split(r"\n\s*\n", body):
        for label in FOOTNOTE_REF.findall(para):
            if not para.lstrip().startswith(f"[^{label}]:"):
                attrib[label].append(" ".join(para.split())[:160])
    links = []
    for text, target in LINK.findall(body):
        entry = {"text": text, "target": target}
        if root and not is_url(target) and not target.startswith("#"):
            entry["exists"] = resolve(root, path.parent, target) is not None
        links.append(entry)
    out = {
        "file": str(path),
        "frontmatter": fm if doc.frontmatter is not None else None,
        "derived": derived(fm, _now(args)),
        "headings": headings(doc.body),
        "links": links,
        "claims_by_source": {k: {"source": srcs.get(k), "claims": v} for k, v in attrib.items()},
    }
    if fm.get("type") == "Attested Computation":
        sec = section(doc.body, "Computation")
        out["computation"] = {"file": fm.get("computation"), "inline": sec.strip() if sec else None,
                              "runtime": fm.get("runtime"), "parameters": fm.get("parameters")}
    print(json.dumps(out, indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check a bundle for OKF v0.2 conformance")
    v.add_argument("bundle")
    v.add_argument("--json", action="store_true")
    v.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    v.add_argument("--min-level", choices=["error", "warning", "info"], default="info",
                   help="hide findings below this level (default: show all)")
    v.add_argument("--now", help="evaluate staleness as of this ISO datetime")
    v.set_defaults(fn=cmd_validate)

    s = sub.add_parser("summary", help="list concepts with derived trust/lifecycle")
    s.add_argument("bundle")
    s.add_argument("--json", action="store_true")
    s.add_argument("--type", help="only concepts of this type")
    s.add_argument("--tag", help="only concepts carrying this tag")
    s.add_argument("--now")
    s.set_defaults(fn=cmd_summary)

    sh = sub.add_parser("show", help="parse one concept and print JSON")
    sh.add_argument("file")
    sh.add_argument("--bundle", help="bundle root, to check whether links resolve")
    sh.add_argument("--now")
    sh.set_defaults(fn=cmd_show)

    i = sub.add_parser("index", help="(re)generate index.md files")
    i.add_argument("bundle")
    i.add_argument("--dry-run", action="store_true", help="print what would be written")
    i.add_argument("--check", action="store_true", help="exit 1 if any index.md is out of date")
    i.add_argument("--okf-version", help='write okf_version into the root index.md, e.g. "0.2"')
    i.set_defaults(fn=cmd_index)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
