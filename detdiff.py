"""detdiff — deterministic band comparison: the layer that owns WHETHER.

QA-signed architecture (QA-REPORT-5, Acts 1-6): a 1B VLM pairwise
same-or-different judgment is prompt-pragmatics-corrupted (strict wording
confabulates differences on identical pages; permissive wording launders
blanked pages to score 100). Pixel comparison neither confabulates nor
obeys prompts: identical input scores exactly 1.0, every time, with no GPU.

So: this module answers *did anything change* (deterministic, always run);
the VLM is demoted to narrating regions the diff already located, and is
skipped entirely when nothing changed — which also kills the alert-flood
and most of the inference cost.

Per-band dHash-64 (structural) combined with normalized MAE on a 256px
downsample (luminance shift), per band: sim = min(hash_sim, mae_sim).
Page score = MIN over bands (worst band is the page). Overlap between
bands so a defacement straddling a cut is fully visible in one band.

Threshold: DET_PASS_SIM env, default 0.995. That number is a PLACEHOLDER
pending the v0.5 calibration control runs (per-baseline, per-band control
profiles — Sammy/Quinn sign-off conditions); what is proven at commit time
is ordering and determinism, not the cut-point: identical 1.000,
half-white 0.259, band-blank 0.662, 100px banner 0.969 on the production
fhlb baseline.
"""
from __future__ import annotations

import base64
import io
import json
from PIL import Image, ImageChops, ImageStat

BAND_H = 1280
BAND_OVERLAP = 64


def bands(h: int) -> list[tuple[int, int]]:
    """Horizontal (y0, y1) bands covering [0, h) with interior overlap."""
    out: list[tuple[int, int]] = []
    y = 0
    while y < h:
        y2 = min(h, y + BAND_H)
        out.append((max(0, y - BAND_OVERLAP) if y > 0 else 0, y2))
        if y2 >= h:
            break
        y = y2
    return out


def dhash(img: Image.Image, size: int = 8) -> int:
    """64-bit difference hash on an (size+1)xsize gradient resize."""
    g = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = g.tobytes()  # L-mode: one byte per pixel, row-major
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (
                1 if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else 0
            )
    return bits


def hamming_sim(a: int, b: int) -> float:
    return 1.0 - bin(a ^ b).count("1") / 64.0


def mae_sim(a: Image.Image, b: Image.Image, sample: int = 256) -> float:
    """1 - mean-abs-error/255 on a fixed-size grayscale downsample."""
    ga = a.convert("L").resize((sample, sample), Image.Resampling.LANCZOS).tobytes()
    gb = b.convert("L").resize((sample, sample), Image.Resampling.LANCZOS).tobytes()
    d = sum(abs(x - y) for x, y in zip(ga, gb)) / (sample * sample)
    return 1.0 - d / 255.0


