# Calibration: train → enable

The monitor's verdict layer (`detdiff`, threshold `page_sim >= 0.995`) is
**static**. Calibration measures what each monitored site does *when
nothing is wrong*, so the static threshold can be replaced by per-block
noise ceilings fit to observed reality. This directory is that system;
it lives in-repo (v0.5 protocol §2) but **runs out-of-band from serving**
— no live verdict ever consults it, and deleting it leaves the monitor
fully functional.

## Components

| file | role |
|---|---|
| `rig.py` | scheduled capture batch. Iterates the app's **own** baseline index (`baselines/index.json`), captures each site through the sanctioned regime (imports `LOADER_JS`, `blank_tail_bands`, route guard, and stamps `capture_surface_sha()` **by reference from `app.py`** — rig/socket regime parity is computed, not argued), runs `detdiff.page_diff`, appends one JSONL row incl. the `blockmae` per-block shadow grid. |
| `fit.py` | reduces cohort rows → `ceilings.json`: per baseline, per 64px block, `clamp(k·p95(observations), floor, cap)`, one byte per block b64. Blocks whose baseline has `< --min-rows` rows get `status:"insufficient"` and must never be enabled. |
| rows | `control.jsonl` (prod: `/root/mpw-calibration/`). Regime quarantine is **strict equality** on `capture_regime` — rows from any older capture regime are excluded, never "close enough". |

## Train → enable workflow (the "training" component)

1. **Pin baselines** for your site list via the app's normal capture-as-baseline
   path (each baseline must be a clean, regime-passing capture —
   `blank_tail_bands` gated).
2. **Run the rig on a schedule** ≥ 2 full days (want both diurnal halves
   and a weekday+weekend):
   `0 */6 * * * cd <repo>/calibration && .venv/bin/python rig.py >> batch.log 2>&1`
   **⚠ clock pitfall (measured, Oct-20):** cron fields are LOCAL time.
   pvellm2 runs EDT, so `0 */6` fires 04/10/16/22 **UTC**. Name the clock
   in every schedule claim.
3. **Fit:** `python fit.py --rows control.jsonl --out ceilings.json`
   (defaults: `--min-rows 48 --k 2.0 --floor 24`).
4. **Enable:** serve-time comparison of a live block's MAE against that
   block's ceiling **is not yet wired into `app.py`** — that is the
   Oct-21 change, gated on QA. Until then the app keeps the global
   threshold and the corpus keeps accumulating; nothing half-fit ships.
5. **Re-fit = deliberate redeploy.** See anti-ratchet rules below.

## Generality

Nothing here is fhlb- or amazon-specific *except the data*. The site
list is the app's own baseline index; the grids are pure pixel statistics
in page coordinates; the fit is per-(baseline, block) from that
baseline's own history. Adding a site = capturing a baseline in the app;
the rig picks it up on its next fire. What did NOT generalize and is
honest debt: reveal-CSS/animation behaviors are handled by the shared
`LOADER_JS` heuristics (WOW.js/AOS idioms, CSS backgrounds); a site using
a different reveal mechanism could still capture unrevealed — the
`blank_tail_bands` + narration layers exist precisely to catch that class.

## Anti-ratchet rules (binding, adversarial review Oct-20)

* Deployed ceilings are frozen at fit time. Live traffic never updates
  them — an attacker would ratchet a ceiling upward with slow edits.
* Blocks with insufficient observations fall back to the global verdict.
* Any fitted-threshold change ships through the normal QA loop with the
  fit parameters and cohort provenance (`regime`, `capture_surface`,
  `app_commit` lineage) attached.

## Operational notes

* Prod deployment copy currently runs from `/root/mpw-calibration/`; this
  repo copy is the source of truth and adds the `__main__` import guard
  (importing the old prod file executed the whole batch — incident
  2026-10-20, self-reported).
* Row provenance: `trigger` `auto` means cron; a `python -c` import of an
  unguarded rig mislabels manual runs as `auto` — treat pre-guard `auto`
  rows near a known manual intervention with suspicion.
* Shadow payload is ~1–4 KB/row; a 48-row cohort per site is well under
  1 MB/site/day.
