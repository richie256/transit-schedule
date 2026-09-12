import datetime
from unittest.mock import MagicMock, patch

import pytest

from transit_schedule.hastus_scraper import HastusScraper


@pytest.fixture
def scraper():
    with (
        patch("transit_schedule.hastus_scraper.HastusScraper._initialize"),
        patch("transit_schedule.hastus_scraper.HastusScraper._load_cache"),
    ):
        scraper = HastusScraper()
        scraper.buildtime = "20260408"
        return scraper


def test_cache_logic(scraper):
    test_pattern = {
        "stop": "2752",
        "pattern": "44_1_1",
        "code": "44",
        "desc": "Direction Terminus Panama",
        "ligne": "44 Direction Terminus Panama",
    }

    # Mock get_stop_patterns to return our test pattern
    scraper.get_stop_patterns = MagicMock(return_value=[test_pattern])

    # Mock _get_times_from_cache to return some times
    with patch.object(scraper, "session") as mock_session, patch.object(scraper, "_get_now") as mock_now:
        # Set "now" to 10:00 AM (not triggered by >= 20:00 rule)
        mock_now.return_value = datetime.datetime(2026, 3, 16, 10, 0, 0)

        # Mock responses for Monday, Saturday, Sunday
        mock_mon_res = MagicMock()
        mock_mon_res.status_code = 200
        mock_mon_res.json.return_value = {
            "data": [
                {"scheduledarrival": 8 * 3600, "date": "2026-03-16T00:00:00Z", "stopid": "2752", "id": "44_1_1:01"}
            ]
        }

        mock_sat_res = MagicMock()
        mock_sat_res.status_code = 200
        mock_sat_res.json.return_value = {
            "data": [
                {"scheduledarrival": 9 * 3600, "date": "2026-03-21T00:00:00Z", "stopid": "2752", "id": "44_1_1:02"}
            ]
        }

        mock_sun_res = MagicMock()
        mock_sun_res.status_code = 200
        mock_sun_res.json.return_value = {
            "data": [
                {"scheduledarrival": 10 * 3600, "date": "2026-03-22T00:00:00Z", "stopid": "2752", "id": "44_1_1:03"}
            ]
        }

        mock_session.get.side_effect = [mock_mon_res, mock_sat_res, mock_sun_res]

        # Monday - Should trigger SCRAPE (and cache Monday, Saturday, Sunday)
        mon = datetime.date(2026, 3, 16)
        mon_schedule = scraper.get_schedule_by_params(test_pattern, mon)
        assert len(mon_schedule) == 1
        assert mon_schedule[0].time() == datetime.time(8, 0)
        assert mock_session.get.call_count == 3

        # Tuesday - Should use CACHE
        tue = datetime.date(2026, 3, 17)
        tue_schedule = scraper.get_schedule_by_params(test_pattern, tue)
        assert len(tue_schedule) == 1
        assert tue_schedule[0].time() == datetime.time(8, 0)
        # Call count should still be 3
        assert mock_session.get.call_count == 3

        # Saturday - Should use CACHE
        sat = datetime.date(2026, 3, 21)
        sat_schedule = scraper.get_schedule_by_params(test_pattern, sat)
        assert len(sat_schedule) == 1
        assert sat_schedule[0].time() == datetime.time(9, 0)
        assert mock_session.get.call_count == 3

        # Sunday - Should use CACHE
        sun = datetime.date(2026, 3, 22)
        sun_schedule = scraper.get_schedule_by_params(test_pattern, sun)
        assert len(sun_schedule) == 1
        assert sun_schedule[0].time() == datetime.time(10, 0)
        assert mock_session.get.call_count == 3
