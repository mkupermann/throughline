"""Operator-only account bootstrap/recovery. Passwords never enter command arguments."""

import getpass
import os
from pathlib import Path

import psycopg2

from throughline.api.access import password_hash
from throughline.config import get_db_config
from throughline.queries._exec import one


def manage(args):
    username = args.username.lower()
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9._@-]{0,119}", username):
        raise ValueError("Use a username with letters, numbers, dots, underscores, @ or hyphens.")
    if args.password_file:
        source = Path(args.password_file)
        if os.name != "nt" and source.stat().st_mode & 0o077:
            raise ValueError("Password file must be readable only by its owner (chmod 600).")
        password = source.read_text().rstrip("\r\n")
    else:
        password = getpass.getpass("New account password (15–128 characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise ValueError("Passwords do not match.")
    encoded = password_hash(password)
    with psycopg2.connect(**get_db_config()) as conn:
        one(conn, "SELECT pg_advisory_xact_lock(1349913)")
        if args.reset:
            user = one(
                conn,
                "UPDATE access_users SET password_hash=%s,enabled=true WHERE username=%s RETURNING id",
                (encoded, username),
            )
            if not user:
                raise ValueError("Account does not exist. Omit --reset to create it.")
            one(conn, "DELETE FROM access_sessions WHERE user_id=%s", (user["id"],))
        else:
            user = one(
                conn,
                """INSERT INTO access_users(username,display_name,password_hash,role)
                VALUES (%s,%s,%s,%s) ON CONFLICT(username) DO NOTHING RETURNING id""",
                (username, args.display_name or username, encoded, args.role),
            )
            if not user:
                raise ValueError("Account exists. Use --reset only to reset its password.")
    print(f"Account {username} {'reset' if args.reset else 'created'}. No provider credentials changed.")
    return 0
