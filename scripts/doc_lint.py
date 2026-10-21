#!/usr/bin/env python3
"""doc_lint — qa_gate clause 8 (Quinn spec QA-REPORT-7 + gap-round amendments).

Four mechanical detectors; per-line marker doctrine pinned by
tests/test_tower_orientation.py::test_retraction_exemption_semantics
(marker must share the physical line — re-wrap != retraction).

 1. dead receipts   — falsified token figures (837/832-class) outside
                      RETRACT-marked lines. IMPORTS dead_line_violations()
                      from the orientation pin: one implementation.
 2. alias inversion — NUMBER-FREE (Quinn acceptance #1). Fact table parsed
                      OUT OF CODE (MODE_ALIASES + LLAMA_SERVER_* defaults).
                      A window fires when it carries a SUBJECT for one mode
                      (its name OR any MODE_ALIASES alias — all six keys
                      resolve, gap 5) plus a CLAIM token that can only mean
                      the other side (its port, its file marker), or both
                      mode names bound in one clause. Windows: prose = clause
                      with binding context; markdown table = WHOLE ROW
                      (cell-adjacency IS the binding syntax — splitting
                      cells would make every table inversion silently
                      self-silence, Sammy's fake-green trap). RETRACT-marked
                      lines run with quoted/backticked spans STRIPPED
                      (Quinn: a retraction may cite a binding, never assert
                      one — a bare assertion survives stripping and still
                      fires; quote-EXEMPTION was rejected, the laundered
                      tail walks it). Shell selector windows (&& || $( )
                      skipped: an echo ladder is not a mapping claim.
                      Ambiguous English aliases (ocr/general/bare 4x/16x)
                      only ever fire together with a wrong-side claim token
                      inside a binding window — prose like "a general
                      speedup of 2x" stays silent (no wolf, no policy
                      override).
 3. phantom paths   — repo-relative path cited in tracked markdown, present
                      on neither disk nor any branch ref.
 4. version skew    — pyproject version vs newest reachable tag.

Scope boundary (Sammy SINC-1, declared, not bug): no per-line rule can tell
a sincere RETRACT marker from an insincere one. RETRACT-marked lines are
diff-review surface; keep the token exact and the marker line count greppable.
"""
import os
import re
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_sibling(relpath, modname):
    """Load a repo file by explicit path (no sys.path games): clause 8 and
    the orientation pin share ONE implementation of the per-line RETRACT
    semantics."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(modname, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dead_line_violations = _load_sibling(
    "tests/test_tower_orientation.py", "_orientation_pin").dead_line_violations

SCAN_SUFFIXES = {".py", ".sh", ".md", ".html"}
SKIP_DIR_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
# Lint-about-lint ledgers must be allowed to name phantoms and quote traps.
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
    other = {"fast": "detail", "detail": "fast"}
    # OWN-side claim tokens: a token occurrence claims its side; it FIRES
    # only when the nearest SUBJECT (mode name/alias) is the other side.
    # (Set-based "both sides appear" was wrong: 'fast = f16 = :8080 ·
    # detail = -4x = :8081' is two TRUE bindings sharing one window.)
    own = {"fast": [ports["fast"], r"f16(?!-4x)"],
           "detail": [ports["detail"], r"-4x"]}
    return {"ports": ports, "aliases": aliases, "other": other, "own": own}


# clause = assertion unit; period/em-dash included: "plain f16 is
# 64 tok/tile. More detail always costs tokens" must NOT bind across the
# sentence boundary (found by running the detector on the live tree).
CLAUSE_SPLIT_RE = re.compile(r"[;,()\[\]·.]|\u2014")
# binding context for prose windows; table rows are binding BY CONSTRUCTION
BIND_RE = re.compile(r"[=→⇒]|->|\bis\b|\broutes?\b|\bmaps?\b|\bdefaults?\b"
                     r"|\bbound?\b|\bmode\b|\btower\b|\bport\b|\bfile\b"
                     r"|\bsends?\b|\bsending\b|\bpoints?\b|\bpointe?[drs]?\b"
                     r"|\bredirect\w*\b|\bserves?\b|\bserved\b")
COPULA_RE = re.compile(r"\bis the\b|[=→]|\b->|\broutes to\b|\bmaps to\b")
# "detail|fast" alternation lists both modes without equating them
ALTERNATION_RE = re.compile(r"\b(fast|detail|ocr|general|16x|4x)\s*\|\s*(fast|detail|ocr|general|16x|4x)\b")
# a claim binds to its nearest subject within this many characters
NEAR_BIND = 40
SHELL_WINDOW_RE = re.compile(r"&&|\|\||\$\(")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def strip_quoted(text):
    """Per-line text with quoted/backticked spans removed, quote state
    tracked ACROSS lines (markdown citations wrap). Used on RETRACT-marked
    lines: the inversion detector then sees only what the line ASSERTS."""
    out, in_dq, in_bt = [], False, False
    for line in text.splitlines():
        keep, i = [], 0
        while i < len(line):
            c = line[i]
            if c == '"' and line[i:i + 3] == '"""':
                i += 3                       # python triple-quote: opaque
                continue
            if in_dq:
                if c == '"':
                    in_dq = False
            elif in_bt:
                if c == "`":
                    in_bt = False
            elif c == '"':
                in_dq = True
            elif c == "`":
                in_bt = True
            else:
                keep.append(c)
            i += 1
        out.append("".join(keep))
    return out


