"""Adapter registry contract — discovery + name uniqueness."""

from __future__ import annotations

from throughline.adapters import Adapter, all_adapters, get_adapter


def test_registry_returns_builtin_adapters():
    adapters = all_adapters()
    names = {a.name for a in adapters}
    # The 5 ship-now adapters. Asserting their names so a typo or
    # accidental rename breaks the test rather than silently dropping a
    # source on someone's machine.
    assert {"claude_code", "windsurf", "hermes", "codex", "continue"} <= names


def test_every_registered_adapter_subclasses_base():
    for a in all_adapters():
        assert isinstance(a, Adapter), f"{a!r} is not an Adapter"


def test_adapter_names_are_unique():
    names = [a.name for a in all_adapters()]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


def test_get_adapter_known_name():
    a = get_adapter("hermes")
    assert a is not None
    assert a.name == "hermes"
    assert a.home.name == "sessions"


def test_get_adapter_unknown_returns_none():
    assert get_adapter("does-not-exist") is None


def test_adapter_homes_use_user_directory():
    """Every adapter's home should live somewhere under the user's homedir.

    Catches the easy mistake of leaving a tilde in the path string
    (Path("~/.foo") versus Path("~/.foo").expanduser()).
    """
    import os

    home = os.path.expanduser("~")
    for a in all_adapters():
        assert str(a.home).startswith(home), (
            f"{a.name}.home={a.home!r} is not under {home!r} — "
            "did you forget to .expanduser()?"
        )
