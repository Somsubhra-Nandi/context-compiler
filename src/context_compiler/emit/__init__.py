"""Item 6 -- emission (spec Sec 7).

``emit()`` renders Item 5's ``Context`` -- a level map, provenance, hints, a
profile and a status -- into the string the model actually receives. It does not
select, score or budget, and it never recomputes a cost: Item 5 owns the cost
model and two implementations of it would diverge silently.

Text comes from the ``symbols.jsonl`` byte-offset index, never from the graph
(Amendment A2.1).
"""
from .render import (
    FRAMING_FIXED,
    FRAMING_PER_EMITTED,
    FRAMING_PER_FILE,
    EmittedContext,
    ProvenanceStyle,
    RenderedBlock,
    SectionTokens,
    emit,
    framing_allowance,
    split_class_shell,
    split_imports,
    unresolved_references,
)
from .source import (
    MappingTextSource,
    OffsetTextSource,
    SeekStats,
    SymbolRecord,
    TextSource,
    load_offsets,
    source_from_symbols,
)

__all__ = [
    "FRAMING_FIXED",
    "FRAMING_PER_EMITTED",
    "FRAMING_PER_FILE",
    "EmittedContext",
    "RenderedBlock",
    "MappingTextSource",
    "OffsetTextSource",
    "ProvenanceStyle",
    "SectionTokens",
    "SeekStats",
    "SymbolRecord",
    "TextSource",
    "emit",
    "framing_allowance",
    "load_offsets",
    "source_from_symbols",
    "split_class_shell",
    "split_imports",
    "unresolved_references",
]
