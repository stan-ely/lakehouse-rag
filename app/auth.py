"""Bearer-token authentication: a verified JWT is the only source of a caller's groups.

Groups never come from request bodies or headers the client controls directly, and every
failure mode (missing token, bad signature, expired, wrong audience, malformed groups) raises,
so nothing downstream can run with an unauthenticated or guessed identity.
"""

import time
from dataclasses import dataclass

import jwt

from app.settings import Settings

ALGORITHM = "HS256"


class AuthError(Exception):
    """The request carries no valid identity."""


@dataclass(frozen=True)
class Principal:
    subject: str
    groups: frozenset[str]


def decode_token(token: str, settings: Settings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            leeway=settings.jwt_leeway_seconds,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    groups = claims.get("groups")
    if not isinstance(groups, list) or not all(isinstance(g, str) and g for g in groups):
        raise AuthError("token groups claim must be a list of non-empty strings")
    return Principal(subject=str(claims["sub"]), groups=frozenset(groups))


def mint_token(settings: Settings, subject: str, groups: list[str], ttl_seconds: int = 3600) -> str:
    """Issues a token for local personas, tests and the demo UI. Not exposed over HTTP."""
    now = int(time.time())
    claims = {
        "sub": subject,
        "groups": sorted(set(groups)),
        "iat": now,
        "exp": now + ttl_seconds,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
