import pytest


@pytest.fixture
def test_clip():
    """Path to a small test video clip."""
    from pathlib import Path

    clip = Path(__file__).parent / "fixtures" / "test_clip.mp4"
    if not clip.exists():
        pytest.skip("test_clip.mp4 not found in fixtures/")
    return clip