def _open(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def page_diff(baseline_bytes: bytes, live_bytes: bytes,
              threshold: float = 0.995) -> dict:
    """Deterministic change verdict between two page screenshots.

    Returns {page_sim, changed: bool, threshold, band_sims, changed_bands,
    n_bands}. changed_bands lists "y0-y1" windows with sim < threshold —
    the exact regions the narration VLM (and any human alert) should look
    at. Pure CPU, deterministic: same inputs -> same numbers, always.
    """
    a = _open(baseline_bytes)
    b = _open(live_bytes)
    # Height mismatch is itself a change (page grew/shrank/collapsed).
    h = min(a.size[1], b.size[1])
    band_sims: list[float] = []
    changed_bands: list[str] = []
    for y0, y1 in bands(h):
        ba = a.crop((0, y0, a.size[0], y1))
        bb = b.crop((0, y0, b.size[0], y1))
        hs = hamming_sim(dhash(ba), dhash(bb))
        ms = mae_sim(ba, bb)
        s = min(hs, ms)
        band_sims.append(round(s, 4))
        if s < threshold:
            changed_bands.append(f"{y0}-{y1}")
    page_sim = min(band_sims) if band_sims else 1.0
    changed = bool(changed_bands) or abs(a.size[1] - b.size[1]) > 2 * BAND_OVERLAP
    # (b)-lite aligned rematch (calibration-protocol 6.5, Paul's ruling:
    # (a)+(b)-lite): for FLAGGED bands only, re-compare the live band window
    # against baseline windows at +/- one band offset and keep the BEST sim.
    # Sub-flag content insertion shifts every downstream band; without this,
    # changed_bands over-reports "everything below the insertion" and the
    # narrator crops point at innocent pixels.
    # HARD GUARDRAIL (QA, non-negotiable): aligned sims NEVER feed `changed`
    # or `page_sim` above — the alert consumes the RAW page-min only.
    # "Keep the best sim" is a maximization direction and every laundering
    # story in this project started with maximizing agreement; aligned sims
    # are admitted solely for localization, narrator-crop selection, and
    # tolerance distributions (calibration 2 stores both channels).
    aligned_band_sims = list(band_sims)
    align_offsets = [0] * len(band_sims)
    band_list = bands(h)
    # Search radius per Sammy's spec: sub-flag drift is <= 2*BAND_OVERLAP px,
    # so scan that range on a 32px grid. (A first cut used +/-BAND_H offsets;
    # the synthetic-insertion test showed a 100px shift is aligned by NO whole
    # band offset — the grid has to match the actual drift scale.)
    ALIGN_SEARCH = [o for o in range(-2 * BAND_OVERLAP, 2 * BAND_OVERLAP + 1, 32) if o]
    for i, (y0, y1) in enumerate(band_list):
        if band_sims[i] >= threshold and abs(a.size[1] - b.size[1]) <= 2 * BAND_OVERLAP:
            continue  # unflagged: zero extra work by design
        bb = b.crop((0, y0, b.size[0], y1))
        best, best_off = band_sims[i], 0
        for off in ALIGN_SEARCH:
            ay0, ay1 = max(0, y0 + off), min(a.size[1], y1 + off)
            if ay1 - ay0 < (y1 - y0) // 2:
                continue  # window mostly off-page: not a valid rematch
            ba = a.crop((0, ay0, a.size[0], ay1))
            s = min(hamming_sim(dhash(ba), dhash(bb)), mae_sim(ba, bb))
            if s > best:
                best, best_off = round(s, 4), off
        aligned_band_sims[i] = round(best, 4)
        align_offsets[i] = best_off
    return {
        "page_sim": round(page_sim, 4),
        "changed": changed,
        "threshold": threshold,
        "band_sims": band_sims,
        "aligned_band_sims": aligned_band_sims,
        "band_align_offsets": align_offsets,
        "changed_bands": changed_bands,
        "n_bands": len(band_sims),
        "height_delta_px": b.size[1] - a.size[1],
        "algorithm": "dhash64+mae256/min-per-band, min-over-bands",
    }


def block_mae_sig(a_bytes: bytes, b_bytes: bytes, size: int = 64,
                  window_px: int | None = None) -> dict:
    """Shadow metric (spatial-noise-ceiling fit, Oct-20): per-block mean
    absolute luma error on the overlap region, page coordinates. Global
    band scalars dilute localized edits (a 200x40 wire-number swap is
    ~0.005 of a 1280x1280 band — W1 blind spot, external review E); a
    per-block ceiling learned from the cohort needs the per-block grid.
    One byte per block (quantized mae*255) base64'd — a fhlb-tall page is
    ~20x117 blocks ~= 3.2 KB per row, cohort-sized. Shadow only: this
    MUST NOT feed any verdict until the Oct-21 fit (Sammy's
    anti-ratchet rules + Quinn's exceedance harness)."""
    ia = Image.open(io.BytesIO(a_bytes)).convert("L")
    ib = Image.open(io.BytesIO(b_bytes)).convert("L")
    if ia.width != 1280:
        ia = ia.resize((1280, int(ia.height * 1280 / ia.width)))
    if ib.width != 1280:
        ib = ib.resize((1280, int(ib.height * 1280 / ib.width)))
    if window_px is not None:
        # GRIDWIN parity (Sammy ruling, Oct-25 r5): the regime string must
        # describe EVERY grid on the row. Same top-anchored crop as the
        # mean grid; collapses below H keep their own height (min(H,h))
        # and STILL differ in shape — fit's single-shape guard excludes
        # those cohorts by name; the window never launders them.
        if ia.height > window_px:
            ia = ia.crop((0, 0, ia.width, window_px))
        if ib.height > window_px:
            ib = ib.crop((0, 0, ib.width, window_px))
    rows = min(ia.height, ib.height) // size
    cols = 1280 // size
    diff = ImageChops.difference(ia, ib)
    vals = bytearray(rows * cols)
    for by in range(rows):
        for bx in range(cols):
            block = diff.crop((bx * size, by * size,
                               (bx + 1) * size, (by + 1) * size))
            st = ImageStat.Stat(block)
            vals[by * cols + bx] = min(255, int(round(st.mean[0])))
    return {"size": size, "cols": cols, "rows": rows,
            "b64": base64.b64encode(bytes(vals)).decode()}


def block_mae_shadow(a_bytes: bytes, b_bytes: bytes,
                     window_px: int | None = None) -> dict:
    """Compact shadow payload for cohort rows (Oct-20 schema ask): full grid
    at 64px + top-64 blocks at 16px with page coords + the censoring floor
    (value of the 64th block = every omitted 16px block is <= this, which
    is right-censored data, standard for ceiling fitting). ~4 KB/row.
    window_px: GRIDWIN parity — applied to BOTH grids; t16 coords become
    window-relative under a windowed regime (the regime string covers it).
    """
    fine = block_mae_sig(a_bytes, b_bytes, size=16, window_px=window_px)
    fv = list(base64.b64decode(fine["b64"]))
    cols = fine["cols"]
    order = sorted(range(len(fv)), key=lambda i: fv[i], reverse=True)[:64]
    top = [[i % cols, i // cols, fv[i]] for i in order]
    floor16 = fv[order[-1]] if len(order) == 64 else 0
    coarse = block_mae_sig(a_bytes, b_bytes, size=64, window_px=window_px)
    return {"g64": coarse, "t16": top, "floor16": floor16}


def block_mean_grid(a_bytes: bytes, size: int = 64,
                    window_px: int | None = None) -> dict:
    """Per-block mean luma (1 byte/block, b64) — the schema answer to the
    persistence clause (Oct-20, Sammy/Quinn): unsigned |MAE| vs the
    baseline cannot express sign, so slow same-direction edits are
    invisible to it.

    SCOPE — PERSISTENCE ONLY (Sammy's correction, measured; confirmed
    locally: equal-mean content slide reads MAE 36 vs |Δmean| 5): the
    mean grid must NEVER replace the MAE grid as the ceiling source.
    Block-mean averaging cancels equal-mean edits — exactly the
    swap/defacement class (measured under-read up to 27 levels on real
    hero motion, 8/14 moving blocks). Rows carry BOTH grids:
      * MAE grid (block_mae_sig)  -> ceiling fitting (unchanged, source of truth);
      * mean grid (this fn)       -> live↔live SIGNED deltas between
        consecutive rows -> persistence_events() only.
    ~6 KB/row total for a tall page."""
    ia = Image.open(io.BytesIO(a_bytes)).convert("L")
    if ia.width != 1280:
        ia = ia.resize((1280, int(ia.height * 1280 / ia.width)))
    if window_px is not None and ia.height > window_px:
        # GRIDWIN (Oct-25, HANDOFF-9 Addendum 3): top-anchored window,
        # shape deterministic by construction for pages TALLER than H.
        # Pages at-or-below H keep their own height — a collapse below H
        # MUST still break shape (truncation self-flags; never launder).
        ia = ia.crop((0, 0, ia.width, window_px))
    rows, cols = ia.height // size, 1280 // size
    vals = bytearray(rows * cols)
    for by in range(rows):
        for bx in range(cols):
            block = ia.crop((bx * size, by * size, (bx + 1) * size, (by + 1) * size))
            vals[by * cols + bx] = int(round(ImageStat.Stat(block).mean[0]))
    return {"kind": "block_mean", "size": size, "cols": cols, "rows": rows,
            "b64": base64.b64encode(bytes(vals)).decode()}


def _walk_epochs(parsed, max_gap):
    """Partition one site's ts-sorted (t, row) pairs into mean-grid
    epochs. Laws (Sammy round, owned here not by callers): missing grid
    closes the epoch (no_mean_grid); undecodable/contradicting metadata
    closes it (undecodable); unparseable ts closes it (no_ts); shape
    change closes it (shape_break); baseline_sha change closes it
    (repin_break — block indices are relative to the baseline); a ts gap
    > max_gap closes it (gap_break — schedule-splice laundering).
    Returns (epochs [(shape, grids, start_iso, bsha)], skips dict)."""
    epochs = []
    skips = {"no_mean_grid": 0, "undecodable": 0, "shape_break": 0,
             "repin_break": 0, "gap_break": 0, "no_ts": 0}
    cur = None            # [shape, grids, start_ts, bsha]
    prev_t = None

    def _close():
        nonlocal cur
        if cur is not None:
            epochs.append(cur)
            cur = None

    for t, r in parsed:
        mg = r.get("mean_grid")
        if mg is None:                      # row present, grid absent
            _close(); skips["no_mean_grid"] += 1
            continue
        try:
            meta = mg
            if isinstance(meta, str):
                # REAL rig rows stamp mean_grid as a JSON STRING (same
                # wire format as blockmae). A bare b64 string is NOT a
                # legal row: falling back to it silently decoded JSON
                # stamps as garbage bytes on the first live-ledger run
                # (caught on pvellm control.jsonl, Oct-21 18:2x UTC).
                meta = json.loads(meta)
            if not isinstance(meta, dict):
                raise ValueError("mean_grid must be dict or JSON dict")
            raw = base64.b64decode(meta["b64"], validate=True)
            cols = int(meta.get("cols") or 0)
            nrow = int(meta.get("rows") or 0)
            if not (cols and nrow) or cols * nrow != len(raw):
                raise ValueError("grid length contradicts metadata")
        except Exception:
            _close(); skips["undecodable"] += 1
            continue
        if t is None:                       # unorderable -> no epoch
            _close(); skips["no_ts"] += 1
            continue
        shape = (cols, nrow, len(raw))
        bsha = r.get("baseline_sha")
        if cur is not None:
            if shape != cur[0]:
                _close(); skips["shape_break"] += 1
            elif bsha and cur[3] and bsha != cur[3]:
                # re-pin boundary: block indices are relative to the
                # baseline, so a new baseline starts a new corpus
                _close(); skips["repin_break"] += 1
            elif max_gap and prev_t and \
                    (t - prev_t).total_seconds() > max_gap:
                _close(); skips["gap_break"] += 1
        if cur is None:
            cur = (shape, [], t.isoformat().replace("+00:00", "Z"), bsha)
            epochs.append(cur)
        cur[1].append(list(raw))
        prev_t = t
    return epochs, skips


def _parse_sort_gap(rows, expected_interval_s=None):
    """Shared corpus/shadow setup: group by baseline_key, parse ts,
    sort, derive max_gap (3 x expected, else 3 x median of the site's
    own positive spacings). Returns {key: (parsed, max_gap)}."""
    import datetime as _dt
    by_site: dict[str, list] = {}
    for r in rows:
        if isinstance(r, dict):
            by_site.setdefault(str(r.get("baseline_key", "")), []).append(r)
    out = {}
    for key, srows in by_site.items():
        parsed = []
        for r in srows:
            ts = r.get("ts")
            try:
                t = (_dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                     if ts else None)
            except ValueError:
                t = None
            parsed.append((t, r))
        parsed.sort(key=lambda x: (x[0] is None, x[0]))
        ts_all = [t for t, _ in parsed if t is not None]
        spacings = [(b - a).total_seconds()
                    for a, b in zip(ts_all, ts_all[1:])
                    if (b - a).total_seconds() > 0]
        if expected_interval_s:
            max_gap: float | None = 3.0 * expected_interval_s
        elif spacings:
            srt = sorted(spacings)
            max_gap = 3.0 * srt[len(srt) // 2]
        else:
            max_gap = None  # single row: no schedule to launder against
        out[key] = (parsed, max_gap)
    return out


def persistence_corpus(rows: list, min_run: int = 8, min_mag: int = 2,
                       expected_interval_s: float | None = None) -> dict:
    """Per-site persistence corpus (Sammy/Quinn attack round, same day as
    the first wiring — the law must NOT be loadable on callers).

    The ledger is cross-site and shape-variable, so this function, not
    the caller, owns the three laws:
      1. PARTITION: rows are grouped by `baseline_key` internally and
         sorted by `ts`. Ungrouped cross-site streams were measured
         guilty both directions: phantom storms (1360 fake events) and
         truth-annihilation (8 real events -> 0, status "ok").
      2. SHAPE: block index means position in THAT shot's own grid. A
         `(cols, rows)` change — or a grid whose decoded length disagrees
         with its own metadata — is a corpus BREAK, not a diff: any
         re-pin/recapture boundary starts a fresh epoch.
      3. ADJACENCY: a row present without `mean_grid` (capture-failure
         path), an unparseable ts, or a ts gap wider than
         `3 x expected_interval_s` breaks the run too. The gap law
         travels with the schedule: `expected_interval_s=None` derives
         `3 x median(spacings)` from the site's own rows, so the
         6h->1h cadence change cannot silently launder splices (Sammy).

    Return shape is PER-SITE (Sammy decision 1): "ok" is never a
    whole-ledger word — amazon can sit in `shape_mismatch` while fhlb
    reports `ok` in the same call:
      {"sites": {key: {"status": "ok" | "insufficient_corpus" |
                       "shape_mismatch",
                       "rows": <mean-grid rows seen for site>,
                       "usable_rows": <consecutive rows in newest epoch>,
                       "need": min_run,
                       "max_run_possible": usable_rows - 1,
                       "events": [... "epoch_start" stamped ...],
                       "skipped": {no_mean_grid, undecodable,
                                   shape_break, gap_break, no_ts},
                       "message"?}},
       "corpus_breaks": <total>, "message"?}
    House rule everywhere: never a bare [] that reads "checked, clean" —
    insufficiency is a named status with a message, and a site with 8
    usable rows reports max_run_possible=7 so no consumer mistakes the
    0-events answer (a run of 8 needs 9 rows) for a clean bill.
    Scope law unchanged (eb7d0bb): mean grids never enter a change
    verdict; the MAE grid stays the ceiling source.
    """
    sites: dict[str, dict] = {}
    total_breaks = 0
    for key, (parsed, max_gap) in _parse_sort_gap(
            rows, expected_interval_s).items():
        epochs, skips = _walk_epochs(parsed, max_gap)
        events = []
        for _shape, grids, start, _bsha in epochs:
            if len(grids) >= 2:
                for ev in persistence_events(grids, min_run=min_run,
                                             min_mag=min_mag):
                    ev["epoch_start"] = start
                    events.append(ev)
        usable = len(epochs[-1][1]) if epochs else 0
        breaks = sum(skips.values())
        total_breaks += breaks
        site = {"status": "insufficient_corpus", "rows": len(parsed),
                "usable_rows": usable, "need": min_run,
                "max_run_possible": max(usable - 1, 0),
                "events": events, "skipped": skips}
        if usable >= min_run:
            site["status"] = "ok"
        elif skips["shape_break"] or skips["undecodable"]:
            site["status"] = "shape_mismatch"
            site["message"] = (f"capture-shape break reset the corpus; "
                               f"{usable}/{min_run} usable rows since — not "
                               f"measurable until capture shape settles "
                               f"(re-pin decision), NOT a clean result")
        else:
            site["message"] = (f"insufficient corpus: {usable} consecutive "
                               f"mean-grid row(s), need {min_run} — no "
                               f"persistence verdict")
        sites[key] = site

    out = {"sites": sites, "corpus_breaks": total_breaks}
    if not sites:
        out["message"] = ("no mean-grid rows in ledger — nothing checked, "
                          "NOT a clean verdict")
    return out


def viability_report(rows: list, min_fires: int = 24,
                     min_mode_share: float = 23 / 24,
                     min_blank_share: float = 23 / 24,
                     min_osc_transitions: int = 4,
                     flip_rate_osc: float = 0.6) -> dict:
    """Det-only viability gate (Quinn final spec + Sammy content half)
    computed BY THE GUARD over the viability ledger itself — no
    hand-counted numbers graduate a site.

    Input is ordinary ledger rows (real wire: mean_grid/blockmae/
    capture_sanity as JSON strings or dicts). Per site reports:
      shape side: fires, mean_grid_fires, shape histogram keyed
        "colsXrows", mode_share, shape_flips (transitions where the
        shape state changed);
      regime side: the set of capture_regime values seen — equality,
        not a value test (drift off the current lineage is auto-fail,
        the regime-equality quarantine's lesson);
      blank side: blank_bands-empty share from capture_sanity;
      CONTENT side (Sammy): the inverted-polarity sign-run — per block,
        a flip is a change of sign between consecutive moving deltas
        (|d| >= min_mag=2); a block with >= min_osc_transitions moving
        steps and flip_rate >= flip_rate_osc is an OSCILLATOR with its
        mean |delta|. A shape-stable, content-randomized site can no
        longer walk the gate into a baseline ceremony: its random block
        shows up as a pre-registered oscillator line instead of as
        future mystery drama. Waiver/oscillator pre-marks write
        themselves out of this report, not out of anyone's memory.

    Graduation is per-site: shape pass = mean_grid_fires >= min_fires
    AND mode_share >= min_mode_share AND regime_ok AND blank share >=
    min_blank_share. Content is REPORTED, not gated — oscillators inform
    ceilings and waivers; they do not fail a site by themselves.
    """
    by_site: dict[str, list] = {}
    for r in rows:
        if isinstance(r, dict):
            by_site.setdefault(str(r.get("baseline_key", "")), []).append(r)

    sites, passed, failed = {}, [], {}
    for key, srows in by_site.items():
        srows = sorted(srows, key=lambda r: str(r.get("ts", "")))
        shapes, mg_n = [], 0
        regimes = set()
        blank_empty = blank_seen = 0
        grids = []          # (shape, values) for content floor
        for r in srows:
            regimes.add(str(r.get("capture_regime")))
            cs = r.get("capture_sanity")
            try:
                cs = json.loads(cs) if isinstance(cs, str) else cs
                if isinstance(cs, dict) and "blank_bands" in cs:
                    blank_seen += 1
                    if not cs["blank_bands"]:
                        blank_empty += 1
            except Exception:
                pass
            mg = r.get("mean_grid")
            if not mg:
                continue
            try:
                meta = json.loads(mg) if isinstance(mg, str) else mg
                raw = base64.b64decode(meta["b64"], validate=True)
                cols, nrow = int(meta["cols"]), int(meta["rows"])
                if cols * nrow != len(raw):
                    continue
            except Exception:
                continue
            mg_n += 1
            shapes.append((cols, nrow))
            grids.append(((cols, nrow), list(raw)))

        hist: dict[str, int] = {}
        for c, n in shapes:
            hist[f"{c}X{n}"] = hist.get(f"{c}X{n}", 0) + 1
        mode_share = (max(hist.values()) / mg_n) if mg_n else 0.0
        flips = sum(1 for a, b in zip(shapes, shapes[1:]) if a != b)
        regime_ok = len(regimes) <= 1
        blank_share = (blank_empty / blank_seen) if blank_seen else 0.0

        # content noise floor: consecutive SAME-SHAPE captures only
        osc = []
        prev = None
        moves: dict[int, list[int]] = {}
        for (c, n), vals in grids:
            if prev is not None and (c, n) == prev[0]:
                for bi in range(len(vals)):
                    d = vals[bi] - prev[1][bi]
                    if abs(d) >= 2:
                        moves.setdefault(bi, []).append(d)
            prev = ((c, n), vals)
        for bi, ds in moves.items():
            if len(ds) < min_osc_transitions:
                continue
            fl = sum(1 for a, b in zip(ds, ds[1:]) if (a > 0) != (b > 0))
            rate = fl / (len(ds) - 1)
            if rate >= flip_rate_osc:
                osc.append({"block": bi, "flip_rate": round(rate, 3),
                            "mean_abs_delta":
                                round(sum(abs(d) for d in ds) / len(ds), 2)})
        osc.sort(key=lambda e: -e["flip_rate"])

        reasons = []
        if mg_n < min_fires:
            reasons.append(f"mean_grid_fires {mg_n} < {min_fires}")
        if mg_n and mode_share < min_mode_share:
            reasons.append(f"mode_share {mode_share:.3f} < {min_mode_share:.3f}"
                           f" (hist {hist})")
        if not regime_ok:
            reasons.append(f"regime drift: {sorted(regimes)}")
        if blank_seen and blank_share < min_blank_share:
            reasons.append(f"blank_bands_empty {blank_share:.3f} < "
                           f"{min_blank_share:.3f}")
        site = {"fires": len(srows), "mean_grid_fires": mg_n,
                "shape_hist": hist, "mode_share": round(mode_share, 4),
                "shape_flips": flips, "regime_set": sorted(regimes),
                "regime_ok": regime_ok,
                "blank_bands_empty_share": round(blank_share, 4),
                "oscillators": osc,
                "graduates_shape": not reasons}
        sites[key] = site
        if reasons:
            failed[key] = reasons
        else:
            passed.append(key)
    return {"sites": sites, "pass": sorted(passed), "fail": failed,
            "policy": {"min_fires": min_fires,
                       "min_mode_share": round(min_mode_share, 4),
                       "min_blank_share": round(min_blank_share, 4),
                       "oscillator": {"min_transitions": min_osc_transitions,
                                      "flip_rate": flip_rate_osc}}}


def persistence_events_ratio_net(grids: list, min_run: int = 8,
                                 min_mag: int = 2, window: int = 16,
                                 ratio_req: float = 0.8,
                                 net_req: int = 8) -> list:
    """SHADOW law (not shipped into any verdict): Quinn's ratio+net
    composite over sliding windows, with Sammy's velocity-floor law
    documented as scope, not a bug to fix.

        event := nonzero_steps >= min_run within a `window`-transition
                 window  AND  ( same-sign ratio >= ratio_req
                                OR |Σ window Δ| >= net_req )

    * `ratio` term closes Sammy's duty-cycle hole (a zero delta is
      NEUTRAL — rest hours no longer reset anything);
    * `net` term closes Quinn's biased-sawtooth hole (+3,-2,... has
      ratio 0.5 forever but Σ=+8 per 16 transitions);
    * Σ sums ALL deltas (telescoping displacement — velocity per
      Sammy), so sub-min_mag steps still count toward the net; the
      nonzero_steps gate is the noise floor for the ratio term.

    VELOCITY FLOOR (Sammy, structural to the whole law FAMILY):
    displacement under ~net_req/window per row — e.g. +1 every fourth
    row — is SILENT under run, ratio, and net alike (Σ<8 per window and
    nonzero_steps<8), and any further `or` only moves the constant.
    Slow drift is CUMULATIVE prey: it belongs to per-block ceilings
    against the baseline (v0.7, unshipped), and nothing between here
    and there covers it. Do NOT "fix" this detector to eat its own
    layer's output — plant sub_velocity_known_silent guards it.

    Oscillation is silent by CONSTRUCTION on the net term (zero-net),
    not by threshold tuning: an unbiased oscillator can never clear it.

    Every event carries its `term` ("ratio" | "net") — Quinn's
    consumer law: a `net` event on a rotator and a `ratio` event on a
    static page are not the same accusation, and cross-check-before-
    celebrating weighs evidence per class. One event per block per term
    (first firing window only); same partition law as
    persistence_events: one site, one shape, one adjacent epoch, else
    ValueError.
    """
    if grids and any(len(g) != len(grids[0]) for g in grids):
        raise ValueError("persistence_events_ratio_net: mismatched grid "
                         "shapes in one stream — partition first")
    if len(grids) < 2:
        return []
    events = []
    for bi in range(len(grids[0])):
        ds = [grids[t][bi] - grids[t - 1][bi] for t in range(1, len(grids))]
        fired = set()
        for s in range(0, len(ds) - window + 1):
            win = ds[s:s + window]
            nz = [d for d in win if abs(d) >= min_mag]
            if len(nz) < min_run:
                continue
            pos = sum(1 for d in nz if d > 0)
            neg = len(nz) - pos
            net = sum(win)
            if "ratio" not in fired and max(pos, neg) / len(nz) >= ratio_req:
                fired.add("ratio")
                events.append({"block": bi, "term": "ratio",
                               "sign": 1 if pos > neg else -1,
                               "pos": pos, "neg": neg, "nz": len(nz),
                               "window_start_idx": s})
            if "net" not in fired and abs(net) >= net_req:
                fired.add("net")
                events.append({"block": bi, "term": "net",
                               "sign": 1 if net > 0 else -1,
                               "net_sum": net,
                               "window_start_idx": s})
    return events


def persistence_shadow(rows: list, min_run: int = 8, min_mag: int = 2,
                       window: int = 16, ratio_req: float = 0.8,
                       net_req: int = 8) -> dict:
    """Side-by-side SHADOW table over one ledger: shipped run-law vs
    ratio-only vs ratio+net, over IDENTICAL epochs (same partition,
    same skip accounting — _walk_epochs), zero new captures.

    Purpose (Sammy/Quinn shadow-run protocol): if the ratio or net term
    produces an event the shipped run law missed on a REAL site, that
    is a cohort true/false-positive candidate priced for free — the
    cohort's numbers, not anyone's aesthetics, decide whether a term
    ships. Per-site output:
      {"rows", "epochs", "events": {"run": [...], "ratio": [...],
                                    "net": [...]}}
    run events keep persistence_events' schema; ratio/net events carry
    their `term`. This table never produces a verdict; mean grids never
    enter a change verdict (Persistence Law unchanged)."""
    sites = {}
    for key, (parsed, max_gap) in _parse_sort_gap(rows).items():
        epochs, _skips = _walk_epochs(parsed, max_gap)
        ev: dict[str, list] = {"run": [], "ratio": [], "net": []}
        for _shape, grids, start, _bsha in epochs:
            if len(grids) < 2:
                continue
            for e in persistence_events(grids, min_run=min_run,
                                        min_mag=min_mag):
                e["epoch_start"] = start
                ev["run"].append(e)
            for e in persistence_events_ratio_net(
                    grids, min_run=min_run, min_mag=min_mag, window=window,
                    ratio_req=ratio_req, net_req=net_req):
                e["epoch_start"] = start
                ev[e["term"]].append(e)
        sites[key] = {"rows": len(parsed),
                      "epochs": len([1 for _s, g, _t, _b in epochs
                                     if len(g) >= 2]),
                      "events": ev}
    return {"sites": sites,
            "law": {"run": f"consecutive same-sign run >= {min_run}",
                    "ratio": f"window {window}: nz>={min_run} ∧ "
                             f"same-sign>={ratio_req}",
                    "net": f"window {window}: nz>={min_run} ∧ |ΣΔ|>={net_req}"},
            "scope_note": "sub-velocity drift (<net_req/window net, "
                          "<min_run nonzero steps) is OUT OF SCOPE by "
                          "construction for every window law — cumulative-"
                          "layer (v0.7 ceilings) prey, not a clean result"}


def persistence_events(grids: list, min_run: int = 8, min_mag: int = 2) -> list:
    """Suspicion events from consecutive live block-mean grids: a block
    whose signed live↔live delta keeps the SAME SIGN for >= min_run
    transitions (each step >= min_mag) is an event — noise oscillates,
    defacements don't swing back. Amplitude-independent by design
    (Quinn's laundering path); min_mag keeps quantization jitter out.
    Returns [{block, cols/rows implied by index, sign, run, total}].

    GRIDS MUST BE ONE SITE, ONE SHAPE, ONE ADJACENT EPOCH — callers do
    not get to guarantee that informally (a cross-shape scan was a live
    crash / silent-blindness vector until the epoch partition landed), so
    the requirement is asserted here: mismatch raises, never IndexError,
    never a truncated field of view. Use persistence_corpus() for raw
    ledger rows."""
    if grids and any(len(g) != len(grids[0]) for g in grids):
        raise ValueError("persistence_events: mismatched grid shapes in one "
                         "stream — partition by baseline_key/shape/adjacency "
                         "first (see persistence_corpus)")
    if len(grids) < 2:
        return []
    n = len(grids[0])
    events = []
    for bi in range(n):
        run, last_sign, total = 0, 0, 0
        for t in range(1, len(grids)):
            d = grids[t][bi] - grids[t - 1][bi]
            sign = 1 if d >= min_mag else (-1 if d <= -min_mag else 0)
            if sign != 0 and sign == last_sign:
                run += 1
                total += d
            elif sign != 0:
                run, last_sign, total = 1, sign, d
            else:
                run, last_sign, total = 0, 0, 0
            if run == min_run:
                events.append({"block": bi, "sign": last_sign,
                               "run": run, "total": total})
    return events
