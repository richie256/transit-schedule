from unittest.mock import patch

from transit_schedule.app import main


@patch("transit_schedule.app.start_http_server")
@patch("transit_schedule.app.os.environ.get")
def test_main_http(mock_get, mock_start_http):
    mock_get.return_value = "http"
    main()
    mock_start_http.assert_called_once()


@patch("transit_schedule.app.start_mqtt_client")
@patch("transit_schedule.app.os.environ.get")
def test_main_mqtt(mock_get, mock_start_mqtt):
    mock_get.return_value = "mqtt"
    main()
    mock_start_mqtt.assert_called_once()


@patch("transit_schedule.app._LOGGER")
@patch("transit_schedule.app.os.environ.get")
def test_main_invalid(mock_get, mock_logger):
    mock_get.return_value = "invalid"
    main()
    mock_logger.error.assert_called_with("Invalid mode: invalid")
