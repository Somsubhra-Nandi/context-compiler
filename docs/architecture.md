# Context Compiler architecture

The repository evaluates three retrieval strategies behind a shared context
output and agent/evaluation flow. Arm C is the Context Compiler production
path; Arms A and B are controlled baselines.

```mermaid
flowchart TB
    subgraph Inputs
        direction LR
        task["User task / bug report"]
        seeds["Explicit symbol seeds"]
    end

    subgraph Build["Offline build"]
        direction LR
        symbols["symbols.jsonl / repr_L2_text"]
        edges["edges.jsonl"]
        vector_build["Embedding / index build"]
        ingest["HydraDB ingest"]
        embedding_index["Embedding index"]
        graph["HydraDB graph"]
        symbols --> vector_build --> embedding_index
        symbols --> ingest
        edges --> ingest --> graph
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
    source_map["Source / offset mapping"]

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

    embedding_index --> arm_a
    graph --> arm_b
    graph --> expand
    source_map -.-> output

    arm_a --> output
    arm_b --> output
    pack --> output
    output --> coding_agent
```

Arm A accepts caller-resolved symbol seeds and uses the embedding index without
graph traversal. Arm B ranks a one-hop HydraDB graph neighborhood but does not
enforce structural closure. Arm C is the production Context Compiler path: it
resolves task text or explicit seeds, compiles a structurally closed graph slice,
and packs it under the token budget. The shared emitter then produces the
context consumed by the coding agent and evaluation flow.
