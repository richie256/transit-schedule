import datetime
import json
from unittest.mock import MagicMock

import pytest
import requests

from transit_schedule.hastus_scraper import HastusScraper


@pytest.fixture
def scraper(mocker):
    mocker.patch.object(HastusScraper, "_initialize", return_value=None)
    mocker.patch.object(HastusScraper, "fetch_stop_mappings", return_value=None)
    mocker.patch.object(HastusScraper, "_load_cache", return_value=None)
    s = HastusScraper()
    s.stop_mappings = {"32752": ["15:2752"]}
    s.schedule_cache = {}
    return s


def test_initialize_success(mocker):
    mocker.patch.object(HastusScraper, "_load_cache")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"buildtime": 123456789}
    mocker.patch("requests.Session.get", return_value=mock_res)
    s = HastusScraper()
    assert s.buildtime == 123456789


def test_initialize_json_error(mocker):
    mocker.patch.object(HastusScraper, "_load_cache")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    mock_res = MagicMock()
    mock_res.json.side_effect = ValueError("JSON Error")
    mocker.patch("requests.Session.get", return_value=mock_res)
    s = HastusScraper()
    assert s.buildtime is None


def test_fetch_stop_mappings_success(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "_load_cache")
    mock_res = MagicMock()
    mock_res.text = "15:456,p1,p2,p3,p4,123;"
    mocker.patch("requests.Session.get", return_value=mock_res)
    s = HastusScraper()
    s.fetch_stop_mappings()
    assert s.stop_mappings == {"123": ["15:456"]}


def test_get_stop_patterns_success(scraper, mocker):
    mock_res = MagicMock()
    mock_res.text = "urlHoraireArret('2752','P1','C1','D1',' 44 Direction Panama');"
    mocker.patch.object(scraper.session, "get", return_value=mock_res)
    patterns = scraper.get_stop_patterns("32752")
    assert len(patterns) == 1
    assert patterns[0]["pattern"] == "P1"


def test_load_cache_success(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    cache_data = {
        "version": "2",
        "data": {"2752|P1|2026-03-16": {"semaine": ["08:00:00"], "samedi": [], "dimanche": []}},
    }
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data=json.dumps(cache_data)))
    s = HastusScraper()
    key = ("2752", "P1", datetime.date(2026, 3, 16))
    assert key in s.schedule_cache


def test_parse_json_weekly_schedule(scraper):
    json_data = {"data": [{"scheduledarrival": 36000, "stopid": "S1", "id": "P1:01", "date": "2026-04-22"}]}
    data = scraper._parse_json_weekly_schedule(json_data, "S1", "P1", datetime.date(2026, 4, 22))
    assert datetime.time(10, 0) in data["semaine"]


def test_parse_html_weekly_schedule(scraper):
    html = """
    <table><tr><td><b>Semaine</b></td></tr><tr><td>08:00</td></tr></table>
    """
    data = scraper._parse_html_weekly_schedule(html)
    assert datetime.time(8, 0) in data["semaine"]


def test_get_schedule(scraper, mocker):
    mocker.patch.object(scraper, "get_stop_code_from_id", return_value="32752")
    mocker.patch.object(
        scraper, "get_stop_patterns", return_value=[{"stop": "2752", "pattern": "P1", "ligne": " 44 Direction Panama"}]
    )
    arrival_dt = datetime.datetime.combine(datetime.date(2026, 4, 22), datetime.time(10, 0))
    mocker.patch.object(scraper, "get_schedule_by_params", return_value=[arrival_dt])
    mocker.patch("transit_schedule.hastus_scraper.TARGET_DIRECTION", "Panama")
    mocker.patch("transit_schedule.hastus_scraper.TARGET_ROUTE", "44")
    arrivals = scraper.get_schedule(2752, datetime.date(2026, 4, 22))
    assert len(arrivals) == 1


def test_initialize_network_error(mocker):
    mocker.patch.object(HastusScraper, "_load_cache")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    mocker.patch("requests.Session.get", side_effect=requests.exceptions.RequestException("Error"))
    s = HastusScraper()
    assert s.buildtime is None


def test_fetch_stop_mappings_error(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "_load_cache")
    mocker.patch("requests.Session.get", side_effect=Exception("Error"))
    s = HastusScraper()
    s.fetch_stop_mappings()
    assert s.stop_mappings == {}


def test_get_stop_patterns_error(scraper, mocker):
    mocker.patch.object(scraper.session, "get", side_effect=Exception("Failed"))
    patterns = scraper.get_stop_patterns("32752")
    assert patterns == []


