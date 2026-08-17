# Extraction JSONL Contract

This is the frozen interface between extraction and graph ingestion for Items 1–2.
Both files are UTF-8 JSON Lines: one compact JSON object per line, terminated by
`\n`, ordered deterministically as specified below.

## `symbols.jsonl`

Symbols are emitted in ascending `fqn` order. IDs are assigned in that same order.

```json
{"id":4611686018427387904,"fqn":"requests.sessions.Session.get","kind":"method","file":"requests/sessions.py","start_line":542,"end_line":561,"body_hash":"sha256:ab12…","repr_L2_text":"def get(self, url, **kwargs) -> Response: ...","repr_L2_tokens":47,"repr_L2_refs":[123,456],"repr_L3_text":"from .models import Response\n\ndef get(self, …","repr_L3_tokens":312,"repr_L3_refs":[123,456,789],"identity_tokens":11,"provenance_tokens":15,"evaluable":null,"static_value":null,"mro_partial":false}
```

Required fields and types:

| Field | Type | Meaning |
|---|---:|---|
| `id` | integer | Non-negative deterministic 63-bit node ID |
| `fqn` | string | Application-unique fully qualified name |
| `kind` | string | `function`, `method`, `class`, `constant`, `module`, or `test` |
| `file` | string | POSIX path relative to repository root |
| `start_line` | integer | One-based inclusive source line |
| `end_line` | integer | One-based inclusive source line |
| `body_hash` | string | `sha256:` followed by the canonical source-body digest |
| `repr_L2_text` | string | Canonical declaration representation |
| `repr_L2_tokens` | integer | Token count of exactly `repr_L2_text` |
| `repr_L2_refs` | array[integer] | IDs of symbols textually referenced by L2 |
| `repr_L3_text` | string | Canonical full-body representation |
| `repr_L3_tokens` | integer | Token count of exactly `repr_L3_text` |
| `repr_L3_refs` | array[integer] | IDs of symbols textually referenced by L3 |
| `identity_tokens` | integer | Token cost of this symbol's identity line |
| `provenance_tokens` | integer | Token cost of this symbol's provenance trailer |
| `evaluable` | boolean or null | Constant-fold result; null for non-constants |
| `static_value` | string or null | Folded constant literal; null otherwise, and null when over the A2.2 cap |
| `static_value_bytes` | integer or null | Size of the folded literal, present **only** when it exceeded the cap |
| `mro_partial` | boolean | Whether class MRO flattening fell back to own members |

`node_id(fqn)` is:

```python
int.from_bytes(blake2b(fqn.encode(), digest_size=8).digest(), "big") >> 1
```

Collisions probe `id + 1`. Probing is deterministic because symbols are processed
in sorted-FQN order. Token counts use `cl100k_base` through the extraction layer's
single `count_tokens(text)` function. `repr_*_refs` contain unique IDs in ascending
order. Constants alone use non-null `evaluable`; `static_value` is non-null only
when `evaluable` is true.

### Folded constants are capped at 4 KB (Amendment A2.2)

```
folded literal <= 4,096 bytes  ->  evaluable: true,  static_value: "<literal>",
                                   static_value_bytes: null
folded literal >  4,096 bytes  ->  evaluable: true,  static_value: null,
                                   static_value_bytes: <int>
not evaluable                  ->  evaluable: false, static_value: null,
                                   static_value_bytes: null
```

**`evaluable` stays `true` above the cap.** The constant *is* statically
evaluable; the extractor is declining to inline it. Downgrading it to `false`
would misdescribe the code and would change which Sec 4 propagation row applies.

**The literal is never truncated.** A clipped constant looks valid and is wrong,
and I4's guarantee that token counts describe the canonical emitted
representation depends on stored text being exactly what was costed. When the
value is omitted, `repr_L2_text` carries the defining expression plus a
`# folded value omitted: N bytes` comment, so the model learns what the constant
is without paying for its contents.

Size is measured in **UTF-8 bytes**, not characters. One Django constant
(`tests.validators.tests.INVALID_URLS`) folds to 66,517 bytes and broke the
default ingest before this cap existed.

## `edges.jsonl`

Edges are emitted in ascending `(type, src, dst)` order. Duplicate occurrences are
coalesced. `CALLS` carries the aggregated number of syntactic call sites.

```json
{"type":"CALLS","src":4611686018427387904,"dst":1152921504606846976,"resolver":"ast+scip","confidence":0.95,"call_sites":3}
```

Required fields and types:

| Field | Type | Meaning |
|---|---:|---|
| `type` | string | Relation type listed below |
| `src` | integer | Existing source symbol ID |
| `dst` | integer | Existing destination symbol ID |
| `resolver` | string | `scip-python`, `ast+scip`, or `tree-sitter` |
| `confidence` | number | Resolution confidence in `[0, 1]` |
| `call_sites` | integer | Present only for `CALLS` |
| `mandatory` | boolean | Present and false only for `INHERITS_FROM` |

Mandatory relation types are `REFERENCES_TYPE`, `CALLS`, `OVERRIDES`,
`IMPLEMENTS`, `DECORATED_BY`, and `READS_CONSTANT`. `INHERITS_FROM` may also be
emitted for display and must carry `"mandatory": false`.

Edges never contain an `id`. Graph ingestion derives static relationship identity
from `(type, src, dst)`. Every endpoint and every `repr_*_refs` entry must resolve
to an ID in the same `symbols.jsonl` artifact.
