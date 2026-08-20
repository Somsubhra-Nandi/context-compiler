# B1 Controlled Agent Case Study — Nullable Exclusion Regression

## Scope

This is a controlled agent case study (`n = 1`), not a statistical benchmark.

It is intended to illustrate how Context Compiler can affect an agent's debugging path and final correctness on one real codebase regression. Aggregate compiler measurements such as P3 hit rate and closure statistics are reported separately.

## Task

Diagnose and fix a Django ORM regression where:

    Tag.objects.exclude(parent__annotation__name="a1")

incorrectly returns:

    [t4, t5]

instead of:

    [t1, t4, t5]

`t1` has no parent and must remain in the result.

Focused regression test:

    queries.tests.Queries6Tests.test_tickets_8921_9188

Broken fixture commit:

    26c6738ecb8aa3519c17dedb7f4a3515d7e910c1

## Controlled Setup

All primary conditions used:

    Model: gpt-5.6-luna
    Reasoning effort: low
    Codex CLI: 0.147.0

All primary conditions started from the same broken commit and received the same natural-language user task.

All primary conditions used the same project-level agent workflow instructions:

    AGENTS.md SHA256:
    0f309d5bb7b54bef8552ac675a5bcd588af16082acb3b71feec38bc22d0592a8

The workflow instructed the agent to perform minimal initial localization, identify concrete symbols, and then:

- use Context Compiler with explicit symbol seeds if available;
- otherwise continue normal repository investigation from the same localized path.

Context Compiler was enabled only in the Compiler arm. The vector-only MCP was available only in the Vector-enabled arm; the baseline had no retrieval MCP.

Repository isolation rules prohibited using sibling worktrees, other checkouts, Git history, or another repository to locate the regression.

---

## Context Compiler Arm

### Agent Localization

After minimal inspection, the agent selected these seeds itself:

    django.db.models.sql.query.Query.split_exclude
    django.db.models.sql.query.Query.build_filter
    django.db.models.query.QuerySet._filter_or_exclude

The natural-language user prompt did not provide these seeds.

### Context Compiler Call

Budget:

    8000 tokens

Result:

    Status: OK
    Profile: P3 FULL
    Emitted symbols: 32
    Closure size: 57
    Declarations: 29
    Bodies: 3
    Identities: 25
    Budgeted tokens: 7,139
    Actual context tokens: 6,659
    Graph round trips: 21
    Compiler latency: 1,312.1 ms

The compiled structural context was used as navigation, after which the agent verified the current source.

### Diagnosis

The agent reached `Query.trim_start()`.

The function computes `contains_louter` while trimming the query prefix, but the broken fixture returned `True` unconditionally.

### Patch

The agent produced the one-line root-cause repair:

    - return trimmed_prefix, True
    + return trimmed_prefix, contains_louter

Actual Codex-produced patch artifact:

[`compiler.patch`](compiler.patch)

The raw interactive Codex session was retained locally as provenance and is not checked into this repository.

### Verification

Focused regression test:

    PASS

Entire `Queries6Tests` class:

    9 / 9 PASS

Full `queries` suite:

    505 tests run
    0 failures
    14 skipped
    1 expected failure

### Codex Token Usage

    Total: 38,949
    Input: 37,546
    Cached input: 260,352
    Output: 1,403
    Reasoning output: 174

---

## Vector-enabled Arm

Context Compiler was absent. The vector-only MCP was available.

### Retrieval Behavior and Diagnosis

The agent performed ordinary repository exploration and did not invoke the vector MCP. It localized the problem downstream in `Query.split_exclude()` and produced a nullable-prefix-specific boolean rewrite.

### Patch

The preserved agent patch changes the NULL predicate and connector only when the filter right-hand side is nullable:

    + nullable_prefix = filter_rhs is not None
    - ("%s__isnull" % trimmed_prefix, True)
    + ("%s__isnull" % trimmed_prefix, not nullable_prefix)

    - condition.add(or_null_condition, OR)
    + condition.add(or_null_condition, AND if nullable_prefix else OR)

Patch artifact:

[`vector-enabled.patch`](vector-enabled.patch)

### Verification

Focused regression test:

    PASS

Entire `Queries6Tests` class:

    9 / 9 PASS

Full `queries` suite, rerun single-process:

    505 tests run
    2 failures
    14 skipped
    1 expected failure

Failures:

    queries.tests.ExcludeTests.test_exclude_m2m_through
    queries.tests.ManyToManyExcludeTest.test_ticket_12823

The patch fixed the narrow symptom but still changed exclusion semantics elsewhere.

### Codex Token Usage

    Total: 67,506
    Input: 63,585
    Cached input: 402,944
    Output: 3,921
    Reasoning output: 1,733

---

## Baseline Arm

