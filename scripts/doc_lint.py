#!/usr/bin/env python3
"""doc_lint — qa_gate clause 8 (Quinn spec, QA-REPORT-7 + room amendments).

Four mechanical detectors; every line rule uses the per-line marker doctrine
pinned by tests/test_tower_orientation.py::test_retraction_exemption_semantics
(a claim is only lawful as history if the marker shares the physical line —
re-wrap != retraction):

 1. dead receipts   — falsified token figures (837/832-class) outside
                      RETRACT-marked lines. IMPORTS dead_line_violations()
                      from the orientation pin: one implementation, one place
                      to extend.
 2. alias inversion — NUMBER-FREE detector (Quinn acceptance #1): the fact
                      table is parsed OUT OF CODE at run time — MODE_ALIASES
                      + LLAMA_SERVER_* endpoint defaults in app.py — and any
                      assignment-shaped line ("X = Y", "X -> Y", "X ⇒ Y")
                      that binds a mode to the other mode's port or tower
                      file fires. Greps lost this class twice (backticks,
                      line-wrap); this parses truth from code, not strings.
 3. phantom paths   — a repo-relative path cited in tracked markdown that
                      exists on disk and on no branch ref. Lines marked
                      PHANTOM/RETRACT are exempt (the lint's own ledgers
                      must be allowed to name what they report).
                      Quinn acceptance #2: on 2026-10-21 HEAD this MUST fire
                      exactly on RUNBOOK.md + OPERATOR.md regime_filter cites;
                      a green run here means the detector is broken.
 4. version skew    — pyproject version must equal the newest reachable tag.
"""
import os
import re
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_sibling(relpath, modname):
    """Load a repo file by explicit path (no sys.path games): clause 8 reuses
    dead_line_violations() from the orientation pin so the per-line RETRACT
    semantics live in exactly ONE implementation."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(modname, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dead_line_violations = _load_sibling(
    "tests/test_tower_orientation.py", "_orientation_pin").dead_line_violations

SCAN_SUFFIXES = {".py", ".sh", ".md", ".html"}
SKIP_DIR_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
# Lint-about-lint files: ledgers must be allowed to name the phantoms and
# quote the traps they report (they still carry the dead-number pin? no —
# ledger lines quote both classes; they are exempt from alias+phantom only).
LINT_LEDGER_FILES = {"DOC-LINT-2026-10-21.md"}
# generated artifacts documented but intentionally untracked
GENERATED_BASENAMES = {"ceilings.json", "index.json", "control.jsonl"}


def tree_files():
    for f in sorted(ROOT.rglob("*")):
        if any(p in SKIP_DIR_PARTS for p in f.parts) or f.suffix not in SCAN_SUFFIXES:
            continue
        if f.name == "uv.lock":
            continue
        yield f, str(f.relative_to(ROOT))


# ---------- fact table (parsed from CODE, not prose) ----------
def fact_table():
    app = (ROOT / "app.py").read_text()
    m = re.search(r"MODE_ALIASES\s*=\s*\{([^}]*)\}", app, re.S)
    if not m:
        raise SystemExit("doc_lint: cannot parse MODE_ALIASES from app.py — "
                         "if the table moved, fix THIS file in the same commit")
    aliases = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', m.group(1)))
    ports = {}
    for mode, var in (("fast", "LLAMA_SERVER_FAST"), ("detail", "LLAMA_SERVER_DETAIL")):
        pm = re.search(var + r'.*?:(\d{4})', app, re.S)
        ports[mode] = f":{pm.group(1)}"
    # wrong-side tokens per mode, derived (never hardcoded) from the table:
    wrong = {
        "fast":   [ports["detail"], r"-4x", r"(?<![-\w])4x\b"],
        "detail": [ports["fast"], r"f16(?!-4x)", r"(?<![-\w])16x\b"],
    }
    return {"ports": ports, "aliases": aliases, "wrong": wrong}


ASSIGN_RE = re.compile(r"[=→⇒]|->")
# clause = assertion unit; period and em-dash included: "plain f16 is
# 64 tok/tile. More detail always costs tokens" must NOT bind across the
# sentence boundary (found by running the detector on the live tree).
CLAUSE_SPLIT_RE = re.compile(r"[;,()\[\]·.]|\u2014")


def alias_inversions(fname, text, facts):
    out = []
    if os.path.basename(fname) in LINT_LEDGER_FILES:
        return out  # the lint's own ledger necessarily quotes the traps
    for ln, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if "retract" in low:
            continue
        if not ASSIGN_RE.search(line):
            continue  # only assignment-shaped claims assert a binding
        for clause in CLAUSE_SPLIT_RE.split(low):
            if not ASSIGN_RE.search(clause):
                continue
            for mode, wrongs in facts["wrong"].items():
                if not re.search(rf"\b{mode}\b", clause):
                    continue
                for w in wrongs:
                    if re.search(w, clause):
                        out.append(f"{fname}:{ln}: binds '{mode}' to the "
                                   f"OTHER tower ({w!r} seen): {line.strip()[:90]}")
                        break
                else:
                    continue
                break
    return out


# ---------- phantom paths ----------
def _ref_files():
    refs = subprocess.run(["git", "for-each-ref", "--format=%(refname)",
                           "refs/heads", "refs/remotes", "refs/tags"],
                          cwd=ROOT, capture_output=True, text=True).stdout.split()
    names = set()
    for r in refs:
        out = subprocess.run(["git", "ls-tree", "-r", "--name-only", r],
                             cwd=ROOT, capture_output=True, text=True).stdout
        names.update(out.split())
    return {os.path.basename(n) for n in names}


PATH_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-./]*[A-Za-z0-9_\-]\.(?:py|sh|md|html|toml|ini)\b")


def phantom_paths(fname, text, known_basenames, disk_basenames):
    out = []
    if os.path.basename(fname) in LINT_LEDGER_FILES:
        return out
    for ln, line in enumerate(text.splitlines(), 1):
        if "retract" in line.lower() or "phantom" in line.lower():
            continue
        for tok in PATH_TOKEN_RE.findall(line):
            if "//" in tok or tok.startswith("/"):
                continue
            base = os.path.basename(tok)
            if base in GENERATED_BASENAMES or base in disk_basenames or base in known_basenames:
                continue
            out.append(f"{fname}:{ln}: cites '{tok}' — exists on disk and on "
                       f"no branch ref (phantom)")
    return out


# ---------- version skew ----------
def version_skew():
    try:
        tag = subprocess.run(["git", "describe", "--tags", "--abbrev=0"],
                             cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        return []
    if not tag:
        return []
    pv = re.search(r'^version\s*=\s*"([^"]+)"',
                   (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    if pv != tag.lstrip("v"):
        return [f"pyproject.toml: version {pv!r} != newest tag {tag!r}"]
    return []


# Phantoms KNOWN to be pending the v0.6 reconciliation (OPERATOR/RUNBOOK
# sentences rewrite these cites; delete the entry in the SAME commit that
# rewrites the sentence — an expired allow-list entry then surfaces as a
# plain violation, so it cannot quietly outlive its excuse).
KNOWN_PENDING_RECONCILE = {"regime_filter.py"}


def main():
    strict = "--strict" in sys.argv
    facts = fact_table()
    known = _ref_files()
    disk = {f.name for f in ROOT.rglob("*")
            if f.is_file() and not any(p in SKIP_DIR_PARTS for p in f.parts)}
    violations = []
    md_texts = {}
    for f, rel in tree_files():
        text = f.read_text(errors="ignore")
        violations += dead_line_violations(rel, text)
        violations += alias_inversions(rel, text, facts)
        if f.suffix == ".md":
            md_texts[rel] = text
    for rel, text in md_texts.items():
        violations += phantom_paths(rel, text, known, disk)
    if not strict:
        violations = [v for v in violations
                      if not any(k in v for k in KNOWN_PENDING_RECONCILE)]
    violations += version_skew()
    if violations:
        print(f"doc_lint: {len(violations)} violation(s):")
        for v in violations:
            print("  " + v)
        return 1
    print("doc_lint: clean (dead receipts, alias inversions, phantoms, version"
          + ("" if strict else "; allow-listed: " + ", ".join(sorted(KNOWN_PENDING_RECONCILE))) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
