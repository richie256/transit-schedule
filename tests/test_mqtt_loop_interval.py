import datetime
from unittest.mock import MagicMock, patch

from transit_schedule.mqtt_client import start_mqtt_client


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
@patch("transit_schedule.mqtt_client.threading.Event.wait")
def test_mqtt_loop_interval_bus_upcoming(mock_event_wait, mock_parser, mock_mqtt_client, mock_cfg):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    # Mock config
    mock_cfg.stops = [{"stop_code": "12345"}]
    mock_cfg.mqtt_host = "localhost"
    mock_cfg.mqtt_port = 1883
    mock_cfg.mqtt_username = None
    mock_cfg.mqtt_password = None
    mock_cfg.mqtt_use_tls = False
    mock_cfg.hass_discovery_enabled = False
    mock_cfg.language = "fr"

    # Mock parser
    parser_inst = mock_parser.return_value
    parser_inst.get_stop_id.return_value = "stop_id"

    # Mock upcoming bus in 30 seconds
    now = datetime.datetime.now()
    arrival_dt = now + datetime.timedelta(seconds=30)

    mock_next_stop = MagicMock()
    mock_next_stop.arrival_datetime = arrival_dt
    mock_next_stop.arrival_time = arrival_dt.strftime("%H:%M:%S")
    mock_next_stop.route_id = "1"
    mock_next_stop.trip_headsign = "Test"
    mock_next_stop.retrieve_method = "GTFS"

    parser_inst.get_next_stop.return_value = mock_next_stop

    # We want to break the loop after one iteration
    mock_event_wait.side_effect = KeyboardInterrupt()

    try:
        start_mqtt_client()
    except KeyboardInterrupt:
        pass

    # Interval should be 30 + 10 = 40 seconds
    # refresh_event.wait is called with timeout=min(remaining, 60)
    args, kwargs = mock_event_wait.call_args
    timeout = kwargs["timeout"]
    assert 35 <= timeout <= 45


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
@patch("transit_schedule.mqtt_client.threading.Event.wait")
def test_mqtt_loop_interval_fallback(mock_event_wait, mock_parser, mock_mqtt_client, mock_cfg):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    # Mock config
    mock_cfg.stops = [{"stop_code": "12345"}]
    mock_cfg.mqtt_host = "localhost"
    mock_cfg.mqtt_port = 1883
    mock_cfg.mqtt_username = None
    mock_cfg.mqtt_password = None
    mock_cfg.mqtt_use_tls = False
    mock_cfg.hass_discovery_enabled = False
    mock_cfg.language = "fr"

    # Mock parser
    parser_inst = mock_parser.return_value
    parser_inst.get_stop_id.return_value = "stop_id"

    # No bus found
    parser_inst.get_next_stop.return_value = None

    # We want to break the loop after one iteration
    mock_event_wait.side_effect = KeyboardInterrupt()

    try:
        start_mqtt_client()
    except KeyboardInterrupt:
        pass

    # Fallback interval is 3600, but wait is called with min(3600, 60)
    mock_event_wait.assert_called_once_with(timeout=60)


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
@patch("transit_schedule.mqtt_client.threading.Event.wait")
def test_mqtt_loop_interval_no_cap(mock_event_wait, mock_parser, mock_mqtt_client, mock_cfg):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    # Mock config
    mock_cfg.stops = [{"stop_code": "12345"}]
    mock_cfg.mqtt_host = "localhost"
    mock_cfg.mqtt_port = 1883
    mock_cfg.mqtt_username = None
    mock_cfg.mqtt_password = None
    mock_cfg.mqtt_use_tls = False
    mock_cfg.hass_discovery_enabled = False
    mock_cfg.language = "fr"

    # Mock parser
    parser_inst = mock_parser.return_value
    parser_inst.get_stop_id.return_value = "stop_id"

    # Mock upcoming bus in 1 hour
    now = datetime.datetime.now()
    arrival_dt = now + datetime.timedelta(hours=1)

    mock_next_stop = MagicMock()
    mock_next_stop.arrival_datetime = arrival_dt
    mock_next_stop.arrival_time = arrival_dt.strftime("%H:%M:%S")
    mock_next_stop.route_id = "1"
    mock_next_stop.trip_headsign = "Test"
    mock_next_stop.retrieve_method = "GTFS"

    parser_inst.get_next_stop.return_value = mock_next_stop

    # We want to break the loop after one iteration
    mock_event_wait.side_effect = KeyboardInterrupt()

    try:
        start_mqtt_client()
    except KeyboardInterrupt:
        pass

    # Interval is 3600+10, but wait is called with min(3610, 60)
    mock_event_wait.assert_called_once_with(timeout=60)


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
@patch("transit_schedule.mqtt_client.threading.Event.wait")
def test_mqtt_loop_interval_multiple_stops(mock_event_wait, mock_parser, mock_mqtt_client, mock_cfg):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False

    # Mock config with TWO stops
    mock_cfg.stops = [{"stop_code": "31592", "route_id": "14"}, {"stop_code": "32752", "route_id": "44"}]
    mock_cfg.mqtt_host = "localhost"
    mock_cfg.mqtt_port = 1883
    mock_cfg.mqtt_username = None
    mock_cfg.mqtt_password = None
    mock_cfg.mqtt_use_tls = False
    mock_cfg.hass_discovery_enabled = False
    mock_cfg.language = "fr"
    mock_cfg.get_mqtt_state_topic.return_value = "topic"

    # Mock parser
    parser_inst = mock_parser.return_value
    parser_inst.get_stop_id.side_effect = ["id1", "id2"]

    now = datetime.datetime.now()

    # Bus 1 arrives in 200 seconds
    arrival1 = now + datetime.timedelta(seconds=200)
    mock_stop1 = MagicMock()
    mock_stop1.arrival_datetime = arrival1
    mock_stop1.retrieve_method = "GTFS"

    # Bus 2 arrives in 100 seconds (The Earliest)
    arrival2 = now + datetime.timedelta(seconds=100)
    mock_stop2 = MagicMock()
    mock_stop2.arrival_datetime = arrival2
    mock_stop2.retrieve_method = "GTFS"

    # Side effect for get_next_stop: first call for stop1, second for stop2
    parser_inst.get_next_stop.side_effect = [mock_stop1, mock_stop2]

    mock_event_wait.side_effect = KeyboardInterrupt()

    try:
        start_mqtt_client()
    except KeyboardInterrupt:
        pass

    # The interval should be based on arrival2 (100s) + 10s = 110s
    # Capped at default max_poll_interval=60s, refresh_event.wait is called with ~60s
    args, kwargs = mock_event_wait.call_args
    assert 55 <= kwargs["timeout"] <= 60

    # Verify that get_next_stop was called for both stops
    assert parser_inst.get_next_stop.call_count == 2


