# Context Compiler architecture

Context Compiler compares three retrieval strategies behind a shared context
output and agent/evaluation flow.

```mermaid
flowchart TB
    subgraph Inputs
        direction LR
        task["User task / bug report"]
        seeds["Explicit symbol seeds"]
    end

    subgraph Data["Shared data sources"]
        direction LR
        symbols["Symbol sidecar"]
        edges["Edge sidecar"]
        offsets["Offsets / source mapping"]
        embeddings["Embedding index"]
        graph["HydraDB graph"]
    end

    subgraph Paths["Retrieval / compilation paths"]
        direction LR
        arm_a["Arm A<br/>Vector similarity retrieval"]
        arm_b["Arm B<br/>Graph-ranked top-k<br/>(no closure)"]

        subgraph arm_c["Arm C — Context Compiler"]
            direction TB
            resolve["Seed resolution"]
            expand["Graph traversal /<br/>structural expansion"]
            closure["Structural closure /<br/>profile selection"]
            pack["Budgeted packing"]
            resolve --> expand --> closure --> pack
        end
    end

    output["Rendered context<br/>Token-budgeted output<br/>Identity hints / metadata"]

    subgraph Agent["Agent / evaluation layer"]
        direction LR
        coding_agent["Coding agent uses context"]
        patch["Patch generation"]
        evaluation["Regression tests /<br/>benchmark evaluation"]
        coding_agent --> patch --> evaluation
    end

    task --> resolve
    seeds --> arm_a
    seeds --> arm_b
    seeds --> resolve

    symbols -.-> arm_a
    symbols -.-> arm_b
    symbols -.-> resolve
    edges -.-> arm_b
    edges -.-> expand
    offsets -.-> output
    embeddings --> arm_a
    graph --> arm_b
    graph --> expand

    arm_a --> output
    arm_b --> output
    pack --> output
    output --> coding_agent
```

Arm A accepts caller-resolved symbol seeds and uses the embedding index without
graph traversal. Arm B ranks a one-hop graph neighborhood but does not enforce
structural closure. Arm C resolves task text or explicit seeds, compiles a
structurally closed graph slice, and packs it under the token budget. The shared
emitter then produces the context consumed by the coding agent and evaluation
flow.
