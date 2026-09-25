import re

from app.core.security import derive_pkce_verifier, pkce_code_challenge

# RFC 7636: code_verifier is 43-128 chars from [A-Za-z0-9-._~].
_VALID_VERIFIER_CHARS = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


def test_derive_pkce_verifier_is_deterministic():
    # The whole point: the callback (which only gets `state` back, not anything we'd have to
    # separately remember) must be able to reconstruct the exact same verifier.
    assert derive_pkce_verifier("some-state-token") == derive_pkce_verifier("some-state-token")


def test_derive_pkce_verifier_differs_per_state():
    assert derive_pkce_verifier("state-a") != derive_pkce_verifier("state-b")


def test_derive_pkce_verifier_matches_rfc7636_charset_and_length():
    verifier = derive_pkce_verifier("some-state-token")
    assert _VALID_VERIFIER_CHARS.match(verifier)


def test_pkce_code_challenge_is_deterministic_and_differs_from_verifier():
    verifier = derive_pkce_verifier("some-state-token")
    challenge = pkce_code_challenge(verifier)
    assert challenge == pkce_code_challenge(verifier)
    assert challenge != verifier
