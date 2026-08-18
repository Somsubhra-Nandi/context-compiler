"""Amendment A6: a symbol's own scope excludes anything nested inside it."""

from __future__ import annotations

from pathlib import Path

from context_compiler.extract.ast_occurrences import discover_file, occurrence_nodes


def _by_suffix(symbols, suffix):
    return next(s for s in symbols if s.fqn.endswith(suffix))


def test_module_scope_does_not_see_a_call_nested_two_scopes_down(tmp_path: Path):
    """A5.2's failing case: a module must not claim a call made deep inside
    one of its classes' methods. Before A6, `ast.walk` + `continue` could not
    stop the walk from descending into `Foo.method_a`'s body."""
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "class Foo:\n"
        "    def method_a(self):\n"
        "        inner_call()\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    module_symbol = next(s for s in symbols if s.kind == "module")
    calls = {n.id for edge, n in occurrence_nodes(module_symbol) if edge == "CALLS"}
    assert "inner_call" not in calls


def test_class_scope_does_not_see_its_methods_calls(tmp_path: Path):
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "class Foo:\n"
        "    def method_a(self):\n"
        "        inner_call()\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    foo = _by_suffix(symbols, "Foo")
    calls = {n.id for edge, n in occurrence_nodes(foo) if edge == "CALLS"}
    assert calls == set()


def test_class_scope_still_sees_its_own_bases_and_decorators(tmp_path: Path):
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "@register\n"
        "class Foo(Base):\n"
        "    def method_a(self):\n"
        "        pass\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    foo = _by_suffix(symbols, "Foo")
    occs = occurrence_nodes(foo)
    assert ("REFERENCES_TYPE", "Base") == (occs[0][0], occs[0][1].id)
    assert any(e == "DECORATED_BY" and getattr(n, "id", None) == "register" for e, n in occs)


def test_class_scope_still_sees_calls_in_its_own_class_level_statements(tmp_path: Path):
    """A class-level assignment is the class's own scope, not a method's."""
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "class Foo:\n"
        "    x = compute_default()\n"
        "    def method_a(self):\n"
        "        pass\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    foo = _by_suffix(symbols, "Foo")
    calls = {n.id for edge, n in occurrence_nodes(foo) if edge == "CALLS"}
    assert calls == {"compute_default"}


def test_method_scope_still_sees_its_own_calls(tmp_path: Path):
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "class Foo:\n"
        "    def method_a(self):\n"
        "        inner_call()\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    method = _by_suffix(symbols, "Foo.method_a")
    calls = {n.id for edge, n in occurrence_nodes(method) if edge == "CALLS"}
    assert calls == {"inner_call"}


def test_module_scope_still_sees_a_real_module_level_call(tmp_path: Path):
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "PATTERN = compile_pattern()\n"
        "\n"
        "class Foo:\n"
        "    def method_a(self):\n"
        "        inner_call()\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    module_symbol = next(s for s in symbols if s.kind == "module")
    calls = {n.id for edge, n in occurrence_nodes(module_symbol) if edge == "CALLS"}
    assert calls == {"compile_pattern"}


def test_function_scope_does_not_see_calls_nested_inside_an_inner_function(tmp_path: Path):
    """Inner functions get no `Symbol` of their own, but their calls still
    must not be misattributed to the enclosing function."""
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "def outer():\n"
        "    own_call()\n"
        "    def inner():\n"
        "        inner_call()\n"
        "    return inner\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    outer = _by_suffix(symbols, "outer")
    calls = {n.id for edge, n in occurrence_nodes(outer) if edge == "CALLS"}
    assert calls == {"own_call"}


def test_function_own_annotations_and_decorators_still_seen(tmp_path: Path):
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(
        "@decorator\n"
        "def f(x: Param) -> Result:\n"
        "    return x\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    f = _by_suffix(symbols, ".f")
    occs = occurrence_nodes(f)
    ref_names = {n.id for e, n in occs if e == "REFERENCES_TYPE"}
    assert ref_names == {"Param", "Result"}
    assert any(e == "DECORATED_BY" and getattr(n, "id", None) == "decorator" for e, n in occs)
