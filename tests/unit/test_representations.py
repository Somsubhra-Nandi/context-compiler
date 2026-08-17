import ast
from pathlib import Path

import pytest

from context_compiler.extract.ast_occurrences import discover_file
from context_compiler.extract.representations import (
    canonical_l2,
    canonical_l3,
    docstring_literal,
)


def test_method_has_class_header_and_only_needed_import(tmp_path: Path):
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text("import os\nimport sys\n\nclass C:\n    def f(self) -> str:\n        return os.name\n")
    symbols, _ = discover_file(tmp_path, path)
    method = next(symbol for symbol in symbols if symbol.fqn.endswith("C.f"))
    l2, l3 = canonical_l2(method), canonical_l3(method)
    assert "class C:" in l2 and "class C:" in l3
    assert "import os" in l3
    assert "import sys" not in l3


def test_docstring_with_a_trailing_quote_still_parses(tmp_path: Path):
    """Found by Item 6's ast.parse() check on assembled output.

    Two Django symbols document themselves with a phrase ending in a quoted
    word -- `munge the "where"` -- and naive triple-quoting produces an
    unterminated literal that makes the whole declaration unparseable.
    """
    path = tmp_path / "pkg" / "quoted.py"
    path.parent.mkdir()
    path.write_text(
        'class C:\n'
        '    def f(self):\n'
        '        """Munge the "where"\n\n        More text.\n        """\n'
        '        return 1\n'
    )
    symbols, _ = discover_file(tmp_path, path)
    method = next(s for s in symbols if s.fqn.endswith("C.f"))
    l2 = canonical_l2(method)
    assert 'where' in l2
    ast.parse(l2)


@pytest.mark.parametrize(
    "doc",
    [
        'plain',
        'ends with a quote"',
        'has """ inside',
        r"has a \ backslash",
        "has 'single' quotes",
        'has "double" quotes mid-line',
    ],
)
def test_docstring_literal_is_always_valid_python(doc):
    literal = docstring_literal(doc)
    parsed = ast.parse(literal)
    assert isinstance(parsed.body[0], ast.Expr)
    assert ast.literal_eval(parsed.body[0].value) == doc


def test_docstring_literal_prefers_triple_quotes_when_safe():
    assert docstring_literal("Issue a token.") == '"""Issue a token."""'


def test_method_body_is_indented_once_not_twice(tmp_path: Path):
    """`get_source_segment` dedents only the first line; the rest must follow.

    Before this, every Django method body sat four columns deeper than its own
    `def`. Valid Python, but wrong, and visible in the worked example.
    """
    path = tmp_path / "pkg" / "deep.py"
    path.parent.mkdir()
    path.write_text(
        "class C:\n"
        "    def f(self):\n"
        "        x = 1\n"
        "        if x:\n"
        "            return x\n"
        "        return 0\n"
    )
    symbols, _ = discover_file(tmp_path, path)
    method = next(s for s in symbols if s.fqn.endswith("C.f"))
    l3 = canonical_l3(method)
    assert "    def f(self):" in l3
    assert "        x = 1" in l3
    assert "            x = 1" not in l3, "body indented twice"
    assert "            return x" in l3, "nesting inside the body is preserved"
    ast.parse(l3)


def test_module_level_function_body_is_untouched(tmp_path: Path):
    path = tmp_path / "pkg" / "flat.py"
    path.parent.mkdir()
    path.write_text("def g():\n    y = 2\n    return y\n")
    symbols, _ = discover_file(tmp_path, path)
    fn = next(s for s in symbols if s.fqn.endswith("flat.g"))
    l3 = canonical_l3(fn)
    assert l3.startswith("def g():\n    y = 2")
    ast.parse(l3)
