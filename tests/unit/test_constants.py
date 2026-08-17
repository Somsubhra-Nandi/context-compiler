import ast

from context_compiler.extract.constants import (
    MAX_STATIC_VALUE_BYTES,
    Folded,
    fold_constants,
    render_folded,
)


def expressions(source: str):
    tree = ast.parse(source)
    return {stmt.targets[0].id: stmt.value for stmt in tree.body if isinstance(stmt, ast.Assign)}


def test_literal_and_folded_chain():
    folded = fold_constants(expressions("BASE = 30\nTIMEOUT = BASE * 2"))
    assert folded["BASE"].value == 30
    assert folded["TIMEOUT"].value == 60


def test_dynamic_environment_is_not_evaluable():
    folded = fold_constants(expressions('TIMEOUT = int(os.getenv("TIMEOUT", "30"))'))
    assert not folded["TIMEOUT"].evaluable


def test_cycle_is_not_evaluable():
    folded = fold_constants(expressions("A = B\nB = A"))
    assert not folded["A"].evaluable
    assert not folded["B"].evaluable


# -- Amendment A2.2: the 4 KB cap on folded values ----------------------


def test_small_value_is_inlined():
    r = render_folded(Folded(True, 30))
    assert r.evaluable and r.inlined and not r.over_cap
    assert r.literal == "30"
    assert r.size_bytes == 2


def test_oversized_value_stays_evaluable_but_is_not_inlined():
    """A2.2: we decline to inline; we do not claim the constant is dynamic."""
    r = render_folded(Folded(True, ["https://bad.example"] * 500))
    assert r.evaluable is True  # the constant IS statically evaluable
    assert r.literal is None
    assert r.over_cap
    assert r.size_bytes > MAX_STATIC_VALUE_BYTES


def test_oversized_value_is_never_truncated():
    """A clipped constant looks valid and is wrong."""
    value = ["x" * 100] * 200
    r = render_folded(Folded(True, value))
    assert r.literal is None
    assert r.size_bytes == len(repr(value).encode())


def test_cap_boundary_is_exact():
    under = "a" * (MAX_STATIC_VALUE_BYTES - 2)  # repr adds two quotes
    assert len(repr(under).encode()) == MAX_STATIC_VALUE_BYTES
    assert render_folded(Folded(True, under)).inlined

    over = "a" * (MAX_STATIC_VALUE_BYTES - 1)
    assert len(repr(over).encode()) == MAX_STATIC_VALUE_BYTES + 1
    assert render_folded(Folded(True, over)).over_cap


def test_size_is_measured_in_bytes_not_characters():
    value = "é" * 3000  # 1 char, 2 bytes each in UTF-8
    r = render_folded(Folded(True, value))
    assert len(value) < MAX_STATIC_VALUE_BYTES  # under cap by character count
    assert r.over_cap  # over cap by byte count


def test_non_evaluable_renders_as_absent():
    for folded in (None, Folded(False)):
        r = render_folded(folded)
        assert r.evaluable is False
        assert r.literal is None
        assert not r.over_cap
