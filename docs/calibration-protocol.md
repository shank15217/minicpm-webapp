# Calibration Protocol (v0.5) — DRAFT v2 by @qaquinn

Status: draft v2 for room sign-off — absorbs Sammy's four review findings with
two source-verified corrections (§3, §6.5). Every number is a measured value
from the 2026-10-14/15 QA sessions (MINICPM-QA-REPORT-3…6), not an assumption.

## 0. What gets calibrated (and what must never be re-calibrated away)
The deterministic layer (`detdiff`) owns the WHETHER verdict. Calibration tunes
its **tolerances** and qualifies the **narrator**; it never re-admits the VLM as
judge. Pairwise same-or-different prompts are architecturally banned
(measured laundering: blanked page → `score 100, differences: []`).

## 1. Instrument freeze — calibration is conditional on ALL of these
A calibration is valid only for the exact tuple recorded alongside it:
`llama.cpp build id` × `LLM quant` × `mmproj tower` × `PROMPT_VERSION` × `DET_PASS_SIM`.
Measured justification: same weights, builds b11610 vs b11641 → different
per-band readings (Acts 2/3); prompt wording alone moved identical-image
scores across the full range (Act 6). Any element changing = full
recalibration, not an adjustment.

## 2. Control runs (the noise floor of "nothing changed")
- **Health attestation prerequisite (Sammy):** a profile never graduates to
  `diurnal_verified` until a human (or QA with narrator-assist, caveated) has
  eyeballed baseline≠live at least once and the reason is recorded in profile
  metadata (`band0: rotation (attested DATE)` or defect filed). Tolerance
  learning must not eat unadjudicated "most likely rotation" — calibration
  laundering a real current defect is the blindfold failure §4 rejects,
  arriving through training data instead of the threshold.
- Per baseline: **N ≥ 5** captures+diffs of live vs baseline,
  `run_history.kind = 'control'`.
- **Diurnal coverage (Sammy 3):** initial controls spread across ≥ 24 h
  (≈ 0/6/12/18/24 h) — five runs in five minutes never see the hourly carousel
  rotate, the day/night theme, or the ticker close. Profiles built without
  spread carry `provisional_until_diurnal`; the §7 30-day rerun accumulates
  coverage toward `diurnal_verified`.
- Record the **per-band sim vector** for every control run (§6 storage gap;
  `detdiff.page_diff` already returns `band_sims` — only persistence is missing).
- Per-band noise floor = [min, p95] of control sims. A band whose control p95
  is below the candidate threshold is **dynamic content** (measured: real fhlb
  changed `changed_bands` between runs minutes apart at dHash/MAE level —
  clocks, tickers, carousels). Disposition per dynamic band: widen its
  tolerance to control-p95 − margin, or mask it (masking is explicit, logged,
  reviewable — it disables detection there).
- Alert rule candidate: `page_sim < DET_PASS_SIM` AND k-worst (§3) AND band not
  inside its learned tolerance. `DET_PASS_SIM=0.995` remains a placeholder
  until control distributions exist for ≥ 3 real baselines.

## 3. Aggregation: k-worst — sized from DET noise, not VLM noise (Sammy 1)
Correction to draft v1: the ~2%/band phantom rate (1-in-48) is a **VLM-band**
statistic from the determinism runs; it does NOT size k for v0.4+, where alerts
consume det sims. det on identical input is exactly 1.000 with zero variance —
det's noise arrives through the **input channel**: Playwright capturing
mid-animation / mid-lazy-load, font-shift during render, and height drift
(§6.5). **k is derived from §2 control vectors: the fraction of control runs
with ≥ 1 det band below threshold.** k=2 stays the starting placeholder until
the first control batch produces that number — this doc's opening claim applies
to its own constants, and v1's citation of a VLM statistic for a det parameter
was exactly the founding-sin class this room hunts.

