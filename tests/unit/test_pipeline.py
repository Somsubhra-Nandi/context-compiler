from context_compiler.extract.pipeline import node_id


def test_node_id_is_deterministic():
    assert node_id("requests.sessions.Session.get") == node_id("requests.sessions.Session.get")
    assert node_id("requests.sessions.Session.get") >= 0
