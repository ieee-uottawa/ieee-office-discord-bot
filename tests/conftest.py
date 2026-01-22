"""Shared test fixtures and configuration."""
import pytest
from datetime import datetime, timedelta


@pytest.fixture
def mock_visits_response():
    """Sample visits from backend API for leaderboard testing."""
    now = datetime.now()
    return [
        {
            "name": "Alice",
            "signin_time": (now - timedelta(hours=2)).isoformat(),
            "signout_time": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "name": "Alice",
            "signin_time": (now - timedelta(days=1, hours=2)).isoformat(),
            "signout_time": (now - timedelta(days=1, hours=1)).isoformat(),
        },
        # 4 AM auto-signout (should be filtered)
        {
            "name": "Bob",
            "signin_time": (now - timedelta(days=1, hours=6)).isoformat(),
            "signout_time": (now - timedelta(days=1, hours=4)).isoformat(),
        },
    ]

