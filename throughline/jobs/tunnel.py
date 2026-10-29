#!/usr/bin/env python3
"""A standing link to the other machine, over one SSH connection.

Bidirectional replication needs each node to reach the other's PostgreSQL.
Two ways not to do that:

* Open the database port on the LAN. This corpus is served by a `trust`-
  configured PostgreSQL — whoever reaches the port is in, without a password.
* Run an SSH server on both machines, so each can dial the other. That means
  enabling remote login on both, and maintaining it on both.

One SSH connection carries both directions. ``-L`` makes the peer's database
reachable here; ``-R`` makes ours reachable there, over the same socket. Only
the machine being dialled needs a server, and both forwards stay on loopback,
so neither database is ever exposed to the network.

Usage::

    throughline tunnel --host otherhost.local --user alex
    throughline tunnel --host … --user … --bridge-port 5434

It does not daemonise. Supervision belongs to launchd or systemd, which
already know how to restart something when it dies and to back off when the
other machine is simply not there — see ``launchd/``.
"""

from __future__ import annotations

#: Where each side reaches the other. Deliberately not the local database
#: port: a tunnel that shadowed 5433 would make "which database am I talking
#: to" unanswerable from a connection string alone.
DEFAULT_BRIDGE_PORT = 5434

#: How long a dead link may look alive. Three missed probes at fifteen seconds
#: is under a minute, which is faster than a replication subscription notices
#: and slow enough not to drop a link over a hiccup.
ALIVE_INTERVAL = 15
ALIVE_COUNT_MAX = 3


def ssh_command(
    *,
    host: str,
    user: str,
    peer_port: int = 5433,
    local_port: int = 5433,
    bridge_port: int = DEFAULT_BRIDGE_PORT,
    identity: str | None = None,
) -> list[str]:
    """The ssh invocation that opens both directions and nothing else."""
    for name, port in (
        ("peer_port", peer_port),
        ("local_port", local_port),
        ("bridge_port", bridge_port),
    ):
        if not 1 <= int(port) <= 65535:
            raise ValueError(f"{name} is outside the valid port range: {port}")

    argv = [
        "ssh",
        "-N",  # forwards only; no shell, no command
        # A forward that could not bind must take the connection down with it.
        # Without this ssh stays up, the supervisor sees a live process, and
        # replication sees nothing.
        "-o",
        "ExitOnForwardFailure=yes",
        # Never wait for a human: this runs from a launch agent, where a
        # password or host-key prompt hangs forever and looks like a working
        # tunnel.
        "-o",
        "BatchMode=yes",
        "-o",
        f"ServerAliveInterval={ALIVE_INTERVAL}",
        "-o",
        f"ServerAliveCountMax={ALIVE_COUNT_MAX}",
        # Both forwards bind to loopback. Binding to 0.0.0.0 would publish the
        # database to the whole network, which is what this exists to avoid.
        "-L",
        f"{bridge_port}:127.0.0.1:{peer_port}",
        "-R",
        f"{bridge_port}:127.0.0.1:{local_port}",
    ]
    if identity:
        argv += ["-i", identity]
    argv.append(f"{user}@{host}")
    return argv


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(
        prog="throughline tunnel",
        description="Hold open a loopback-only link to the other machine's PostgreSQL.",
    )
    parser.add_argument("--host", required=True, help="The other machine, e.g. otherhost.local.")
    parser.add_argument("--user", required=True, help="Login on the other machine.")
    parser.add_argument("--peer-port", type=int, default=5433, help="Its PostgreSQL port (default: 5433).")
    parser.add_argument("--local-port", type=int, default=5433, help="This machine's PostgreSQL port.")
    parser.add_argument(
        "--bridge-port",
        type=int,
        default=DEFAULT_BRIDGE_PORT,
        help=f"Where each side reaches the other (default: {DEFAULT_BRIDGE_PORT}).",
    )
    parser.add_argument("--identity", default=None, help="SSH key to use.")
    parser.add_argument("--print", action="store_true", dest="show", help="Print the command and exit.")
    args = parser.parse_args(argv)

    try:
        command = ssh_command(
            host=args.host,
            user=args.user,
            peer_port=args.peer_port,
            local_port=args.local_port,
            bridge_port=args.bridge_port,
            identity=args.identity,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.show:
        print(" ".join(command))
        return 0

    print(
        f"local  127.0.0.1:{args.bridge_port} -> {args.host}:{args.peer_port}\n"
        f"remote 127.0.0.1:{args.bridge_port} -> this machine:{args.local_port}",
        file=sys.stderr,
    )
    os.execvp(command[0], command)


if __name__ == "__main__":
    raise SystemExit(main())
