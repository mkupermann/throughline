"""Server settings, resolved from the environment.

Deliberately not pydantic-settings: the configuration surface is four values
and ``throughline.config`` already owns the database half of it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from throughline.config import repo_root

#: Hosts that keep the server on this machine. Anything else exposes the
#: database to the network; team mode does not change the bind safeguard.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

#: Opt-in escape hatch, so binding publicly is a deliberate act rather than
#: a typo in a launch script.
ALLOW_REMOTE_ENV = "THROUGHLINE_ALLOW_REMOTE"


def default_web_dist() -> Path | None:
    """Locate the built frontend, or None if it has not been built.

    Vite writes into ``throughline/web/`` (see web/vite.config.ts) precisely
    so the assets live inside the installed package. The repo-root fallback
    covers an older checkout that still has ``web/dist``.
    """
    packaged = Path(__file__).resolve().parent.parent / "web"
    if (packaged / "index.html").is_file():
        return packaged
    legacy = repo_root() / "web" / "dist"
    return legacy if (legacy / "index.html").is_file() else None


class RemoteBindRefused(RuntimeError):
    """Raised when the server is asked to bind a non-loopback address."""


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    #: 8787 was the original default and is no longer usable here: a launchd
    #: agent on the author's machine claims it and reclaims it the moment
    #: anything releases it. 8788 is taken by the Docker publish mapping
    #: (see docker-compose.yml), so the native server sits on 8790 and the two
    #: can run side by side — which is useful, because they talk to different
    #: databases. Override with THROUGHLINE_PORT or `serve --port`.
    port: int = 8790
    #: Directory holding the built frontend. Absent during backend-only dev,
    #: in which case the API still serves and only the SPA routes 404.
    web_dist: Path | None = None
    #: Connection pool bounds. A single local user does not need many, but
    #: SSE job-progress streams hold a connection for their lifetime.
    pool_min: int = 1
    pool_max: int = 8
    #: Redact secrets in serialized content by default, matching the GUI.
    redact: bool = True
    auth_mode: str = "local"
    public_url: str = ""

    def __post_init__(self):
        if self.auth_mode not in {"local", "team"}:
            raise ValueError("THROUGHLINE_AUTH_MODE must be local or team.")
        if self.auth_mode == "team":
            from .access import validate_origin

            object.__setattr__(self, "public_url", validate_origin(self.public_url))

    @classmethod
    def from_env(cls) -> Settings:
        dist_override = os.environ.get("THROUGHLINE_WEB_DIST")
        if dist_override:
            web_dist: Path | None = Path(dist_override).expanduser().resolve()
        else:
            web_dist = default_web_dist()

        return cls(
            auth_mode=os.environ.get("THROUGHLINE_AUTH_MODE", "local"),
            public_url=os.environ.get("THROUGHLINE_PUBLIC_URL", ""),
            host=os.environ.get("THROUGHLINE_HOST", "127.0.0.1"),
            port=int(os.environ.get("THROUGHLINE_PORT", "8790")),
            web_dist=web_dist,
            pool_min=int(os.environ.get("THROUGHLINE_POOL_MIN", "1")),
            pool_max=int(os.environ.get("THROUGHLINE_POOL_MAX", "8")),
            redact=os.environ.get("THROUGHLINE_REDACT", "1").lower() not in ("0", "false", "no", "off"),
        )


def check_bind_allowed(host: str) -> None:
    """Refuse to bind a non-loopback address unless explicitly permitted.

    Local mode exposes the corpus without authentication. Shared deployments
    require team mode and a trusted TLS proxy. Enforced here in code rather than in documentation,
    because a README cannot stop ``--host 0.0.0.0``.
    """
    if host in LOOPBACK_HOSTS:
        return
    if os.environ.get(ALLOW_REMOTE_ENV, "").lower() in ("1", "true", "yes", "on"):
        return
    raise RemoteBindRefused(
        f"Refusing to bind {host!r}: local mode has no authentication; remote access exposes the shared corpus. "
        f"Bind 127.0.0.1, or set {ALLOW_REMOTE_ENV}=1 for a controlled "
        f"deployment with team authentication and a trusted TLS proxy."
    )
