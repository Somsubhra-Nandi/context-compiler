# Prompt: A6-bis — back-propagation and the metrics A6 did not collect

**Read first:** `docs/specs/amendment-a6.md`, `docs/spikes/mcp-item-7-results.md` §8.1,
`README.md`, `docs/specs/context-compiler-v1.3.md` §9.

**Context.** A6 is adopted and green. But `grep -rn "A5\|A6" docs/spikes/ README.md`
returns nothing: every published artifact still shows numbers measured on the
pre-A6 graph, which carried 38,944 fabricated `CALLS` edges. The amendments
record discrepancies going forward and nothing propagates them back. This task
closes that gap and collects the figures A6.3 did not measure.

Time-box: 3 hours. Stop and report rather than working around.

---

## Task 1 — Pre-register, before running anything

Append to `docs/specs/amendment-a6.md` a short section **A6.5**, written and
committed *before* Task 2 executes:

> The v1.3 §9 simulation predicted median closure 47, p90 83, 10/200 over
> 8,000 tokens; the pre-A6 implementation produced 46, 86, 10. That validation
> ran against a graph since shown to carry 38,944 fabricated `CALLS` edges.
> Prediction for the corrected graph, registered before measurement:
> **closure sizes shrink**, because fabricated container edges are removed;
> the effect is **largest where a class or module was admitted at L2**, since
> those carried the bulk of the fabrication and re-expanded on hop 2. Median
> and p90 should both fall; the over-budget count should fall or hold.

Then state which of three outcomes you will report: the prediction holds and
the simulation was capturing level-merging rather than edge noise; it shifts
but the deviations run in the predicted direction, with a mechanism; or it
breaks, in which case say so plainly and scope the original result to the
pre-A6 graph.

**Do not tune anything to preserve the 46-vs-47 match.** If it breaks, that is
the finding.

---

## Task 2 — The metrics A6.3 skipped

Re-run the 200-trial harness (same pool of 1,891, same `rng_seed=20260817`,
6 seeds, 8,000-token budget) and report, each against its pre-A6 value:

1. **Closure size** — median, p90, max, and count over 8,000 tokens. This is
   the A6.5 comparison. Report it first.
2. **Optional packing share** — the pre-A6 claim is 50.4% of the compiled
   context. Given A6.4 shows the flagship example now admits 13 optional
   tokens, this may have moved a long way. Report the distribution, not just
   the median.
3. **Level composition** — pre-A6 was 6.0 L3 / 17.0 L2 / 29.8 L1.
4. **Mandatory floor and compiled totals** — emitted symbols and tokens for
   both, the two rows the README's results section will quote.
5. **Compile latency** — median and p99. Pre-A6 was 1,059 ms / 2,782 ms.
   Expect improvement from 31% fewer edges; if it did *not* improve, say so
   and investigate briefly, because that would be surprising.
6. **Round trips** — pre-A6 median 24 (12 closure + 6 reverse + 6 envelope).

Also re-measure `impact_cone` latency in the 150–500 in-degree band, listed as
a known open issue at up to 6.3 s. Fewer reverse edges should have helped.

---

## Task 3 — Regenerate the three MCP transcripts

`docs/spikes/mcp-item-7-transcripts/` are the demo evidence and all three are
pre-A5. `interaction3-impact-cone.txt` is the urgent one: it is pure reverse
`CALLS`, reports 78 symbols with 5 depth-1 callers, and A6.4 established that
`build_filter` now has exactly 3 real callers. Re-record all three against the
current graph, same prompts, same session shape. Keep the originals alongside
as `*-pre-a6.txt` — the delta is itself evidence that the fix reached the
product surface, not just the graph.

---

## Task 4 — A6 status banners

At the top of `docs/spikes/mcp-item-7-results.md` and
`docs/spikes/emit-item-6-results.md`, add:

> **Superseded in part by Amendment A6.** Figures in this document were
> measured before the `occurrence_nodes()` containment fix. See
> `docs/specs/amendment-a6.md` for current values.

Do **not** rewrite the bodies. These are dated handoff records and the record
has value; a banner preserves it while stopping a reader treating stale
figures as current.

Fix the `README.md` "Example" paragraph, which quotes `24 symbols, ~6,100
tokens` — that is the pre-A5 figure including 2,169 tokens of fabricated
optional context. Replace with the A6.4 values.

---

## Task 5 — A representative worked example for the demo

A6.4's regenerated §8.1 example fills 4,715 of 8,000 tokens and admits one
optional symbol. That is 59% utilisation against a 200-trial median near 94%,
and it visibly contradicts the packing claim. The cause is that §8.1 uses two
seeds while the harness uses six, and A5.1 already found this pair's candidate
pool unusually thin.

Select a **6-seed** example for the README and video, by a rule fixed before
you look at results — e.g. the trial from the 200-run whose closure size is
closest to the post-A6 median. State the rule in the doc. Emit it in full to
`docs/spikes/worked-example-a6.md`.

Keep the 2-seed example too, labelled as the thin-pool case. Showing both is
stronger than showing the flattering one.

---

## Deliverables

- `docs/specs/amendment-a6.md` gains A6.5 (pre-registration, written first)
  and A6.6 (the Task 2 measurements).
- `docs/spikes/worked-example-a6.md`, 6-seed and 2-seed.
- Three regenerated transcripts, originals retained.
- Banners on two spike docs; README example corrected.
- Report the A6.5 outcome explicitly as held / shifted-with-mechanism / broke.

## Constraints

- Never substitute Neo4j or mock HydraDB.
- No background polling loops or scheduled wakeups. Long runs go to a log;
  read the log.
- Report numbers as measured. No tuning to preserve a prior figure.
- If any phase exceeds its share of the 3 hours, stop and report.