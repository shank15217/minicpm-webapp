# Calibration Protocol (v0.5) — DRAFT v1 by @qaquinn

Status: draft for room sign-off. Every number below is a measured value from the
2026-10-14/15 QA sessions (see MINICPM-QA-REPORT-3…6), not an assumption.

## 0. What gets calibrated (and what must never be re-calibrated away)
The deterministic layer (`detdiff`) owns the WHETHER verdict. Calibration tunes
its **tolerances** and qualifies the **narrator**; it never re-admits the VLM as
judge. Pairwise same-or-different prompts are architecturally banned
(p measured laundering: blanked page → `score 100, differences: []`).

## 1. Instrument freeze — calibration is conditional on ALL of these
A calibration is only valid for the exact tuple recorded alongside it:
`llama.cpp build id` × `LLM quant` × `mmproj tower` × `PROMPT_VERSION` × `DET_PASS_SIM`.
Measured justification: same weights on builds b11610 vs b11641 produced
different per-band readings (Act 2/3); same prompt wording across harnesses
produced different tables (Act 1); prompt wording alone moved scores across the
full range on identical images (Act 6). Any element changing = full
recalibration, not an adjustment.

## 2. Control runs (the noise floor of "nothing changed")
- Per baseline: **N ≥ 5** captures+diffs of the live page vs its baseline,
  spaced ≥ 60 s, `run_history.kind = 'control'`.
- Record the **per-band sim vector** for every control run (not just the page
  min — see §6 storage gap). 
- Per-band noise floor = [min, p95] of control sims. A band whose control p95
  < candidate threshold is **dynamic content** (measured: real fhlb changed
  `changed_bands` between two runs minutes apart at dHash/MAE level — clocks,
  tickers, carousels). Disposition per dynamic band: widen its tolerance to
  control-p95 − margin, or mask it (masking is an explicit, logged, reviewable
  act — it disables detection there).
- Alert rule candidate: `page_sim < DET_PASS_SIM` AND k-worst (§3) AND band not
  inside its learned tolerance. `DET_PASS_SIM=0.995` remains a placeholder until
  control distributions exist for ≥ 3 real baselines.

## 3. Aggregation: k-worst, never naïve min
Measured phantom-band rate ≈ 1-in-48 clean bands (~2%) at temp 0 on b11641
→ naïve min false-alerts ≈ 10% of clean 6-band pages. Spec: downgrade only when
**≥ 2 bands** independently fall below threshold (k=2 default); record phantom
rate from control sets continuously to refine k per baseline size.

## 4. Post-calibration acceptance probe (calibration must prove it didn't
loosen the alarm into a blindfold)
Synthetic defacements replayed against every calibrated baseline: blank-band
(≥ 25% region whited — caught at 0.259 det sim), banner overlay (1.3% area →
0.969 det sim), half-page white. Pass = k-worst flags it ≥ 2 consecutive
scheduled checks. Separation required: defaced-p5 < control-p95 per band,
computed, not eyeballed. A calibration that cannot separate these is rejected.

## 5. Narrator qualification (single-image only)
`NARRATION_PROMPT_SINGLE` on located crops; QA spot-grades ≥ 10 narrations per
model/tower tuple for factual grounding (name objects present; penalty for
verdict language — narration assigning a health score is an instrument
violation, file a defect). `detail` tower (f16, ~833 tok/640×480 img) for
narration; `fast` (-4x, ~224 tok) allowed for triage only.

## 6. Known storage gap (blocks §2 implementation) — owner: code owner
`run_history.det_similarity` stores the page MIN only. Calibration math needs
the per-band vector; today it is recomputable but not queryable. Proposal:
`det_band_sims TEXT` (JSON list) column via the same idempotent startup ALTER
(S9-pinned). Until then, calibration tooling computes on demand from stored
PNGs — slower, but the protocol runs either way.

## 7. Recalibration triggers (any one ⇒ full §2–§4 rerun)
prompt text change · model/quant change · build id change · tower mode change
· threshold or k change · baseline re-capture · 30 days elapsed, whichever first.

## 8. Deliverables when v0.5 executes
`scripts/calibrate.py` (runs §2, emits per-baseline profile JSON), profiles
versioned next to baselines, acceptance §4 results in `kind='calibration'`
rows, README calibration section citing this protocol's measured constants.
