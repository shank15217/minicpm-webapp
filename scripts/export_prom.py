#!/usr/bin/env python3
"""export_prom — Prometheus textfile for the monitor (user metrics law,
2026-10-27): (1) TLS certificate facts with change recording, (2) the
vision tower's description as a readable-over-time ledger (full text in
state.toml; prom carries the current one + its run id), (3) a numeric
confidence (det_similarity vs the PINNED baseline = unscheduled-change
signal) and the fixed state labels {OK, MODIFIED, DEFACED, PENDING,
BLIND, DOWN}. Written to metrics/metrics.prom by run_daily.sh; app.py
serves the same file at GET /metrics for scrapers.

State law (exactly one state=1 per site):
  DOWN     heartbeat stale/not-OK          BLIND   eye or store blind
  DEFACED  newest live verdict = DEFACED   MODIFIED sim < pass ceiling
  PENDING  no delta yet                    OK      everything else
"""
import argparse
import json
import os
import socket
import sqlite3
import ssl
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from monitorlib import connect, live_pred, is_stale, latest_heartbeat, utcnow  # noqa: E402

DET_PASS = float(os.environ.get("DET_PASS_SIM", "0.995"))


def parse_der(der: bytes) -> dict:
    """Facts from raw DER without trust assumptions: decode with ssl's own
    parser (getpeercert() is empty under CERT_NONE and lab certs are
    self-signed). Real-world shapes are pinned as fixtures in
    tests/fixtures/certs/ after three prod first-run crashes taught us
    issuer nesting the slow way (Sammy law: the real world files its own
    bug reports)."""
    import hashlib
    d = {}
    try:
        import ssl as _ssl
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as tf:
            tf.write(ssl.DER_cert_to_PEM_cert(der))
            pem_p = tf.name
        d = _ssl._ssl._test_decode_cert(pem_p) or {}
        os.unlink(pem_p)
    except Exception:  # noqa: BLE001
        pass
    exp = None
    try:
        exp = datetime.strptime(d["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        pass
    iss = d.get("issuer") or ()
    issuer_val = "unknown"
    try:  # issuer = RDN sequences; the useful name may sit in ANY RDN
        # (LE carries O in the 2nd) — merge all pairs, prefer O then CN
        pairs = {}
        for rdn in iss:
            pairs.update(dict(rdn))
        issuer_val = pairs.get("organizationName") or pairs.get("commonName") \
            or (list(pairs.values())[-1] if pairs else "unknown")
    except (ValueError, IndexError, TypeError):
        pass
    return {"fingerprint": hashlib.sha256(der).hexdigest(),
            "expiry": exp.isoformat() if exp else "",
            "expiry_ts": exp.timestamp() if exp else 0,
            "issuer": issuer_val,
            "serial": d.get("serialNumber", "")}


def cert_facts(url: str, timeout: int = 10) -> dict:
    """Peer cert the server ACTUALLY serves (unverified handshake — this is
    an identity ledger, not a trust verdict)."""
    if not url.startswith("https"):
        return {}
    hostport = url.split("//", 1)[-1].split("/", 1)[0]
    host, _, port = hostport.partition(":")
    port = int(port or 443)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as s:
            der = s.getpeercert(True)
    if not der:
        return {}
    return parse_der(der)


def cert_history_update(con, site: str, facts: dict) -> tuple[int, int]:
    """Record changes: returns (changed_now 0/1, days_since_last_change)."""
    con.execute("CREATE TABLE IF NOT EXISTS cert_history (site TEXT, ts TEXT,"
                " fingerprint TEXT, expiry TEXT, issuer TEXT, serial TEXT)")
    if not facts.get("fingerprint"):
        return 0, -1
    last = con.execute("SELECT fingerprint, ts FROM cert_history WHERE site=?"
                       " ORDER BY rowid DESC LIMIT 1", (site,)).fetchone()
    changed, since = 0, -1
    now = datetime.now(timezone.utc)
    if last is None or (last[0] != facts["fingerprint"]):
        changed = 1
        con.execute("INSERT INTO cert_history VALUES (?,?,?,?,?,?)",
                    (site, now.isoformat(), facts["fingerprint"], facts["expiry"],
                     facts["issuer"], facts["serial"]))
        con.commit()
        last2 = con.execute("SELECT ts FROM cert_history WHERE site=?"
                            " ORDER BY rowid DESC LIMIT 2", (site,)).fetchall()
        if len(last2) == 2:
            prev = datetime.fromisoformat(last2[1][0])
            since = max(0, int((now - prev).total_seconds() // 86400))
    else:
        prev = datetime.fromisoformat(last[1])
        since = int((now - prev).total_seconds() // 86400)
    return changed, since


def esc(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default=os.path.join(ROOT, "baselines", "index.json"))
    ap.add_argument("--db", default=os.path.join(ROOT, "baselines", "runs.db"))
    ap.add_argument("--out", default=os.path.join(ROOT, "metrics", "metrics.prom"))
    ap.add_argument("--cadence", type=int, default=360)
    ap.add_argument("--grace", type=int, default=90)
    a = ap.parse_args()
    idx = json.load(open(a.index))["baselines"]
    con = connect(a.db)
    con.row_factory = sqlite3.Row
    lp = live_pred(con)
    latest = {r["url"]: r for r in con.execute(
        "SELECT * FROM run_history WHERE 1" + lp + " ORDER BY id")}
    sims = {r["url"]: r for r in con.execute(
        "SELECT * FROM run_history WHERE det_similarity IS NOT NULL" + lp + " ORDER BY id")}
    vers = {r["url"]: r for r in con.execute(
        "SELECT * FROM run_history WHERE verdict IS NOT NULL AND verdict!=''"
        + lp + " ORDER BY id")}
    desc = {}
    try:
        desc = {r["url"]: r for r in con.execute(
            "SELECT * FROM run_history WHERE kind='check' AND raw_output IS NOT NULL"
            + lp + " ORDER BY id")}
    except sqlite3.OperationalError:
        pass  # legacy store without raw_output: ledger metric is skipped, not fatal
    now = utcnow()
    L = []

    def line(m, labels, v, help_=None):
        if help_:
            L.append(f"# HELP {m} {help_}")
        L.append(f"{m}{{{labels}}} {v}")

    line("mpw_scrape_ok", "instance=\"mpw\"", 1, "exporter completed a pass")
    # Sammy law (metrics review 2026-10-27): an exporter pass is just reading
    # a file — scrape_ok says ALIVE, it does not say FRESH. The freshness
    # claim must be a SERIES or it does not exist at scrape time: this gauge
    # is set from the newest live data timestamp (last analyze/eye/heartbeat
    # row), NOT the file mtime — a healthy-looking exporter over dead data
    # is precisely the tidy-corpse failure this kills.
    data_ts = 0
    try:
        row = con.execute("SELECT MAX(id) FROM run_history WHERE 1" + lp).fetchone()
        if row and row[0]:
            r2 = con.execute("SELECT ts FROM run_history WHERE id=?", (row[0],)).fetchone()
            if r2:
                data_ts = int(datetime.fromisoformat(r2[0]).timestamp())
    except sqlite3.OperationalError:
        pass
    line("mpw_data_last_run_timestamp_seconds", "instance=\"mpw\"", data_ts,
         "newest live run_history row; alert time()-this > 2*cadence, plus "
         "absent(...) for: 48h for never-exported (Sammy absent-pair law)")
    for key, meta in sorted(idx.items()):
        url = meta.get("url", "")
        site = key[:16]
        lab = f'site="{site}",url="{esc(url)}"'
        paused = bool(meta.get("paused"))
        if paused:
            line("mpw_site_paused", lab, 1, "1 = excluded from monitoring by ruling")
            continue
        hb = latest_heartbeat(con, key)
        fresh = not is_stale(hb[0] if hb else None, now, a.cadence, a.grace)
        up = bool(hb and fresh and hb[1] == "OK")
        line("mpw_site_up", lab, 1 if up else 0, "heartbeat fresh and OK")
        sim = sims.get(url)
        conf = sim["det_similarity"] if sim else None
        if isinstance(conf, float):
            line("mpw_site_confidence", lab, f"{conf:.4f}",
                 "det_similarity vs PINNED baseline: unscheduled-change signal")
        vrow = vers.get(url)
        verdict = vrow["verdict"] if vrow else None
        # Ceiling provenance (metrics review 2026-10-27): MODIFIED is only
        # as honest as the number it was judged against. Untrained sites
        # carry the global default and say so — calibrated=0 means "the
        # threshold is folklore, the confidence gauge is the signal":
        # alert MODIFIED only `and on(site) mpw_site_calibrated == 1`.
        ceiling = meta.get("ceiling")
        calibrated = 1 if isinstance(ceiling, float) else 0
        thr = ceiling if isinstance(ceiling, float) else DET_PASS
        line("mpw_site_ceiling", lab, f"{thr:.4f}",
             "the numeric MODIFIED was judged against")
        line("mpw_site_calibrated", lab, calibrated,
             "1 = per-site trained ceiling; 0 = global default (do not page on MODIFIED)")
        if verdict == "DEFACED":
            state = "DEFACED"
        elif verdict in ("BLIND-JUDGE", "CANNOT_TELL"):
            state = "BLIND"
        elif hb is not None and not up:
            state = "DOWN"
        elif conf is None:
            state = "PENDING"
        elif conf < thr:
            state = "MODIFIED"
        else:
            state = "OK"
        for s in ("OK", "MODIFIED", "DEFACED", "PENDING", "BLIND", "DOWN"):
            line("mpw_site_state", f'{lab},state="{s}"', 1 if state == s else 0,
                 "exactly one state=1 per site")
        if vrow:
            line("mpw_site_verdict_run_id", lab, vrow["id"],
                 "status --verify <id> prints provenance")
        d = desc.get(url)
        if d is not None and d["raw_output"]:
            # word-boundary cut (Quinn): "with a lo…" reads like a bug;
            # a cut at whitespace reads like a summary
            snip = str(d["raw_output"])[:180].rsplit(" ", 1)[0]
            line("mpw_site_description", f'{lab},description="{esc(snip)}"',
                 d["id"], "vision-tower ledger; full history in state.toml")
        if url.startswith("https"):
            try:
                facts = cert_facts(url)
            except Exception as e:  # noqa: BLE001
                facts = {}
                L.append(f"# cert-fetch-error {site} {esc(str(e))[:80]}")
            if facts.get("fingerprint"):
                cl = (f'site="{site}",url="{esc(url)}",'
                      f'fingerprint="{facts["fingerprint"][:32]}",'
                      f'issuer="{esc(facts["issuer"])[:80]}",'
                      f'serial="{esc(facts["serial"])}"')
                line("mpw_cert_info", cl, 1,
                     "current TLS cert identity (fingerprint=first32 of sha256 DER)")
                changed, since = cert_history_update(con, site, facts)
                line("mpw_cert_changed", lab, changed, "1 if cert differs from ledger")
                if since >= 0:
                    line("mpw_cert_age_days", lab, since, "days since last cert change")
                if facts["expiry_ts"]:
                    line("mpw_cert_expiry_timestamp_seconds", lab,
                         int(facts["expiry_ts"]), "notAfter as unix seconds")
                    days = int((facts["expiry_ts"] - now.timestamp()) // 86400)
                    line("mpw_cert_days_remaining", lab, days, "days until expiry")
    con.close()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    open(tmp, "w").write("\n".join(L) + "\n")
    os.replace(tmp, a.out)
    print(f"metrics.prom written: {len([x for x in L if not x.startswith('#')])} series")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