@patch("transit_schedule.mqtt_client.config")
@patch("transit_schedule.mqtt_client.mqtt.Client")
@patch("transit_schedule.mqtt_client.ParseTransitData")
@patch("transit_schedule.mqtt_client.threading.Event.wait")
def test_mqtt_loop_interval_custom_max_poll(mock_event_wait, mock_parser, mock_mqtt_client, mock_cfg):
    import transit_schedule.mqtt_client

    transit_schedule.mqtt_client._MQTT_LOOP_RUNNING = False
    mock_cfg.stops = [{"stop_code": "12345"}]
    mock_cfg.mqtt_host = "localhost"
    mock_cfg.mqtt_port = 1883
    mock_cfg.mqtt_username = None
    mock_cfg.mqtt_password = None
    mock_cfg.mqtt_use_tls = False
    mock_cfg.hass_discovery_enabled = False
    mock_cfg.language = "fr"
    mock_cfg.max_poll_interval = 45

    parser_inst = mock_parser.return_value
    parser_inst.get_stop_id.return_value = "stop_id"

    now = datetime.datetime.now()
    arrival_dt = now + datetime.timedelta(minutes=20)

    mock_next_stop = MagicMock()
    mock_next_stop.arrival_datetime = arrival_dt
    mock_next_stop.arrival_time = arrival_dt.strftime("%H:%M:%S")
    mock_next_stop.route_id = "1"
    mock_next_stop.trip_headsign = "Test"
    mock_next_stop.retrieve_method = "GTFS"

    parser_inst.get_next_stop.return_value = mock_next_stop
    mock_event_wait.side_effect = KeyboardInterrupt()

    try:
        start_mqtt_client()
    except KeyboardInterrupt:
        pass

    args, kwargs = mock_event_wait.call_args
    assert 40 <= kwargs["timeout"] <= 45
