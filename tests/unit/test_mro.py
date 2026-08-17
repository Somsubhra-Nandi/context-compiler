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


# -- Amendment A4.3: dunders/privates inherited from a base are dropped -----


def test_inherited_dunder_is_dropped():
    bases = {"A": [], "B": ["A"]}
    members = {"A": ["def __rxor__(self, other): ..."], "B": []}
    surface, _ = flatten_members("B", bases, members)
    assert surface == []


def test_inherited_private_method_is_dropped():
    bases = {"A": [], "B": ["A"]}
    members = {"A": ["def _internal(self): ..."], "B": []}
    surface, _ = flatten_members("B", bases, members)
    assert surface == []


def test_inherited_public_method_is_kept():
    bases = {"A": [], "B": ["A"]}
    members = {"A": ["def process(self): ..."], "B": []}
    surface, _ = flatten_members("B", bases, members)
    assert surface == [("def process(self): ...", "A")]


def test_own_dunder_and_private_members_are_kept():
    """Sec 3.2: a class's own members are never filtered, regardless of name."""
    bases = {"A": [], "B": ["A"]}
    members = {
        "A": ["def _internal(self): ..."],
        "B": ["def __eq__(self, other): ...", "def _own_helper(self): ..."],
    }
    surface, _ = flatten_members("B", bases, members)
    assert surface == [
        ("def __eq__(self, other): ...", "B"),
        ("def _own_helper(self): ...", "B"),
    ]