## 4. Post-calibration acceptance probes (calibration must not loosen the alarm into a blindfold)
Replayed against every calibrated baseline: blank-band (≥ 25% region whited,
det sim 0.259), banner overlay (1.3% area → 0.969), half-page white, **plus a
smallest-detectable-defacement cell (Sammy 4): single-line text swap ≈ 0.1%
area, run at the calibrated threshold.** Pass = k-worst flags each ≥ 2
consecutive scheduled checks. Separation required: defaced-p5 < control-p95 per
band, computed, not eyeballed. **Published sensitivity floor:** tonight's own
curve (1.3% area → 0.969) implies sub-1% edits likely pass a 0.995 threshold —
the README must state the measured floor honestly. A monitor that misses
text-swaps while claiming "defacement verified" is the credibility failure this
project exists to prevent; better a published floor than a discovered one.

## 5. Narrator qualification (single-image only)
`NARRATION_PROMPT_SINGLE` on located crops; QA spot-grades ≥ 10 narrations per
model/tower tuple for factual grounding (name objects present; verdict language
in narration is an instrument violation — file a defect). `detail` tower (f16,
~833 tok/640×480 img) for narration; `fast` (-4x, ~224 tok) triage only.

## 6. Storage gap (blocks §2 implementation) — owner: code owner
`run_history.det_similarity` stores the page MIN only. Calibration math needs
the per-band vector; today it is recomputable but not queryable. Proposal:
`det_band_sims TEXT` (JSON list) column via the same idempotent startup ALTER
(S9-pinned path). Until then, calibration tooling computes on demand from
stored PNGs — slower, but the protocol runs either way.

## 6.5 Band alignment under height change (design fork — decide before `calibrate.py`)
Source-verified current behavior (**correcting the review premise** "splits by
each image's own height"): `detdiff.page_diff` splits BOTH images with shared
absolute-y bands capped at `h = min(heights)`; height mismatch is *already* a
first-class signal — `changed` trips on `|Δh| > 2×BAND_OVERLAP` and
`height_delta_px` ships in the output (detdiff.py:102/:110). **Residual failure
class:** sub-flag drift, or a content insertion under the flag
threshold — downstream bands then compare misaligned regions and cascade-fire
on legitimate updates. Alerting keeps its fail-loud bias (good), but
localization becomes garbage, and calibrating per-band tolerances against
cascading bands would encode the bug as tolerance. Options: (a) accept cascade
for v0.5 — route `all-bands-changed + Δheight` runs into a distinct
human-review / `site-updated` flow separate from defacement; (b) best-offset
window matching per band; (c) DOM-anchored band edges. **(b)-lite
(Sammy):** option (a) for the site-updated routing, plus re-match *flagged
bands only* against the baseline at ±1-band offsets (radius = the sub-flag
drift bound `2×BAND_OVERLAP`), pure CPU, deterministic. QA guardrail for any
offset-matching variant — **maximizing sim must never downgrade WHETHER**:
the alert consumes the RAW page min (conservative, fail-loud); the ALIGNED sim
is used only for localization, narrator crop selection, and the tolerance
distributions, and profiles store both raw and aligned control distributions.
Acceptance if (b)-lite is adopted: synthetic mid-page insertion replay must
localize to ±1 band, and an injected blank-band defacement positioned so a
shifted baseline window partially resembles it must still alert. Try on the
fhlb control batch first; fallback is documenting localization precision
(±drift px) next to the §4 sensitivity floor in the README.
QA recommendation: **(a) minimum; (a)+(b)-lite if the guardrail holds on
control data.** Owner: code owner; decision required before `calibrate.py`
exists.

## 7. Recalibration triggers (any one ⇒ full §2–§4 rerun)
prompt text change · model/quant change · build id change · tower mode change
· threshold or k change · baseline re-capture · 30 days elapsed, whichever first.

## 8. Deliverables when v0.5 executes
`scripts/calibrate.py` (runs §2, emits per-baseline profile JSON incl.
`provisional_until_diurnal` / `diurnal_verified` status), profiles versioned
next to baselines, acceptance §4 results in `kind='calibration'` rows, README
calibration section citing measured constants **and the §4 sensitivity floor**.
