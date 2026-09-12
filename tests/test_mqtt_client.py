import datetime
import json
from unittest.mock import MagicMock, patch

from transit_schedule.const import TRANSLATIONS
from transit_schedule.mqtt_client import (
    on_message_callback,
    publish_hass_discovery_config,
    publish_schedule,
    start_mqtt_client,
)


@patch("transit_schedule.mqtt_client.config")
def test_publish_hass_discovery_config(mock_cfg):
    mock_cfg.hass_discovery_prefix = "homeassistant"
    mock_cfg.transit = "RTL"
    mock_cfg.get_mqtt_state_topic.return_value = "state_topic"
    mock_client = MagicMock()
    stop_config = {"stop_code": "12345"}
    publish_hass_discovery_config(mock_client, stop_config, "homeassistant")
    mock_client.publish.assert_called()


@patch("transit_schedule.mqtt_client.config")
def test_publish_schedule_success(mock_cfg_inst):
    mock_cfg_inst.stop_code = "12345"
    mock_cfg_inst.get_mqtt_state_topic.return_value = "topic"
    mock_cfg_inst.language = "fr"

    mock_client = MagicMock()
    mock_transit_data = MagicMock()
    mock_next_stop = MagicMock()
    mock_next_stop.arrival_datetime = datetime.datetime.now() + datetime.timedelta(minutes=5)
    mock_next_stop.arrival_time = "12:00:00"
    mock_next_stop.route_id = "44"
    mock_next_stop.trip_headsign = "Panama"
    mock_next_stop.retrieve_method = "live scraper"

    mock_transit_data.get_next_stop.return_value = mock_next_stop

    stop_config = {"stop_code": "12345"}
    publish_schedule(mock_client, mock_transit_data, "stop_id", stop_config)

    args, kwargs = mock_client.publish.call_args
    assert kwargs.get("retain") is True
    payload = json.loads(args[1])
    assert payload["retrieve_method"] == TRANSLATIONS["fr"]["live_scraper"]


@patch("transit_schedule.mqtt_client.config")
def test_publish_schedule_method_fallback(mock_cfg_inst):
    mock_cfg_inst.stop_code = "12345"
    mock_cfg_inst.get_mqtt_state_topic.return_value = "topic"
    mock_cfg_inst.language = "fr"
    mock_client = MagicMock()
    mock_transit_data = MagicMock()
    mock_next_stop = MagicMock()
    mock_next_stop.arrival_datetime = datetime.datetime.now() + datetime.timedelta(minutes=5)
    mock_next_stop.retrieve_method = "unknown"
    mock_transit_data.get_next_stop.return_value = mock_next_stop
    stop_config = {"stop_code": "12345"}
    publish_schedule(mock_client, mock_transit_data, "stop_id", stop_config)
    args, _ = mock_client.publish.call_args
    payload = json.loads(args[1])
    assert payload["retrieve_method"] == "unknown"


def test_start_mqtt_client_duplicate(mocker):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = True
    mock_logger = mocker.patch("transit_schedule.mqtt_client._LOGGER")

    start_mqtt_client()
    mock_logger.warning.assert_called_with(
        "MQTT client loop is already running in this process. Skipping duplicate start."
    )
    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False


@patch("transit_schedule.mqtt_client.config")
def test_start_mqtt_client_init_error(mock_cfg_inst, mocker):
    mock_cfg_inst.stops = [{"stop_code": "12345"}]
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    mocker.patch("transit_schedule.mqtt_client.ParseTransitData", side_effect=Exception("Init error"))
    mock_logger = mocker.patch("transit_schedule.mqtt_client._LOGGER")
    mocker.patch("transit_schedule.mqtt_client.time.sleep")

    start_mqtt_client()
    assert mock_logger.error.called


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
def test_start_mqtt_client_loop_error(mock_parser, mock_mqtt_client, mock_cfg_inst, mocker):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    mock_cfg_inst.stops = [{"stop_code": "12345"}]
    mock_cfg_inst.get_mqtt_state_topic.return_value = "topic"
    mock_cfg_inst.hass_discovery_prefix = "homeassistant"
    mock_cfg_inst.hass_discovery_enabled = True

    mocker.patch(
        "transit_schedule.mqtt_client.publish_schedule", side_effect=[Exception("Loop error"), KeyboardInterrupt]
    )
    mocker.patch("transit_schedule.mqtt_client.time.sleep")
    mock_logger = mocker.patch("transit_schedule.mqtt_client._LOGGER")

    try:
        start_mqtt_client()
    except KeyboardInterrupt:
        pass
    assert any("Error in MQTT main loop" in str(call) for call in mock_logger.error.call_args_list)


