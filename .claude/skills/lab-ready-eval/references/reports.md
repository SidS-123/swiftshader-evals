# Reports, design doc and decision log

## The documents

| Document | Role | Who writes it |
|---|---|---|
| `README.md` | public project page: what it is, task, scoring, leaderboard, how to run, how to reproduce, layout, license | maintainers |
| `docs/DESIGN.md` | the metric, the categories, every control's prediction, known limitations, the decision log | maintainers, before measuring |
| `docs/REQUIREMENTS.md` | the feature families the corpus must cover | maintainers |
| `docs/REPLAY_FORMAT.md` | the driver's case format, every op | maintainers |
| `docs/REPRODUCIBILITY.md` | exact commands to rebuild the oracle, corpus, caches, controls; what needs the private tree; what is host-dependent | maintainers |
| `reports/VALIDATION.md` | the consolidated evidence: host, corpus, tolerance distribution, controls predicted vs observed, metric history, performance timing, harness verification, calibration attempts, defects found and fixed, open items | maintainers, from measurements |
| `reports/CONTROLS.md`, `reports/PERF_VARIANCE.md` | generated sections between markers from run directories, prose outside | a script |
| `harness/README.md`, `harness/RUNBOOK.md` | architecture; operator procedure with every command | maintainers |
| `docs/PUBLISHING.md` | internal pre-publication checklist; excluded from the export | maintainers |
| `docs/internal/` | plans and handoff notes; excluded from the export | whoever is working |

## Rules for every public document

- Neutral maintainers' voice. No "owner", no agent or session names, no
  narration of how work was organised, no status diaries ("Runs: … Not yet:
  …", "as of <date>"), no pointers to internal notes.
- Every number comes from a measurement on a named host on a named date by a
  named grader version; projections are labelled and struck when measured.
- History that explains current behaviour stays, labelled as history:
  compromised runs, metric revisions, defects found. Internal bookkeeping
  goes.
- Measurement dates are evidence; keep them. Section header dates are not;
  drop them.
- Generated sections are never hand-edited; the tests check the markers.

## The validation report, section by section

1. Host: CPU, cores, memory, kernel, Docker, image digests.
2. Corpus: counts per split and category, families covered, determinism
   evidence.
3. Reference thresholds: the tolerance distribution and the cases at the cap.
4. Controls: predicted vs observed, both corpora, with the reasoning for every
   restated prediction.
5. Metric history: one section per version, what changed, why, what it did
   to every control and every attempt, the fairness bars it kept. If a
   version was tightened after scores were seen, say so in plain words and
   show the old numbers beside the new.
6. Performance timing: repeat spread, reference times, the half-point's
   margin.
7. Harness: smokes, boundary verification on the wire, incidents and the
   fix, container ownership.
8. Calibration attempts: leaderboard with all categories and full-success,
   stop reasons and elapsed, per-family results, curves, the compromised runs
   labelled.
9. Defects found and fixed during validation, one line each with the commit.
10. Open items: what is not measured and why, honestly (cross-ISA, run-to-run
    variance, unreached stop reasons).

## The decision log

In the design doc, one dated paragraph per durable decision: what was
decided, the measurement that drove it, what it changed. Examples: which
feature families are not graded; which perturbations are tolerated; that a
control's prediction was restated; that the hidden generators moved to a
private tree; each metric version. Public documents point here, never at
working notes.

## Status tables for hand-backs

Every stage report ends with a table of item, state, evidence path. A
reviewer must be able to open each evidence path and see the number.
