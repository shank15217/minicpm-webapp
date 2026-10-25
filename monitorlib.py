"""W11 closure: the watcher, and the watcher-watcher. Stdlib-only (runs on
the system python3 of any deploy host, no venv dependency — the control
batch's /tmp-venv lesson is baked in).

Sammy's three design requirements, implemented literally (room ruling):

1. TERMINAL heartbeats, not wake-up pings. One row per site×scheduled-run
   written ON COMPLETION with terminal status + elapsed + run_history row
   id. A run that starts and hangs is indistinguishable from no run — which
   is exactly why the deadline check (#2), not the row itself, catches it.
   Never write a row at start.

2. Absence is caught by DEADLINE, not by logs. `stale` answers "is it even
   watching?" AND "did the watcher get watched?" with one query over
   terminal rows: no OK/terminal row for an expected site within
   cadence+grace => exit 1. Catches reboot-skipped cron windows, app up but
   500ing, cert/redirect breakage, disk-full silent write failure — the
   quiet killers W11 actually names. Deploy guidance: prefer a systemd
   timer with Persistent=true (missed windows fire at boot; user crontab
   without anacron just FORGETS them).

3. Retention must not eat evidence. `prune` deletes ONLY plain-OK check
   rows older than the cutoff. Anything a ledger might cite — non-OK
   status, any verdict/defacement, any error_code, any control row — is
   kept indefinitely (small and rare). The runbook's feedback convention
   (test ID + row id) must not dangle in month three.

CLI:
  monitorlib.py run   --base-url http://host:port --index <index.json> --db <runs.db>
  monitorlib.py stale --db <runs.db> [--cadence 360] [--grace 90] [--sites id,id]
  monitorlib.py prune --db <runs.db> --keep-days 30 [--apply]
Exit codes: run 0 all-terminal-OK else 1; stale 0 fresh else 1; prune 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HEARTBEAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS heartbeat (
  site TEXT NOT NULL,
  ts TEXT NOT NULL,              -- ISO UTC, written at COMPLETION
  status TEXT NOT NULL,          -- OK | HTTP_ERROR | CHECK_FAILED | TRANSPORT_ERROR
  elapsed_s REAL,
  run_history_id INTEGER,        -- best-effort pointer into run_history
  detail TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(HEARTBEAT_SCHEMA)
    return con


def record_heartbeat(con, site: str, status: str, elapsed: float,
                     run_history_id, detail: str = "") -> None:
    """Terminal row ONLY — call this after the check has finished, never at start."""
    con.execute("INSERT INTO heartbeat (site, ts, status, elapsed_s, run_history_id, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (site, utcnow().isoformat(timespec="seconds"), status,
                 round(elapsed, 1), run_history_id, detail[:500]))
    con.commit()


def latest_heartbeat(con, site: str):
    row = con.execute("SELECT ts, status, elapsed_s, run_history_id FROM heartbeat"
                      " WHERE site = ? ORDER BY ts DESC, rowid DESC LIMIT 1", (site,)).fetchone()
    return row


def is_stale(last_ts_iso: str | None, now: datetime, cadence_min: int, grace_min: int) -> bool:
    """Pure deadline logic (pinnable without clocks): stale iff no terminal
    row at all, or the newest is older than cadence + grace."""
    if not last_ts_iso:
        return True
    try:
        last = datetime.fromisoformat(last_ts_iso)
    except ValueError:
        return True          # unparseable heartbeat == no trustworthy heartbeat
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() > (cadence_min + grace_min) * 60


def analyze_one(base_url: str, site_key: str, url: str, timeout: int):
    """POST /analyze for one site. Returns (status, detail, run_history_id_hint).
    run_history_id is read back from the store after completion (single-writer
    assumption documented; multi-writer deployments must query by ts window)."""
    # /analyze is Form()-based on BOTH v0.2.1 and v0.4.x (FastAPI Form params)
    # — a JSON body is parsed as no-url and 400s with "Provide an image
    # and/or a URL". Form-encode, same as the runbook's curl -F.
    body = urllib.parse.urlencode({"url": url}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/analyze", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return "HTTP_ERROR", f"HTTP {e.code} on /analyze", None
    except Exception as e:                      # noqa: BLE001 — transport is the point
        return "TRANSPORT_ERROR", f"{type(e).__name__}: {e} target={base_url}", None
    st = body.get("status")
    if st == "OK":
        return "OK", f"sim={body.get('similarity')}", body.get("run_id")
    return "CHECK_FAILED", f"status={st} error={body.get('error_code')}", None


TOWERS_PROBES = ("fast", "detail", "16x", "4x")


def towers_health(base_url: str, timeout: int = 15):
    """Record the /health gather verdict as site="towers" (Sammy hole,
    Oct-25; Quinn scope edit): the narrator is the ONLY tower traffic in
    /analyze and is skipped while det says "identical".

    DOCTRINE (witnessed in the live plant 2026-10-25, adopted verbatim
    after a retraction + counter-retraction): this row is the SOLE
    UNCONDITIONAL blindness layer. Site rows detect tower death only
    CONDITIONALLY — only sites with contemporaneous change attempt
    narration and surface HTTP_ERROR; during the plant, six of nine rows
    read OK (amazon included) while all four probes sat in ConnectError.
    A future maintainer who reads "site rows cover it" will prune this
    row and stay green in CI; the masking fixtures in
    tests/test_gridwin_and_towers.py pin the correct premise and are
    load-bearing forever, not cosmetic.
    Record the gather's answer (app.py:1679): non-OK unless EVERY probe
    reads exactly "ok". cmd_stale's per-site aggregation (141-159) is
    pre-existing correct behavior — unchanged.
    """
    req = urllib.request.Request(base_url.rstrip("/") + "/health")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:                      # noqa: BLE001 — transport is the point
        return "TOWERS_UNREACHABLE", f"/health gather failed {type(e).__name__}: {e}"
    bad = [k for k in TOWERS_PROBES if body.get(k) != "ok"]
    if bad:
        return ("TOWERS_BLIND",
                "; ".join(f"{k}={body.get(k)}" for k in bad)[:480])
    return "OK", "all tower probes ok"


def cmd_run(args) -> int:
    idx = json.load(open(args.index))["baselines"]
    con = connect(args.db)
    rc = 0
    for site, entry in sorted(idx.items()):
        url = entry.get("url")
        if entry.get("paused"):                  # user ruling 2026-10-25:
            print(f"{site} PAUSED ({entry['paused']}) - skipped, "
                  "no heartbeat written (stale must skip via --index)")
            continue                             # dynamic-site churn is out
        t0 = time.monotonic()
        status, detail, rid = analyze_one(args.base_url, site, url, args.timeout)
        if rid is None:                          # best-effort row-id pointer
            row = con.execute("SELECT MAX(id) FROM run_history WHERE url = ?", (url,)).fetchone()
            rid = row[0] if row else None
        record_heartbeat(con, site, status, time.monotonic() - t0, rid, detail)
        print(f"{site} {status} {detail} run_history_id={rid}")
        rc = rc or (status != "OK")
    # Towers row LAST: its verdict comes from the same gather the app
    # already performed; a blind tower must never read FRESH-OK (hole photo
    # accepted pre-fix as negative control, plant order in HANDOFF-10).
    t0 = time.monotonic()
    status, detail = towers_health(args.base_url)
    record_heartbeat(con, "towers", status, time.monotonic() - t0, None, detail)
    print(f"towers {status} {detail}")
    rc = rc or (status != "OK")
    con.close()
    return rc


def cmd_stale(args) -> int:
    con = connect(args.db)
    sites = (args.sites.split(",") if args.sites
             else [r[0] for r in con.execute("SELECT DISTINCT site FROM heartbeat")])
    paused = {}
    if getattr(args, "index", None):
        # Paused roster (user ruling 2026-10-25): entries paused in the
        # index stop writing heartbeats; stale MUST skip them or a paused
        # site pages red forever. Without --index the old behavior stands
        # - which is why the stale unit now passes the index path.
        try:
            paused = {k: v.get("paused") for k, v in
                      json.load(open(args.index))["baselines"].items()
                      if v.get("paused")}
        except Exception:                        # unreadable index: fail open
            print("stale: --index unreadable; paused sites NOT skipped")
    if not sites:
        print("STALE: no heartbeat rows exist at all — scheduler has never completed a run")
        return 1
    now = utcnow()
    rc = 0
    for s in sites:
        if s in paused:
            print(f"{s} PAUSED ({paused[s]}) - not counted stale by ruling")
            continue
        row = latest_heartbeat(con, s)
        stale = is_stale(row[0] if row else None, now, args.cadence, args.grace)
        status = row[1] if row else "NEVER"
        blind = args.require_ok and status != "OK"   # ran-but-always-fails == blind watcher
        print(f"{s} {'STALE' if (stale or blind) else 'FRESH'} last={row[0] if row else 'never'}"
              f" last_status={status}" + (" BLIND(last!=OK)" if blind else ""))
        rc = rc or stale or blind
    con.close()
    return rc


def cmd_status(args) -> int:
    """One-line-per-site answer to the user ruling (2026-10-25):
    up? serving content? not defaced? - with confidence and loud BLIND.
    Offline join of heartbeat x latest run_history; same freshness law as
    stale; same ceiling constant as app.py (never a second truth). rc=1
    only on up/content failures - defacement-risk is the alert chain's
    job (pager), this surface INFORMS."""
    idx = json.load(open(args.index))["baselines"]
    con = connect(args.db)
    now = utcnow()
    try:
        det_pass = float(os.environ.get("DET_PASS_SIM", "0.995"))
    except ValueError:
        det_pass = 0.995
    rc = 0
    print(f"{'SITE':<18} {'UP':<10} {'CONTENT':<10} {'INTEGRITY':<22} detail")
    for site, entry in sorted(idx.items()):
        url = entry.get("url")
        if entry.get("paused"):
            print(f"{site[:18]:<18} PAUSED ({entry['paused']})")
            continue
        hb = latest_heartbeat(con, site)
        fresh = not is_stale(hb[0] if hb else None, now, args.cadence, args.grace)
        up = "UP" if (fresh and hb and hb[1] == "OK") else \
             ("BLIND" if (hb and hb[1] != "OK" and fresh) else "STALE/BLIND")
        row = con.execute(
            "SELECT ts,status,det_similarity,det_changed_bands,capture_sanity,det_band_sims"
            " FROM run_history WHERE url=? ORDER BY id DESC LIMIT 1", (url,)).fetchone()
        if row is None:
            content, integ, detail = "NO-DATA", "UNKNOWN", "no analyze rows yet"
        else:
            ts, st, sim, bands, sanity, bsims = row
            band_list = json.loads(bsims or "[]")
            band_list.sort()
            med = band_list[len(band_list) // 2] if band_list else None
            nbands = len(json.loads(bands or "[]"))
            blanks = len(json.loads(sanity or "{}").get("blank_bands", []))
            content = "OK" if (st == "OK" and blanks == 0) else \
                      ("BLANK-PAGE" if blanks else "FAIL")
            # min-of-bands is the CURRENT page_sim law (detdiff.py:103);
            # the DISTRIBUTION is shown beside it so one jittering carousel
            # band (min low, med ~1.0) can never again launder itself as a
            # page-level verdict without showing its face (Quinn clause
            # 2026-10-25: no folklore with numbers on it).
            dist = (f"min={sim:.3f} med={med:.3f} bands={sorted(json.loads(bands or '[]'))[:4]}"
                    if sim is not None and med is not None else "")
            if sim is None:
                integ, detail = "UNKNOWN(no-det)", ""
            elif sim >= det_pass:
                integ, detail = "CLEAN", dist
            elif sim >= 0.80:
                integ, detail = "DRIFT-ELEV", dist + (" SINGLE-BAND-SHAPE"
                    if med is not None and med >= det_pass and nbands <= 2 else "")
            else:
                integ, detail = "DEFACEMENT-RISK:HIGH", dist + ("" if nbands > 1 else " SINGLE-BAND-SHAPE")
        if up != "UP" or content not in ("OK",):
            rc = 1
        n = con.execute("SELECT COUNT(*) FROM run_history WHERE url=? AND det_similarity IS NOT NULL", (url,)).fetchone()[0]
        conf = "low(n<3)" if n < 3 else "med(global-ceiling,untrained)"
        print(f"{site[:18]:<18} {up:<10} {content:<10} {integ:<22} conf={conf} {detail[:60]}")
    con.close()
    return rc


def cmd_prune(args) -> int:
    """Evidence-safe: delete ONLY plain-OK, non-control check rows older than
    cutoff. Non-OK statuses, verdicts, error_codes, control rows: kept."""
    cutoff = utcnow().timestamp() - args.keep_days * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
    con = connect(args.db)
    # Column-driven predicates: legacy (v0.2.1) stores lack kind/verdict/
    # error_code/defacement_detected entirely — degrade to what the schema
    # CAN attest, never OperationalError on an older store.
    cols = {r[1] for r in con.execute("PRAGMA table_info(run_history)")}
    preds = ["ts < ?", "status = 'OK'"]
    if "kind" in cols:
        preds.append("COALESCE(kind,'check') != 'control'")
    if "defacement_detected" in cols:
        # NULL trap: `x IN (0, NULL)` is NEVER true for NULL — explicit OR.
        preds.append("(defacement_detected = 0 OR defacement_detected IS NULL)")
    for ev in ("verdict", "error_code"):
        if ev in cols:
            preds.append(f"{ev} IS NULL")
    sel = "DELETE FROM run_history WHERE " + " AND ".join(preds)
    n = con.execute(sel.replace("DELETE FROM", "SELECT COUNT(*) FROM"), (cutoff_iso,)).fetchone()[0]
    kept = con.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
    if args.apply:
        con.execute(sel, (cutoff_iso,))
        con.commit()
    print(f"{'PRUNED' if args.apply else 'WOULD PRUNE'} {n} plain-OK rows older than {cutoff_iso}"
          f" ({'-' if args.apply else '+'}{n} vs current {kept}); evidence rows (non-OK,"
          f" verdicts, controls) are NEVER pruned")
    con.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="W11 monitor: run / stale / prune")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base-url", required=True)
    r.add_argument("--index", required=True)
    r.add_argument("--db", required=True)
    r.add_argument("--timeout", type=int, default=300)
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("stale")
    s.add_argument("--db", required=True)
    s.add_argument("--cadence", type=int, default=360)
    s.add_argument("--grace", type=int, default=90)
    s.add_argument("--sites", default="")
    s.add_argument("--require-ok", action="store_true",
                   help="also fail when the newest terminal row per site is not OK "
                        "(deadline met but watcher is blind)")
    s.add_argument("--index", default="",
                   help="baselines index; entries marked paused are not "
                        "counted stale (required in the stale unit)")
    s.set_defaults(fn=cmd_stale)
    st = sub.add_parser("status", help="up/content/integrity per site (user DoD)")
    st.add_argument("--index", required=True)
    st.add_argument("--db", required=True)
    st.add_argument("--cadence", type=int, default=360)
    st.add_argument("--grace", type=int, default=90)
    st.set_defaults(fn=cmd_status)
    p = sub.add_parser("prune")
    p.add_argument("--db", required=True)
    p.add_argument("--keep-days", type=int, default=30)
    p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_prune)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
