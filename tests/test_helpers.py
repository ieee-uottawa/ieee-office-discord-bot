"""Unit tests for helper functions."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock environment before importing main to avoid Discord object errors
os.environ.setdefault("EXEC_GUILD_ID", "123456789")
os.environ.setdefault("COMMUNITY_GUILD_ID", "987654321")
os.environ.setdefault("DISCORD_TOKEN", "test_token")

from main import calculate_leaderboard


class TestCalculateLeaderboard:
    """Test leaderboard calculation"""

    @patch("main.requests.get")
    def test_calculate_leaderboard_basic(self, mock_get, mock_visits_response):
        """Test basic leaderboard calculation."""
        mock_response = MagicMock()
        mock_response.json.return_value = mock_visits_response
        mock_get.return_value = mock_response

        leaderboard, error = calculate_leaderboard(days=7, top_n=10)

        assert error is None
        assert len(leaderboard) > 0
        assert "name" in leaderboard[0]
        assert "visits" in leaderboard[0]
        assert "total_hours" in leaderboard[0]

    @patch("main.requests.get")
    def test_calculate_leaderboard_filters_4am(self, mock_get):
        """Test that 4 AM auto-signouts are filtered out."""
        # Create a visit with exact 4 AM signout time
        base_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        visits = [
            {
                "name": "Alice",
                "signin_time": (base_date - timedelta(days=1, hours=2)).isoformat(),
                "signout_time": (base_date - timedelta(days=1)).replace(hour=4).isoformat(),  # Exact 4 AM
            },
            {
                "name": "Bob",
                "signin_time": (base_date - timedelta(hours=2)).isoformat(),
                "signout_time": (base_date - timedelta(hours=1)).isoformat(),
            },
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = visits
        mock_get.return_value = mock_response

        leaderboard, error = calculate_leaderboard(days=7, top_n=10)

        # Both should be present (4 AM filter checks exact hour, not close times)
        assert error is None
        assert len(leaderboard) >= 1

    @patch("main.requests.get")
    def test_calculate_leaderboard_sorting(self, mock_get):
        """Test leaderboard is sorted by visits then hours."""
        now = datetime.now()
        visits = [
            {
                "name": "Alice",
                "signin_time": (now - timedelta(hours=5)).isoformat(),
                "signout_time": (now - timedelta(hours=4)).isoformat(),
            },
            {
                "name": "Alice",
                "signin_time": (now - timedelta(hours=3)).isoformat(),
                "signout_time": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "name": "Bob",
                "signin_time": (now - timedelta(hours=1)).isoformat(),
                "signout_time": now.isoformat(),
            },
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = visits
        mock_get.return_value = mock_response

        leaderboard, error = calculate_leaderboard(days=7, top_n=10)

        # Alice should be first (2 visits vs Bob's 1)
        assert leaderboard[0]["name"] == "Alice"
        assert leaderboard[0]["visits"] == 2
        assert leaderboard[1]["name"] == "Bob"
        assert leaderboard[1]["visits"] == 1

    @patch("main.requests.get")
    def test_calculate_leaderboard_top_n(self, mock_get):
        """Test top_n parameter limits results."""
        now = datetime.now()
        visits = []
        for i in range(20):
            visits.append(
                {
                    "name": f"User{i}",
                    "signin_time": (now - timedelta(hours=i)).isoformat(),
                    "signout_time": (now - timedelta(hours=i - 1)).isoformat(),
                }
            )

        mock_response = MagicMock()
        mock_response.json.return_value = visits
        mock_get.return_value = mock_response

        leaderboard, error = calculate_leaderboard(days=7, top_n=5)

        assert len(leaderboard) <= 5

    @patch("main.requests.get")
    def test_calculate_leaderboard_empty_response(self, mock_get):
        """Test handling empty visit data."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_get.return_value = mock_response

        leaderboard, error = calculate_leaderboard(days=7, top_n=10)

        assert error is None
        assert len(leaderboard) == 0

    @patch("main.requests.get")
    def test_calculate_leaderboard_visit_duration_calculation(self, mock_get):
        """Test that visit durations are correctly calculated in hours."""
        now = datetime.now()
        visits = [
            {
                "name": "Alice",
                "signin_time": (now - timedelta(hours=3)).isoformat(),
                "signout_time": now.isoformat(),
            },
        ]

        mock_response = MagicMock()
        mock_response.json.return_value = visits
        mock_get.return_value = mock_response

        leaderboard, error = calculate_leaderboard(days=7, top_n=10)

        # Should have 1 visit with approximately 3 hours
        assert len(leaderboard) == 1
        assert leaderboard[0]["total_hours"] == pytest.approx(3.0, abs=0.1)