Context Compiler was disabled.

The agent performed ordinary repository and SQL investigation from the same reported behavior.

### Diagnosis

The baseline agent localized the problem to `Query.split_exclude()` and concluded that the downstream boolean construction was incorrect.

### Patch

It changed the NULL predicate and boolean connector:

    - ("%s__isnull" % trimmed_prefix, True)
    + ("%s__isnull" % trimmed_prefix, False)

    - condition.add(or_null_condition, OR)
    + condition.add(or_null_condition, AND)

Patch artifact:

[`baseline.patch`](baseline.patch)

### Narrow Verification

Focused regression test:

    PASS

Entire `Queries6Tests` class:

    9 / 9 PASS

At this point both arms appeared successful under narrow testing.

### Full-Suite Verification

Full `queries` suite:

    505 tests run
    3 failures
    14 skipped
    1 expected failure

Failures:

    queries.tests.ExcludeTests.test_exclude_m2m_through
    queries.tests.ManyToManyExcludeTest.test_exclude_many_to_many
    queries.tests.ManyToManyExcludeTest.test_ticket_12823

The baseline patch therefore fixed the reported symptom but changed exclusion semantics elsewhere.

### Codex Token Usage

    Total: 18,564
    Input: 16,841
    Cached input: 154,368
    Output: 1,723
    Reasoning output: 566

---

## Primary Comparison

| Condition | Retrieval behavior | Repair location | Focused / `Queries6Tests` | Full `queries` suite |
|---|---|---|---|---|
| Context Compiler | MCP invoked | `Query.trim_start()` | PASS / 9 / 9 PASS | 505 tests, **0 failures** |
| Vector-enabled | Vector MCP available, not invoked | `Query.split_exclude()` | PASS / 9 / 9 PASS | 505 tests, **2 failures** |
| Baseline | No retrieval MCP | `Query.split_exclude()` | PASS / 9 / 9 PASS | 505 tests, **3 failures** |

| Usage / retrieval metric | Context Compiler | Vector-enabled | Baseline |
|---|---:|---:|---:|
| Codex total tokens | 38,949 | 67,506 | 18,564 |
| Codex reasoning tokens | 174 | 1,733 | 566 |
| Retrieval profile | P3 FULL | Vector MCP available, not invoked | N/A |
| Compiled context | 6,659 tokens | N/A | N/A |
| Compiler latency | 1,312.1 ms | N/A | N/A |

## Prediction vs. Observation

The initial expectation was that structural retrieval would primarily reduce agent search/token consumption.

That prediction was not supported by this case study.

The Context Compiler arm consumed more total Codex tokens than the baseline:

    38,949 vs. 18,564

The baseline therefore did not fail because it consumed more context or more total tokens. The vector-enabled condition also cannot support a token-efficiency claim: it is one trial, and the vector MCP was not invoked.

Instead, the observed separation was correctness.

The compiler-guided agent reached the upstream `Query.trim_start()` invariant and produced a one-line repair that passed the complete 505-test query suite.

The baseline agent produced a plausible downstream `Query.split_exclude()` workaround that passed the reported regression and all nine nearby tests, but introduced three regressions under broader verification.

Reasoning-output tokens moved in the opposite direction from total tokens for the Compiler and Baseline arms:

    Context Compiler: 174
    Baseline: 566

This single trial is not sufficient to claim a general reasoning-token reduction, and no reasoning-token comparison should be generalized from these primary conditions.

## Finding

For B1, the demonstrated benefit of Context Compiler is **root-cause correctness and regression avoidance**, not token efficiency.

All primary conditions could make the reported failing test pass.

Only broader verification exposed the difference:

    Context Compiler → upstream invariant repair → 0 regressions
    Baseline         → downstream symptom repair → 3 regressions

The vector-enabled result is a product/tool-use observation, not evidence about the result of applying vector context: the vector MCP was available but not invoked. The controlled retrieval-level comparison is reported separately in [the three-way vector / graph top-k / structural compiler evidence](../../docs/spikes/demo-three-way.md).

Because this is an `n = 1` controlled case study, it should be presented as illustrative agent evidence rather than combined with the project's larger-sample compiler statistics.

## Evidence Integrity

The final Compiler patch was captured directly from the clean Codex run before the worktree was reset or modified.

The interactive Compiler session was recorded during the clean run and retained locally as provenance; the raw terminal transcript is not checked into this repository.

A later forced-call exploratory run failed exact-FQN resolution before returning vector context and is excluded from the primary comparison.

The generated Compiler patch was captured directly after that run and is checked in as [`compiler.patch`](compiler.patch).

The full `queries` suite was then executed against that same modified worktree and completed with:

    505 tests
    0 failures
    14 skipped
    1 expected failure

No reconstructed Compiler patch is used in this final record.
