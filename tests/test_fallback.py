import datetime
from unittest.mock import patch

import pytest

from transit_schedule.hastus_scraper import HastusScraper


@pytest.fixture
def scraper():
    with (
        patch("transit_schedule.hastus_scraper.HastusScraper._initialize"),
        patch("transit_schedule.hastus_scraper.HastusScraper._load_cache"),
    ):
        scraper = HastusScraper()
        return scraper


def test_get_schedule_fallback(scraper):
    stop_id = 2752
    today = datetime.date(2026, 3, 16)

    with (
        patch.object(scraper, "get_stop_code_from_id") as mock_get_code,
        patch.object(scraper, "get_stop_patterns") as mock_get_patterns,
        patch.object(scraper, "get_schedule_by_params") as mock_get_schedule,
    ):
        mock_get_code.return_value = "32752"
        mock_get_patterns.return_value = [
            {"ligne": "44 Direction Terminus Panama", "stop": "2752", "pattern": "44_1_1"},
            {"ligne": "Other line", "stop": "2752", "pattern": "OTHER"},
        ]
        mock_get_schedule.return_value = [datetime.datetime(2026, 3, 16, 8, 0)]

        # We need to mock TARGET_DIRECTION from transit_schedule.const as well
        with patch("transit_schedule.hastus_scraper.TARGET_DIRECTION", "Terminus Panama"):
            arrivals = scraper.get_schedule(stop_id, today)

            assert len(arrivals) == 1
            assert arrivals[0]["route_id"] == "44"
            assert "Terminus Panama" in arrivals[0]["trip_headsign"]

            # Check if get_schedule_by_params was called only once for the target direction
            assert mock_get_schedule.call_count == 1