@patch("transit_schedule.mqtt_client.config")
def test_on_message_callback(mock_cfg):
    mock_cfg.mqtt_refresh_topic = "refresh"
    mock_cfg.mqtt_hass_status_topic = "status"
    mock_cfg.hass_discovery_enabled = True
    mock_cfg.stops = [{"stop_code": "12345"}]
    mock_cfg.hass_discovery_prefix = "homeassistant"
    mock_cfg.get_mqtt_state_topic.return_value = "state_topic"
    mock_cfg.transit = "RTL"

    mock_client = MagicMock()
    mock_event = MagicMock()
    t = TRANSLATIONS["fr"]

    # Test refresh topic
    mock_msg = MagicMock()
    mock_msg.topic = "refresh"
    on_message_callback(mock_client, None, mock_msg, mock_event, t)
    mock_event.set.assert_called_once()
    mock_event.reset_mock()

    # Test HASS status topic
    mock_msg.topic = "status"
    on_message_callback(mock_client, None, mock_msg, mock_event, t)
    mock_event.set.assert_called_once()

    # Test unknown topic
    mock_event.reset_mock()
    mock_msg.topic = "unknown"
    on_message_callback(mock_client, None, mock_msg, mock_event, t)
    assert not mock_event.set.called


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
def test_start_mqtt_client_stop_not_found(mock_parser, mock_mqtt_client, mock_cfg_inst, mocker):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    mock_cfg_inst.stops = [{"stop_code": "12345"}]
    mock_parser.return_value.get_stop_id.return_value = None
    mock_logger = mocker.patch("transit_schedule.mqtt_client._LOGGER")

    start_mqtt_client()
    mock_logger.error.assert_any_call("Stop code 12345 not found.")


@patch("transit_schedule.mqtt_client.config")
def test_publish_schedule_no_bus(mock_cfg_inst):
    mock_cfg_inst.stop_code = "12345"
    mock_cfg_inst.get_mqtt_state_topic.return_value = "topic"
    mock_cfg_inst.language = "fr"
    mock_client = MagicMock()
    mock_transit_data = MagicMock()
    mock_transit_data.get_next_stop.return_value = None
    stop_config = {"stop_code": "12345"}
    publish_schedule(mock_client, mock_transit_data, "stop_id", stop_config)
    mock_client.publish.assert_called_once()
    args, kwargs = mock_client.publish.call_args
    assert args[0] == "topic"
    payload = json.loads(args[1])
    assert payload["nextstop_nbrmins"] is None
    assert payload["arrival_time"] is None
    assert payload["trip_headsign"] == TRANSLATIONS["fr"]["no_more_buses"]
    assert kwargs.get("retain") is True


@patch("transit_schedule.mqtt_client.config")
def test_start_mqtt_client_missing_stop_code(mock_cfg_inst):
    mock_cfg_inst.stops = []
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    with patch("transit_schedule.mqtt_client._LOGGER") as mock_logger:
        start_mqtt_client()
        mock_logger.error.assert_called_with(
            "No stops configured. STOP_CODE or STOPS_CONFIG environment variable is required."
        )


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
@patch("transit_schedule.mqtt_client.threading.Event.wait", side_effect=KeyboardInterrupt)
@patch("transit_schedule.mqtt_client.time.sleep", side_effect=KeyboardInterrupt)
def test_start_mqtt_client_loop(mock_sleep, mock_event_wait, mock_rtl_parser, mock_mqtt_client, mock_cfg_inst):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    mock_cfg_inst.stops = [{"stop_code": "12345"}]
    mock_cfg_inst.mqtt_host = "localhost"
    mock_cfg_inst.mqtt_port = 1883
    mock_cfg_inst.mqtt_username = "user"
    mock_cfg_inst.mqtt_password = "pass"
    mock_cfg_inst.mqtt_use_tls = False
    mock_cfg_inst.hass_discovery_enabled = True
    mock_cfg_inst.hass_discovery_prefix = "homeassistant"
    mock_cfg_inst.mqtt_refresh_topic = "refresh"
    mock_cfg_inst.mqtt_hass_status_topic = "status"
    mock_cfg_inst.get_mqtt_state_topic.return_value = "state"

    try:
        start_mqtt_client()
    except KeyboardInterrupt:
        pass

    mock_mqtt_client.return_value.connect.assert_called_with("localhost", 1883)
    mock_mqtt_client.return_value.loop_start.assert_called()