def test_load_cache_error(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", side_effect=Exception("Error"))
    s = HastusScraper()
    assert s.schedule_cache == {}


def test_fetch_and_cache_success(scraper, mocker):
    mock_res = MagicMock()
    mock_res.status_code = 200
    # Return valid JSON response with one stop time
    mock_res.json.return_value = {
        "data": [
            {"scheduledarrival": 36000, "stopid": "S1", "id": "P1:01", "id_trip": "TRIP_SE_1", "date": "2026-04-20"}
        ]
    }
    mock_get = mocker.patch.object(scraper.session, "get", return_value=mock_res)

    date_val = datetime.date(2026, 4, 22)
    cache_key = ("S1", "P1", datetime.date(2026, 4, 20))
    scraper._fetch_and_cache({"stop": "S1", "pattern": "P1", "ligne": "L1"}, date_val, cache_key)

    # It should have called get 3 times (Monday, Saturday, Sunday)
    assert mock_get.call_count == 3
    # Check that cache has entries
    assert cache_key in scraper.schedule_cache
    # Because of our mock_res returning TRIP_SE_1, and is_day_specific=True overriding:
    # Monday (semaine) -> semaine list gets [10:00:00]
    # Saturday (samedi) -> samedi list gets [10:00:00]
    # Sunday (dimanche) -> dimanche list gets [10:00:00]
    assert datetime.time(10, 0) in scraper.schedule_cache[cache_key]["semaine"]
    assert datetime.time(10, 0) in scraper.schedule_cache[cache_key]["samedi"]
    assert datetime.time(10, 0) in scraper.schedule_cache[cache_key]["dimanche"]


def test_fetch_and_cache_empty_response(scraper, mocker):
    mock_res = MagicMock()
    mock_res.status_code = 200
    mock_res.json.return_value = {"data": []}
    mocker.patch.object(scraper.session, "get", return_value=mock_res)

    date_val = datetime.date(2026, 4, 22)
    cache_key = ("S1", "P1", datetime.date(2026, 4, 20))
    scraper._fetch_and_cache({"stop": "S1", "pattern": "P1", "ligne": "L1"}, date_val, cache_key)
    assert scraper.schedule_cache[cache_key] == {"semaine": [], "samedi": [], "dimanche": []}


def test_get_stop_code_from_id_not_found(scraper):
    scraper.stop_mappings = {"32752": ["15:2752"]}
    assert scraper.get_stop_code_from_id(9999) is None


def test_hastus_scraper_init_force_refresh(mocker):
    mocker.patch("transit_schedule.hastus_scraper.config.force_cache_refresh", True)
    mocker.patch("os.path.exists", return_value=True)
    mock_remove = mocker.patch("os.remove")
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "_load_cache")
    HastusScraper()
    mock_remove.assert_called_with(HastusScraper.CACHE_FILE)


def test_get_stop_patterns_fallback(scraper, mocker):
    scraper.stop_mappings = {}
    mock_res = MagicMock()
    mock_res.text = "urlHoraireArret('2752','P1','C1','D1',' 44 Direction Panama');"
    mocker.patch.object(scraper.session, "get", return_value=mock_res)
    patterns = scraper.get_stop_patterns("32752", stop_id=2752)
    assert len(patterns) == 1


def test_get_stop_patterns_guess_feed(scraper, mocker):
    scraper.stop_mappings = {}
    mock_res = MagicMock()
    mock_res.text = "urlHoraireArret('2752','P1','C1','D1',' 44 Direction Panama');"
    mocker.patch.object(scraper.session, "get", return_value=mock_res)
    patterns = scraper.get_stop_patterns("2752")
    assert len(patterns) == 1


def test_get_stop_patterns_no_patterns_found(scraper, mocker):
    mock_res = MagicMock()
    mock_res.text = "no patterns here"
    mocker.patch.object(scraper.session, "get", return_value=mock_res)
    assert scraper.get_stop_patterns("32752") == []


def test_load_cache_invalid_key(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    cache_data = {"version": "2", "data": {"invalidkey": {"semaine": []}}}
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data=json.dumps(cache_data)))
    s = HastusScraper()
    assert s.schedule_cache == {}


def test_load_cache_old_version(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    cache_data = {"version": "1", "data": {}}
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data=json.dumps(cache_data)))
    s = HastusScraper()
    assert s.schedule_cache == {}


