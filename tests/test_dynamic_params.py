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


def test_get_stop_patterns(scraper):
    stop_code = "32752"

    with patch.object(scraper, "session") as mock_session:
        # Mock stop mappings fetch
        mock_mappings_res = MagicMock()
        mock_mappings_res.text = "2752,feed15,15,32752,32752,32752;"

        # Mock stops_patterns fetch
        mock_patterns_res = MagicMock()
        mock_patterns_res.text = "urlHoraireArret('2752','44_1_1','44','Direction Panama','44','');"

        mock_session.get.side_effect = [mock_mappings_res, mock_patterns_res]

        patterns = scraper.get_stop_patterns(stop_code)

        assert len(patterns) == 1
        assert patterns[0]["stop"] == "2752"
        assert patterns[0]["pattern"] == "44_1_1"
        assert patterns[0]["ligne"] == "44"


def test_get_schedule_by_params(scraper):
    test_pattern = {"stop": "2752", "pattern": "44_1_1", "code": "44", "desc": "Direction Panama", "ligne": "44"}
    today = datetime.date(2026, 3, 16)

    with patch.object(scraper, "session") as mock_session:
        # Mock responses for Monday, Saturday, Sunday
        mock_mon_res = MagicMock()
        mock_mon_res.status_code = 200
        mock_mon_res.json.return_value = {
            "data": [
                {
                    "scheduledarrival": 28800,
                    "date": "2026-03-16T00:00:00Z",
                    "stopid": "2752",
                    "id": "44_1_1:01",
                }  # 08:00
            ]
        }

        mock_sat_res = MagicMock()
        mock_sat_res.status_code = 200
        mock_sat_res.json.return_value = {"data": []}

        mock_sun_res = MagicMock()
        mock_sun_res.status_code = 200
        mock_sun_res.json.return_value = {"data": []}

        mock_session.get.side_effect = [mock_mon_res, mock_sat_res, mock_sun_res]

        schedule = scraper.get_schedule_by_params(test_pattern, today)

        assert len(schedule) == 1
        assert schedule[0].time() == datetime.time(8, 0)
