from context_compiler.extract.scip_reader import symbol_to_fqn


PREFIX = "scip-python python requests rev "


def test_method():
    assert symbol_to_fqn(PREFIX + "`src.requests.sessions`/Session#get().") == "requests.sessions.Session.get"


def test_nested_class():
    assert symbol_to_fqn(PREFIX + "`src.pkg.mod`/Outer#Inner#") == "pkg.mod.Outer.Inner"


def test_module_function():
    assert symbol_to_fqn(PREFIX + "`src.requests.api`/request().") == "requests.api.request"


def test_init_is_preserved():
    assert symbol_to_fqn(PREFIX + "`src.requests.sessions`/Session#__init__().") == "requests.sessions.Session.__init__"


def test_local_has_no_fqn():
    assert symbol_to_fqn("local 12") is None
