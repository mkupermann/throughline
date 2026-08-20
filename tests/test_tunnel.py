"""The standing link between two machines on the same network.

Bidirectional replication needs each node to reach the other's PostgreSQL.
Doing that by opening a database port on the LAN would put an unauthenticated,
`trust`-configured 600 MB corpus on the network; doing it with two SSH servers
means enabling remote login on both machines. One SSH connection carries both
directions, so only one side needs a server at all.
"""

from __future__ import annotations

import pytest

from throughline.jobs.tunnel import ssh_command


def _argv(**kw):
    defaults = {"host": "framework.fritz.box", "user": "michael", "peer_port": 5433, "local_port": 5433}
    return ssh_command(**{**defaults, **kw})


def test_one_connection_carries_both_directions():
    argv = _argv(bridge_port=5434)

    # Ours reaches theirs …
    assert "-L" in argv
    assert "5434:127.0.0.1:5433" in argv
    # … and theirs reaches ours, over the same connection.
    assert "-R" in argv
    assert argv.count("5434:127.0.0.1:5433") == 2


def test_the_forwards_stay_on_loopback():
    # A forward bound to 0.0.0.0 would publish the database to the whole LAN,
    # which is the thing this exists to avoid.
    argv = _argv(bridge_port=5434)
    forwards = [argv[i + 1] for i, part in enumerate(argv) if part in ("-L", "-R")]
    assert all(f.startswith("5434:127.0.0.1:") for f in forwards)
    assert not any(f.startswith("0.0.0.0") or f.startswith("*") for f in forwards)


def test_a_half_open_tunnel_is_not_reported_as_healthy():
    # Without ExitOnForwardFailure ssh stays up when a forward could not bind,
    # so the supervisor sees a live process and replication sees nothing.
    assert "ExitOnForwardFailure=yes" in _argv()


def test_a_dead_link_is_noticed_rather_than_waited_on():
    argv = _argv()
    joined = " ".join(argv)
    assert "ServerAliveInterval=" in joined
    assert "ServerAliveCountMax=" in joined


def test_no_shell_is_requested():
    assert "-N" in _argv()


def test_it_never_waits_for_a_human():
    # It runs from a launch agent; a password or host-key prompt would hang
    # forever and look like a working tunnel.
    argv = _argv()
    assert "BatchMode=yes" in argv


def test_an_identity_file_is_passed_when_given():
    argv = _argv(identity="~/.ssh/id_ed25519_throughline")
    assert "-i" in argv
    assert any("id_ed25519_throughline" in part for part in argv)


def test_without_an_identity_no_empty_flag_is_emitted():
    argv = _argv(identity=None)
    assert "-i" not in argv


def test_the_destination_is_the_last_argument():
    assert _argv()[-1] == "michael@framework.fritz.box"


@pytest.mark.parametrize("port", [0, -1, 70000])
def test_a_port_outside_the_range_is_refused(port):
    with pytest.raises(ValueError):
        _argv(bridge_port=port)
