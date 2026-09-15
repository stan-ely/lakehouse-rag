"""Mint local JWTs for demo personas: `mise run token sales`.

Tokens are signed with the configured secret, so this only works where you already hold it
(local, CI). Production identities come from the organisation's identity provider.
"""

import argparse

from app.auth import mint_token
from app.settings import get_settings

PERSONAS: dict[str, list[str]] = {
    "exec": ["all-staff", "exec"],
    "hr": ["all-staff", "hr"],
    "finance": ["all-staff", "finance"],
    "sales": ["all-staff", "sales"],
    "support": ["all-staff", "support"],
    "ops": ["all-staff", "ops"],
    "engineering": ["all-staff", "engineering"],
    # No groups at all: every query is refused, which demonstrates failing closed.
    "contractor": [],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a local JWT for a demo persona.")
    parser.add_argument("persona", choices=sorted(PERSONAS))
    parser.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    args = parser.parse_args()
    print(mint_token(get_settings(), f"demo-{args.persona}", PERSONAS[args.persona], args.ttl))


if __name__ == "__main__":
    main()
