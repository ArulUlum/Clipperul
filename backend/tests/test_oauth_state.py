import uuid

from app.core.security import create_oauth_state, verify_oauth_state


def test_state_round_trips_to_the_same_user_id():
    user_id = uuid.uuid4()
    state = create_oauth_state(user_id, "youtube")
    assert verify_oauth_state(state, "youtube") == user_id


def test_state_rejects_wrong_provider():
    # A state minted for one platform's connect flow must not be usable against a different
    # platform's callback.
    user_id = uuid.uuid4()
    state = create_oauth_state(user_id, "youtube")
    assert verify_oauth_state(state, "tiktok") is None


def test_state_rejects_garbage_input():
    assert verify_oauth_state("not-a-real-token", "youtube") is None


def test_state_rejects_a_plain_access_token():
    # An access token minted by create_access_token must not double as OAuth state — the
    # 'purpose' claim is what stops that cross-use.
    from app.core.security import create_access_token

    token = create_access_token(uuid.uuid4())
    assert verify_oauth_state(token, "youtube") is None