JOIN_RE = re.compile(r"\s+and\s+|\s+vs\.?\s+|&")


def _binding_spans(window):
    """(offset, text) pieces split on ' and ' / ' vs ' / '&' — two bindings
    joined by a conjunction ("the fast tower runs on :8080 and detail on
    :8081") are two assertions; pairing must not leak across the join."""
    spans, last = [], 0
    for m in JOIN_RE.finditer(window):
        spans.append((last, window[last:m.start()]))
        last = m.end()
    spans.append((last, window[last:]))
    return spans


def _window_hit(window, facts, row_window=False):
    if not window.strip() or SHELL_WINDOW_RE.search(window):
        return None
    window = ALTERNATION_RE.sub(" " * 2, window)
    subs = []                        # (pos, canon) for every name/alias
    for w, canon in facts["aliases"].items():
        for m in re.finditer(r"(?<![-\w])" + re.escape(w) + r"\b", window):
            subs.append((m.start(), canon))
    if not subs:
        return None                  # receipts naming both ports with no
    if not row_window and not BIND_RE.search(window):
        return None                  # subject stay prose in a prose window
    for off, span in _binding_spans(window):
        span_subs = [(p, c) for p, c in subs if off <= p < off + len(span)]
        if not span_subs:
            continue
        for side, toks in facts["own"].items():
            for t in toks:
                for m in re.finditer(t, span):
                    abs_pos = off + m.start()
                    s_pos, s_mode = min(span_subs, key=lambda s: abs(s[0] - abs_pos))
                    if abs(s_pos - abs_pos) <= NEAR_BIND and s_mode != side:
                        return (f"binds '{s_mode}' to the {side} side "
                                f"({t!r} nearest-subject pairing)")
    if not row_window and {"fast", "detail"} <= {c for _, c in subs} \
            and COPULA_RE.search(window) and not JOIN_RE.search(window):
        # both mode names bound in one clause WITHOUT a conjunction between
        # them ("the 4x mode is the fast tower"); '| mode | fast | detail |'
        # table headers and "fast … and detail …" listings are exempt
        return "asserts fast and detail bound in the same clause"
    return None


def alias_inversions(fname, text, facts):
    out = []
    if os.path.basename(fname) in LINT_LEDGER_FILES or fname == "scripts/doc_lint.py":
        # ledgers quote the traps; the detector's own source IS the vocabulary
        return out
    stripped = strip_quoted(text)
    for ln, line in enumerate(text.splitlines(), 1):
        # Quinn formulation (gap 4): on RETRACT-marked lines run over the
        # line with citations STRIPPED — bare assertions survive stripping
        # and fire; cited retractions dissolve. NOT quote-exemption.
        eff = (stripped[ln - 1] if "retract" in line.lower()
               else line).lower()
        row_window = bool(TABLE_ROW_RE.match(line))
        windows = [eff] if row_window else CLAUSE_SPLIT_RE.split(eff)
        for win in windows:
            if hit := _window_hit(win, facts, row_window):
                out.append(f"{fname}:{ln}: {hit}: {line.strip()[:90]}")
                break                    # one entry per line (Sammy dedupe)
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


# Name AND count scoped (Quinn gap 3): gate mode tolerates AT MOST `count`
# hits per key — observed > recorded fails the gate on the surplus, so a
# NEW doc citing regime_filter.py cannot launder through the window. The
# entry dies with the reconciliation commit that rewrites the sentences.
KNOWN_PENDING_RECONCILE = {"regime_filter.py": 2}


def apply_allowlist(violations):
    counts = {}
    for v in violations:
        for k in KNOWN_PENDING_RECONCILE:
            if k in v:
                counts[k] = counts.get(k, 0) + 1
    remaining = [v for v in violations
                 if not any(k in v and counts[k] <= KNOWN_PENDING_RECONCILE[k]
                            for k in KNOWN_PENDING_RECONCILE)]
    for k, c in counts.items():
        if c > KNOWN_PENDING_RECONCILE[k]:
            remaining.append(f"doc_lint: allow-list EXCEEDED for {k!r}: "
                             f"{c} cites observed, {KNOWN_PENDING_RECONCILE[k]} "
                             f"recorded — new cite(s) under a pending-reconcile "
                             f"entry; reconcile the doc or amend the pin (with "
                             f"a reason, in review)")
    return remaining


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
        violations = apply_allowlist(violations)
    violations += version_skew()
    if violations:
        print(f"doc_lint: {len(violations)} violation(s):")
        for v in violations:
            print("  " + v)
        return 1
    print("doc_lint: clean" + ("" if strict else
          " (allow-listed within count: "
          + ", ".join(f"{k}×{n}" for k, n in sorted(KNOWN_PENDING_RECONCILE.items())) + ")"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
