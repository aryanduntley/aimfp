"""--compact-returns (task 40; svamanas item 9)."""
import json

import pytest

from aimfp.mcp_server import server
from aimfp.mcp_server.serialization import compact_return_pointer, compact_return_statements
from aimfp.mcp_server.server import (
    _effect_compact_payload,
    _effect_load_and_cache_tools,
    handle_call_tool,
    set_compact_returns,
)


@pytest.fixture(autouse=True)
def _reset():
    set_compact_returns(False)
    yield
    set_compact_returns(False)


def _statements(resp):
    return json.loads(resp["result"]["content"][0]["text"])["return_statements"]


def _call(i):
    return handle_call_tool(i, {"name": "get_directive_by_name",
                                "arguments": {"directive_name": "aimfp_run"}})


class TestPure:
    def test_first_full_then_pointer(self):
        payload = {"success": True, "return_statements": ["a", "b"]}
        first, sent = compact_return_statements("t", payload, frozenset())
        assert first == payload
        second, sent2 = compact_return_statements("t", payload, sent)
        assert second["return_statements"] == [compact_return_pointer("t")]
        assert second["success"] is True and sent2 == sent

    def test_changed_set_is_sent_in_full(self):
        _, sent = compact_return_statements("t", {"return_statements": ["a"]}, frozenset())
        out, _ = compact_return_statements("t", {"return_statements": ["a", "custom"]}, sent)
        assert out["return_statements"] == ["a", "custom"]

    def test_same_statements_other_tool_sent_in_full(self):
        _, sent = compact_return_statements("t1", {"return_statements": ["a"]}, frozenset())
        out, _ = compact_return_statements("t2", {"return_statements": ["a"]}, sent)
        assert out["return_statements"] == ["a"]

    def test_empty_and_non_dict_untouched(self):
        assert compact_return_statements("t", {"return_statements": []}, frozenset())[0] == \
            {"return_statements": []}
        assert compact_return_statements("t", "text", frozenset())[0] == "text"


class TestServer:
    def test_off_by_default(self):
        _effect_load_and_cache_tools()
        assert _statements(_call(1)) == _statements(_call(2))
        assert len(_statements(_call(3))) > 1

    def test_on_sends_pointer_after_first(self):
        _effect_load_and_cache_tools()
        set_compact_returns(True)
        full = _statements(_call(1))
        assert len(full) > 1
        assert _statements(_call(2)) == [compact_return_pointer("get_directive_by_name")]

    def test_new_session_resets(self):
        set_compact_returns(True)
        payload = {"return_statements": ["x"]}
        _effect_compact_payload("aimfp_run", {"is_new_session": True}, payload)
        assert _effect_compact_payload("aimfp_run", {"is_new_session": False}, payload)[
            "return_statements"] == [compact_return_pointer("aimfp_run")]
        assert _effect_compact_payload("aimfp_run", {"is_new_session": True}, payload) == payload

    def test_cli_flag(self):
        from aimfp.__main__ import _apply_server_flags
        _apply_server_flags(("--compact-returns",))
        assert server._compact_returns_enabled is True