def test_load_cache_corrupted_json(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    mocker.patch("os.path.exists", return_value=True)
    mocker.patch("builtins.open", mocker.mock_open(read_data="corrupted json"))
    s = HastusScraper()
    assert s.schedule_cache == {}


def test_parse_json_no_entries_warning(scraper, mocker):
    mock_logger = mocker.patch("transit_schedule.hastus_scraper._LOGGER")
    scraper._parse_json_weekly_schedule({"data": []}, "S1", "P1", datetime.date.today())
    mock_logger.warning.assert_called()


def test_get_schedule_stm(scraper, mocker):
    import transit_schedule.hastus_scraper

    mocker.patch.object(transit_schedule.hastus_scraper, "TRANSIT", "STM")
    assert scraper.get_schedule(123, datetime.date.today()) == []


def test_initialize_unexpected_error(mocker):
    mocker.patch.object(HastusScraper, "_load_cache")
    mocker.patch.object(HastusScraper, "fetch_stop_mappings")
    mocker.patch("requests.Session.get", side_effect=Exception("Unexpected"))
    s = HastusScraper()
    assert s.buildtime is None


def test_fetch_stop_mappings_network_error(mocker):
    mocker.patch.object(HastusScraper, "_initialize")
    mocker.patch.object(HastusScraper, "_load_cache")
    mocker.patch("requests.Session.get", side_effect=requests.exceptions.RequestException("Error"))
    s = HastusScraper()
    s.fetch_stop_mappings()
    assert s.stop_mappings == {}


def test_get_stop_patterns_network_error(scraper, mocker):
    mocker.patch.object(scraper.session, "get", side_effect=requests.exceptions.RequestException("Conn error"))
    assert scraper.get_stop_patterns("32752") == []


def test_get_stop_patterns_unexpected_error(scraper, mocker):
    mocker.patch.object(scraper.session, "get", side_effect=Exception("Unexpected"))
    assert scraper.get_stop_patterns("32752") == []


def test_fetch_and_cache_bad_response(scraper, mocker):
    mock_bad_res = MagicMock()
    mock_bad_res.status_code = 200
    mock_bad_res.json.side_effect = ValueError("not valid json")
    mocker.patch.object(scraper.session, "get", return_value=mock_bad_res)

    date_val = datetime.date.today()
    cache_key = ("S1", "P1", date_val)
    # This should log warning and not raise exception
    scraper._fetch_and_cache({"stop": "S1", "pattern": "P1", "ligne": "L1"}, date_val, cache_key)
    assert scraper.schedule_cache[cache_key] == {"semaine": [], "samedi": [], "dimanche": []}


def test_parse_json_weekly_schedule_trip_markers(scraper):
    # Test _SE_ marker
    json_data = {"data": [{"scheduledarrival": 36000, "stopid": "S1", "id": "P1:01", "id_trip": "TRIP_SE_1"}]}
    data = scraper._parse_json_weekly_schedule(json_data, "S1", "P1", datetime.date(2026, 4, 22))
    assert datetime.time(10, 0) in data["semaine"]
    # Test _SA_ marker
    json_data = {"data": [{"scheduledarrival": 36000, "stopid": "S1", "id": "P1:01", "id_trip": "TRIP_SA_1"}]}
    data = scraper._parse_json_weekly_schedule(json_data, "S1", "P1", datetime.date(2026, 4, 22))
    assert datetime.time(10, 0) in data["samedi"]
    # Test _DI_ marker
    json_data = {"data": [{"scheduledarrival": 36000, "stopid": "S1", "id": "P1:01", "id_trip": "TRIP_DI_1"}]}
    data = scraper._parse_json_weekly_schedule(json_data, "S1", "P1", datetime.date(2026, 4, 22))
    assert datetime.time(10, 0) in data["dimanche"]


def test_parse_html_weekly_schedule_all_categories(scraper):
    html = """
    <table><tr><td><b>Semaine</b></td></tr><tr><td>08:00</td></tr><tr><td>25:00</td></tr></table>
    <table><tr><td><b>Samedi</b></td></tr><tr><td>09:00</td></tr><tr><td>25:00</td></tr></table>
    <table><tr><td><b>Dimanche</b></td></tr><tr><td>10:00</td></tr><tr><td>25:00</td></tr></table>
    """
    data = scraper._parse_html_weekly_schedule(html)
    assert datetime.time(8, 0) in data["semaine"]
    assert datetime.time(1, 0) in data["samedi"]
    assert datetime.time(1, 0) in data["dimanche"]
