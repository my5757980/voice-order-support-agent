"""Short-lived session tokens.

The browser never holds a vendor API key and never opens a vendor socket. It gets a
single-use, session-scoped token that authenticates it to *us*, and nothing more
(constitution Security & Privacy; NFR-017).

Single-use is per socket pair: the token is claimed once for audio and once for control,
then it is spent. It expires in 60 seconds regardless.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

TTL_SECONDS = 60
MAX_CLAIMS = 2  # one audio socket, one control socket


@dataclass
class _Issued:
    session_id: str
    customer_id: str
    issued_at: float
    claims: int = 0


@dataclass
class TokenStore:
    _tokens: dict[str, _Issued] = field(default_factory=dict)

    def mint(self, customer_id: str) -> tuple[str, str]:
        """Returns (token, session_id)."""
        self._sweep()
        token = secrets.token_urlsafe(24)
        session_id = "sess_" + secrets.token_hex(8)
        self._tokens[token] = _Issued(
            session_id=session_id, customer_id=customer_id, issued_at=time.monotonic()
        )
        return token, session_id

    def claim(self, token: str) -> tuple[str, str] | None:
        """Consume one claim. Returns (session_id, customer_id), or None if invalid."""
        self._sweep()
        issued = self._tokens.get(token)
        if issued is None:
            return None
        if time.monotonic() - issued.issued_at > TTL_SECONDS:
            del self._tokens[token]
            return None
        issued.claims += 1
        if issued.claims > MAX_CLAIMS:
            # Replay beyond the two expected sockets: burn it rather than allowing an
            # unbounded number of connections on one grant.
            del self._tokens[token]
            return None
        return issued.session_id, issued.customer_id

    def _sweep(self) -> None:
        now = time.monotonic()
        expired = [t for t, i in self._tokens.items() if now - i.issued_at > TTL_SECONDS]
        for token in expired:
            del self._tokens[token]
