from __future__ import annotations

import logging

from db.store_support import dump_json, load_json_dict, load_json_list


def test_store_support_logs_and_falls_back_for_invalid_json(caplog) -> None:
    caplog.set_level(logging.WARNING)

    encoded = dump_json({1, 2, 3}, default="[]", context="test.dump")
    parsed = load_json_dict("{bad json", context="test.load")

    assert encoded == "[]"
    assert parsed == {}
    assert "store.json_encode_fallback" in caplog.text
    assert "store.json_decode_object_fallback" in caplog.text


def test_store_support_type_mismatch_returns_default_shapes(caplog) -> None:
    caplog.set_level(logging.WARNING)

    parsed_list = load_json_list('{"a": 1}', context="test.list")
    parsed_dict = load_json_dict("[1, 2]", context="test.dict")

    assert parsed_list == []
    assert parsed_dict == {}
    assert "store.json_decode_list_type_mismatch" in caplog.text
    assert "store.json_decode_object_type_mismatch" in caplog.text
