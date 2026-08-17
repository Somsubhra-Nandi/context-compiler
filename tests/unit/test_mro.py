from context_compiler.extract.mro import c3_linearize, flatten_members


def test_diamond():
    bases = {"O": [], "A": ["O"], "B": ["O"], "C": ["A", "B"]}
    assert c3_linearize("C", bases) == ["C", "A", "B", "O"]


def test_three_deep_chain():
    assert c3_linearize("C", {"A": [], "B": ["A"], "C": ["B"]}) == ["C", "B", "A"]


def test_unresolvable_base_is_partial():
    surface, partial = flatten_members("C", {"C": ["Missing"]}, {"C": ["def own(self): ..."]})
    assert partial
    assert surface == [("def own(self): ...", "C")]
