import datetime
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

from transit_schedule.http_server import create_app


@pytest.fixture
def mock_transit_data():
    return MagicMock()


@pytest.fixture
def client(mock_transit_data):
    app = create_app(transit_data=mock_transit_data)
    app.config["TESTING"] = True
    return app.test_client()


def test_get_next_stop_uninitialized(mocker):
    """Test get_next_stop when transit_data is None."""
    from transit_schedule.http_server import create_app

    mocker.patch("transit_schedule.http_server.data_parser.ParseTransitData", side_effect=Exception("Failed"))
    app = create_app()
    with app.test_client() as client:
        response = client.get("/transit-schedule/nextstop/32752")
        assert response.status_code == 500
        assert response.json == {"error": "Transit data not initialized"}


def test_create_app_error(mocker):
    """Test create_app when an error occurs during initialization."""
    from transit_schedule.http_server import create_app

    mocker.patch(
        "transit_schedule.http_server.data_parser.ParseTransitData", side_effect=Exception("Initialization failed")
    )
    mock_logger = mocker.patch("transit_schedule.http_server._LOGGER")

    app = create_app()
    assert app is not None  # Flask app still created, but error logged
    mock_logger.exception.assert_called()


def test_start_http_server(mocker):
    """Test start_http_server."""
    from transit_schedule.http_server import start_http_server

    mock_app = MagicMock()
    mocker.patch("transit_schedule.http_server.create_app", return_value=mock_app)

    start_http_server()
    mock_app.run.assert_called_once_with(host="0.0.0.0", port=80)


@freeze_time("2026-03-16 08:00:00")
def test_get_next_stop_success(client, mock_transit_data):
    stop_code = 32752
    mock_transit_data.get_stop_id.return_value = "stop_id_123"

    # We are at 08:00:00
    arrival_dt = datetime.datetime(2026, 3, 16, 8, 15, 0)
    mock_next_stop = MagicMock()
    mock_next_stop.arrival_datetime = arrival_dt
    mock_next_stop.arrival_time = "08:15:00"
    mock_next_stop.route_id = 44
    mock_next_stop.trip_headsign = "Terminus Panama"

    mock_transit_data.get_next_stop.return_value = mock_next_stop

    response = client.get(f"/transit-schedule/nextstop/{stop_code}")
    assert response.status_code == 200
    data = response.get_json()
    assert data["nextstop_nbrmins"] == 15
    assert data["nextstop_nbrsecs"] == 0
    assert data["route_id"] == "44"

    assert data["arrival_time"] == "08:15:00"


def test_get_next_stop_invalid_code(client):
    response = client.get("/transit-schedule/nextstop/0")
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_get_next_stop_not_found(client, mock_transit_data):
    mock_transit_data.get_stop_id.return_value = None
    response = client.get("/transit-schedule/nextstop/99999")
    assert response.status_code == 404
    assert response.get_json() == {"error": "Stop code not found"}


def test_get_next_stop_no_more_buses(client, mock_transit_data):
    mock_transit_data.get_stop_id.return_value = "stop_id_123"
    mock_transit_data.get_next_stop.return_value = None
    response = client.get("/transit-schedule/nextstop/32752")
    assert response.status_code == 200
    assert response.get_json() == {"error": "No more buses for today"}
