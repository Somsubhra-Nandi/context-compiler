from pathlib import Path

from context_compiler.extract.ast_occurrences import discover_file
from context_compiler.extract.representations import canonical_l2, canonical_l3


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
