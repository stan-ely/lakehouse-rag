import time

import jwt
import pytest
from pydantic import SecretStr

from app.auth import AuthError, Principal, decode_token, mint_token
from app.settings import Settings

SETTINGS = Settings(jwt_secret=SecretStr("unit-test-secret-0123456789abcdef0123"))


def _claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": "ana",
        "groups": ["sales"],
        "iat": now,
        "exp": now + 60,
        "iss": SETTINGS.jwt_issuer,
        "aud": SETTINGS.jwt_audience,
    }
    return {**claims, **overrides}


def _sign(claims: dict[str, object]) -> str:
    return jwt.encode(claims, SETTINGS.jwt_secret.get_secret_value(), algorithm="HS256")


def test_minted_token_round_trips_to_a_principal() -> None:
    token = mint_token(SETTINGS, "ana", ["sales", "all-staff", "sales"])

    assert decode_token(token, SETTINGS) == Principal("ana", frozenset({"sales", "all-staff"}))


def test_empty_group_list_is_a_valid_identity_with_no_access() -> None:
    assert decode_token(mint_token(SETTINGS, "temp", []), SETTINGS).groups == frozenset()


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(mint_token(SETTINGS, "ana", ["sales"], ttl_seconds=-120), id="expired"),
        pytest.param(
            mint_token(
                Settings(jwt_secret=SecretStr("some-other-secret-0123456789abcdef0")), "ana", ["hr"]
            ),
            id="wrong-signature",
        ),
        pytest.param(_sign(_claims(aud="another-service")), id="wrong-audience"),
        pytest.param(_sign(_claims(iss="someone-else")), id="wrong-issuer"),
        pytest.param(jwt.encode(_claims(), key="", algorithm="none"), id="alg-none"),
        pytest.param(_sign({k: v for k, v in _claims().items() if k != "exp"}), id="no-expiry"),
        pytest.param(_sign(_claims(groups="hr")), id="groups-not-a-list"),
        pytest.param(_sign(_claims(groups=["hr", ""])), id="empty-group-name"),
        pytest.param(_sign({k: v for k, v in _claims().items() if k != "groups"}), id="no-groups"),
        pytest.param("not-a-jwt", id="garbage"),
    ],
)
def test_invalid_tokens_are_rejected(token: str) -> None:
    with pytest.raises(AuthError):
        decode_token(token, SETTINGS)
