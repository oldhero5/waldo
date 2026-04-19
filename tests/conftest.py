import uuid

import pytest


@pytest.fixture
def test_clip():
    """Path to a small test video clip."""
    from pathlib import Path

    clip = Path(__file__).parent / "fixtures" / "test_clip.mp4"
    if not clip.exists():
        pytest.skip("test_clip.mp4 not found in fixtures/")
    return clip


@pytest.fixture(autouse=True)
def _bypass_auth_in_tests(request):
    """Default: all API tests see a mock authenticated user so routes don't 401.

    Tests that explicitly exercise the auth layer (e.g. `tests/test_auth.py`)
    can opt out via `@pytest.mark.no_auth_bypass`.
    """
    if request.node.get_closest_marker("no_auth_bypass"):
        yield
        return

    try:
        from app.main import app
        from lib.auth import get_current_user, require_admin
        from lib.db import User
    except Exception:
        # App can't import — let the test fail on its own terms.
        yield
        return

    fake_user = User(
        id=uuid.uuid4(),
        email="test@waldo.local",
        password_hash="",
        display_name="Test User",
    )

    async def _fake_user():
        return fake_user

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[require_admin] = _fake_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_admin, None)


def pytest_configure(config):
    config.addinivalue_line("markers", "no_auth_bypass: don't override auth dependencies for this test")
