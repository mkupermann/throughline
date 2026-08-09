"""Unit tests for pure helpers in scripts/reflect_memory.py.

These exercise the JSON-parsing and row-unwrapping logic that every reflection
mode depends on, with no database required.
"""

import reflect_memory as rm


class TestParseJsonObject:
    def test_plain_object(self):
        assert rm.parse_json_object('{"a": 1, "b": 2}') == {"a": 1, "b": 2}

    def test_markdown_fenced(self):
        text = '```json\n{"decision": "use pgvector"}\n```'
        assert rm.parse_json_object(text) == {"decision": "use pgvector"}

    def test_object_with_trailing_prose(self):
        # The model often appends an explanation after the JSON.
        text = '{"verdict": "merge"}\n\nI chose merge because the two are dupes.'
        assert rm.parse_json_object(text) == {"verdict": "merge"}

    def test_two_sibling_objects_returns_first(self):
        # Regression: rfind("}") used to span both objects -> invalid JSON -> None.
        text = '{"first": true} {"second": false}'
        assert rm.parse_json_object(text) == {"first": True}

    def test_leading_prose_before_object(self):
        text = 'Here is the result: {"ok": 1}'
        assert rm.parse_json_object(text) == {"ok": 1}

    def test_nested_object_preserved(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        assert rm.parse_json_object(text) == {"outer": {"inner": [1, 2, 3]}}

    def test_empty_and_none(self):
        assert rm.parse_json_object("") is None
        assert rm.parse_json_object("   ") is None

    def test_no_object(self):
        assert rm.parse_json_object("just some words") is None

    def test_malformed_object(self):
        assert rm.parse_json_object('{"a": ') is None

    def test_array_is_not_an_object(self):
        # A top-level array is valid JSON but not a dict; callers expect a dict.
        assert rm.parse_json_object('[1, 2, 3]') is None


class TestFirstVal:
    def test_tuple_row(self):
        assert rm._first_val((42, "x")) == 42

    def test_dict_row(self):
        assert rm._first_val({"count": 7, "other": 9}) == 7

    def test_none_row(self):
        assert rm._first_val(None) is None

    def test_empty_dict_does_not_raise(self):
        # Regression: next(iter({}.values())) raised StopIteration.
        assert rm._first_val({}) is None

    def test_empty_tuple_does_not_raise(self):
        assert rm._first_val(()) is None
