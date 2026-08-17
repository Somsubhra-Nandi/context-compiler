import ast

from context_compiler.extract.constants import fold_constants


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
