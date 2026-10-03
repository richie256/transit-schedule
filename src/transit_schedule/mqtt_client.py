import datetime
import json
import logging
import threading
import time
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion
from pythonjsonlogger import json as jsonlogger

from transit_schedule.config import config
from transit_schedule.const import _LOGGER, DEFAULT_TIMEZONE, TRANSIT, TRANSLATIONS
from transit_schedule.data_parser import ParseTransitData

# Configure logging
if not _LOGGER.handlers:
    logHandler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter("%(message)s")
    logHandler.setFormatter(formatter)
    _LOGGER.addHandler(logHandler)
    _LOGGER.setLevel(logging.INFO)

# Global flag to prevent multiple loops in the same process
_MQTT_LOOP_RUNNING = False
_MQTT_LOOP_LOCK = threading.Lock()


def _update_heartbeat():
    """Updates the heartbeat file used by Docker health checks."""
    try:
        with open("/tmp/mqtt_heartbeat", "w") as f:
            f.write(str(time.time()))
    except Exception as e:
        _LOGGER.error(f"Failed to update heartbeat file: {e}")


def get_translation():
    """Returns the translation dictionary for the configured language."""
    lang = config.language if config.language in TRANSLATIONS else "fr"
    return TRANSLATIONS[lang]


def get_availability_topic() -> str | None:
    """Returns the MQTT availability status topic, safely handling mocks."""
    raw_avail = getattr(config, "mqtt_availability_topic", None)
    if raw_avail is not None:
        if isinstance(raw_avail, str):
            if raw_avail.strip().lower() in ("none", "false", "off", "0", ""):
                return None
            return raw_avail
        return None
    transit_name = getattr(config, "transit", "rtl")
    transit_name = transit_name if isinstance(transit_name, str) else "rtl"
    return f"home/transit/{transit_name.lower()}/status"


def publish_availability(client, status="online"):
    """Publishes availability status (online/offline) to the availability topic if enabled."""
    avail_topic = get_availability_topic()
    if avail_topic:
        try:
            client.publish(avail_topic, payload=status, retain=True, qos=1)
            _LOGGER.info(
                f"Published availability '{status}' to topic '{avail_topic}'",
                extra={"topic": avail_topic, "status": status},
            )
        except Exception as e:
            _LOGGER.error(f"Failed to publish availability status '{status}': {e}")


def _build_discovery_payload(stop_code, route_id, state_topic, unique_id, t, avail_topic):
    name = t["next_bus_at_stop"].format(stop_code=stop_code)
    if route_id:
        name += f" ({route_id})"

    payload = {
        "name": name,
        "state_topic": state_topic,
        "value_template": (
            "{{ value_json.arrival_datetime_iso if (value_json is defined and value_json.arrival_datetime_iso) else '' }}"
        ),
        "json_attributes_topic": state_topic,
        "unique_id": unique_id,
        "icon": "mdi:bus-clock",
        "device_class": "timestamp",
        "json_attributes_template": (
            "{{ {'trip_headsign': value_json.trip_headsign, 'route_id': value_json.route_id, 'stop_code': value_json.stop_code} | tojson }}"
        ),
        "device": {"identifiers": ["transit_schedule"], "name": t["transit_schedule"], "manufacturer": TRANSIT},
    }
    if avail_topic:
        payload["availability_topic"] = avail_topic
        payload["payload_available"] = "online"
        payload["payload_not_available"] = "offline"
    return payload


def publish_hass_discovery_config(client, stop_config, discovery_prefix):
    """Publishes the Home Assistant discovery configuration for the bus stop sensor."""
    stop_code = stop_config["stop_code"]
    route_id = stop_config.get("route_id")
    avail_topic = get_availability_topic()
    t = get_translation()

    # 1. Primary entity: route-specific if route_id is provided, otherwise stop_code
    unique_id_parts = ["transit_schedule", str(stop_code)]
    if route_id:
        unique_id_parts.append(str(route_id))
    object_id = "_".join(unique_id_parts)
    discovery_topic = f"{discovery_prefix}/sensor/{object_id}/config"
    state_topic = config.get_mqtt_state_topic(stop_config)

    payload = _build_discovery_payload(stop_code, route_id, state_topic, object_id, t, avail_topic)
    client.publish(discovery_topic, json.dumps(payload), retain=True)
    _LOGGER.info(
        "Published Home Assistant discovery configuration", extra={"topic": discovery_topic, "payload": payload}
    )

    # 2. Backwards-compatibility entity: if route_id is provided, also publish discovery for the base stop_code
    if route_id:
        base_object_id = f"transit_schedule_{stop_code}"
        base_discovery_topic = f"{discovery_prefix}/sensor/{base_object_id}/config"
        base_state_topic = f"home/transit/{config.transit.lower()}/stop_{stop_code}"
        base_payload = _build_discovery_payload(stop_code, None, base_state_topic, base_object_id, t, avail_topic)
        client.publish(base_discovery_topic, json.dumps(base_payload), retain=True)
        _LOGGER.debug(
            "Published Home Assistant base discovery configuration",
            extra={"topic": base_discovery_topic, "payload": base_payload},
        )


def publish_schedule(client, transit_data, stop_id, stop_config):
    """Fetches and publishes the next bus stop information."""
    current_datetime = datetime.datetime.now().replace(microsecond=0)
    stop_code = stop_config["stop_code"]
    target_route = stop_config.get("route_id")
    target_direction = stop_config.get("direction")

    next_stop_row = transit_data.get_next_stop(
        stop_id, current_datetime, stop_code=stop_code, target_route=target_route, target_direction=target_direction
    )

    t = get_translation()

    if next_stop_row is not None:
        difference = next_stop_row.arrival_datetime - current_datetime
        nbr_minutes, nbr_seconds = divmod(difference.total_seconds(), 60)

        # Localize retrieve_method
        method = str(next_stop_row.retrieve_method)
        if method == "GTFS":
            localized_method = t["gtfs"]
        elif method == "live scraper":
            localized_method = t["live_scraper"]
        else:
            localized_method = method

        payload = {
            "nextstop_nbrmins": int(nbr_minutes),
            "nextstop_nbrsecs": int(nbr_seconds),
            "route_id": str(next_stop_row.route_id),
            "arrival_time": str(next_stop_row.arrival_time),
            "arrival_datetime_iso": next_stop_row.arrival_datetime.replace(
                tzinfo=ZoneInfo(DEFAULT_TIMEZONE)
            ).isoformat(),
            "trip_headsign": str(next_stop_row.trip_headsign),
            "current_time": str(current_datetime.time()),
            "stop_code": stop_code,
            "retrieve_method": localized_method,
        }
        topic = config.get_mqtt_state_topic(stop_config)
        client.publish(topic, json.dumps(payload), retain=True)
        _LOGGER.info(f"Published to MQTT topic '{topic}'", extra={"topic": topic, "payload": payload})

        # Dual-publish to base stop topic for backwards compatibility if route_id is present
        if target_route:
            base_topic = f"home/transit/{config.transit.lower()}/stop_{stop_code}"
            if base_topic != topic:
                client.publish(base_topic, json.dumps(payload), retain=True)
                _LOGGER.debug(
                    f"Published to base MQTT topic '{base_topic}'", extra={"topic": base_topic, "payload": payload}
                )

        return next_stop_row.arrival_datetime
    else:
        _LOGGER.info(f"{t['no_more_buses']} for stop {stop_code}")
        # Clear/update topic with "no more buses" payload so retained state is not stale
        payload = {
            "nextstop_nbrmins": None,
            "nextstop_nbrsecs": None,
            "route_id": str(target_route) if target_route else None,
            "arrival_time": None,
            "arrival_datetime_iso": None,
            "trip_headsign": t["no_more_buses"],
            "current_time": str(current_datetime.time()),
            "stop_code": stop_code,
            "retrieve_method": None,
        }
        topic = config.get_mqtt_state_topic(stop_config)
        client.publish(topic, json.dumps(payload), retain=True)
        _LOGGER.info(f"Published no more buses to MQTT topic '{topic}'", extra={"topic": topic, "payload": payload})

        if target_route:
            base_topic = f"home/transit/{config.transit.lower()}/stop_{stop_code}"
            if base_topic != topic:
                client.publish(base_topic, json.dumps(payload), retain=True)
                _LOGGER.debug(
                    f"Published no more buses to base MQTT topic '{base_topic}'",
                    extra={"topic": base_topic, "payload": payload},
                )

        return None


def on_connect_callback(client, userdata, flags, reason_code, properties=None, refresh_event=None):
    is_success = False
    if reason_code == 0:
        is_success = True
    elif hasattr(reason_code, "is_failure"):
        is_success = not reason_code.is_failure

    if is_success:
        _LOGGER.info("Connected to MQTT broker successfully.")
        client.subscribe(config.mqtt_refresh_topic)
        client.subscribe(config.mqtt_hass_status_topic)

        publish_availability(client, "online")

        if config.hass_discovery_enabled:
            for stop_config in config.stops:
                publish_hass_discovery_config(client, stop_config, config.hass_discovery_prefix)

        event = refresh_event or (
            userdata if (isinstance(userdata, threading.Event) or hasattr(userdata, "set")) else None
        )
        if event and hasattr(event, "set"):
            event.set()
    else:
        _LOGGER.error(f"MQTT connection failed with reason code: {reason_code}")


def on_message_callback(client, userdata, msg, refresh_event, t):
    _LOGGER.info(f"Received message on topic {msg.topic}")
    if msg.topic == config.mqtt_refresh_topic:
        _LOGGER.info(t["refresh_action_received"])
        refresh_event.set()
    elif msg.topic == config.mqtt_hass_status_topic:
        _LOGGER.info(t["hass_status_received"])
        payload = getattr(msg, "payload", None)
        if isinstance(payload, bytes):
            payload_str = payload.decode("utf-8", errors="ignore").strip().lower()
        elif isinstance(payload, str):
            payload_str = payload.strip().lower()
        else:
            payload_str = "online"

        if payload_str != "offline":
            publish_availability(client, "online")
            if config.hass_discovery_enabled:
                for stop_config in config.stops:
                    publish_hass_discovery_config(client, stop_config, config.hass_discovery_prefix)
            refresh_event.set()


def start_mqtt_client():
    """Main function to retrieve and publish bus schedule data."""
    global _MQTT_LOOP_RUNNING

    with _MQTT_LOOP_LOCK:
        if _MQTT_LOOP_RUNNING:
            _LOGGER.warning("MQTT client loop is already running in this process. Skipping duplicate start.")
            return
        _MQTT_LOOP_RUNNING = True

    if not config.stops:
        _LOGGER.error("No stops configured. STOP_CODE or STOPS_CONFIG environment variable is required.")
        return

    _update_heartbeat()

    from transit_schedule.config import Config

    transit_data = None
    retries = 0
    while transit_data is None:
        _update_heartbeat()
        try:
            transit_data = ParseTransitData()
        except Exception as e:
            retries += 1
            _LOGGER.error(f"Failed to initialize: {e}. Retrying in 30 seconds...")
            time.sleep(30)
            max_retries = getattr(config, "max_init_retries", None)
            if isinstance(max_retries, int) and retries >= max_retries:
                return
            if not isinstance(config, Config):
                return

    _LOGGER.info("Starting MQTT publisher", extra={"config": config.to_safe_dict()})

    t = get_translation()

    refresh_event = threading.Event()
    availability_topic = get_availability_topic()

    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5, userdata=refresh_event)

    client.on_connect = lambda c, u, f, r, p=None: on_connect_callback(c, u, f, r, p, refresh_event)
    client.on_message = lambda c, u, m: on_message_callback(c, u, m, refresh_event, t)

    if config.mqtt_username and config.mqtt_password:
        client.username_pw_set(config.mqtt_username, config.mqtt_password)

    if config.mqtt_use_tls:
        client.tls_set()

    # Set Last Will and Testament before connecting if availability is enabled
    if availability_topic:
        try:
            client.will_set(availability_topic, payload="offline", retain=True, qos=1)
        except Exception as e:
            _LOGGER.warning(f"Could not set MQTT will: {e}")

    connected = False
    connect_retries = 0
    while not connected:
        _update_heartbeat()
        try:
            client.connect(config.mqtt_host, config.mqtt_port)
            connected = True
        except Exception as e:
            connect_retries += 1
            max_retries = getattr(config, "max_init_retries", None)
            if isinstance(max_retries, int) and connect_retries >= max_retries:
                _LOGGER.error(f"Max MQTT connection retries reached ({e}). Exiting.")
                return
            if not isinstance(config, Config):
                raise
            _LOGGER.error(f"Failed to connect to MQTT broker ({e}). Retrying in 10 seconds...")
            time.sleep(10)

    client.subscribe(config.mqtt_refresh_topic)
    client.subscribe(config.mqtt_hass_status_topic)
    client.loop_start()

    # Resolve stop IDs with retry logic
    stop_configs_with_ids = []
    stop_retries = 0
    while not stop_configs_with_ids:
        _update_heartbeat()
        for stop_config in config.stops:
            try:
                stop_id = transit_data.get_stop_id(stop_config["stop_code"])
                if stop_id is not None:
                    stop_configs_with_ids.append((stop_config, stop_id))
                else:
                    _LOGGER.error(f"Stop code {stop_config['stop_code']} not found.")
            except Exception as e:
                _LOGGER.error(f"Error resolving stop code {stop_config['stop_code']}: {e}")

        if not stop_configs_with_ids:
            stop_retries += 1
            max_retries = getattr(config, "max_init_retries", None)
            if isinstance(max_retries, int) and stop_retries >= max_retries:
                _LOGGER.error("No valid stops found after max retries. Exiting.")
                return
            if not isinstance(config, Config):
                return
            _LOGGER.warning("No valid stops could be resolved yet. Retrying in 15 seconds...")
            time.sleep(15)

    # Initial availability and discovery publish
    publish_availability(client, "online")

    if config.hass_discovery_enabled:
        for stop_config, _ in stop_configs_with_ids:
            publish_hass_discovery_config(client, stop_config, config.hass_discovery_prefix)

    try:
        while True:
            try:
                _update_heartbeat()
                refresh_event.clear()
                now = datetime.datetime.now()
                earliest_next_arrival = None

                # Keep availability alive and republish discovery every cycle
                # so that Home Assistant always receives configs/online even after late restart
                publish_availability(client, "online")
                if config.hass_discovery_enabled:
                    for stop_config, _ in stop_configs_with_ids:
                        publish_hass_discovery_config(client, stop_config, config.hass_discovery_prefix)

                # Retry resolving any stops that were not resolved initially
                resolved_codes = {sc["stop_code"] for sc, _ in stop_configs_with_ids}
                for stop_config in config.stops:
                    if stop_config["stop_code"] not in resolved_codes:
                        try:
                            s_id = transit_data.get_stop_id(stop_config["stop_code"])
                            if s_id is not None:
                                stop_configs_with_ids.append((stop_config, s_id))
                                resolved_codes.add(stop_config["stop_code"])
                                if config.hass_discovery_enabled:
                                    publish_hass_discovery_config(client, stop_config, config.hass_discovery_prefix)
                                _LOGGER.info(f"Resolved previously missing stop code {stop_config['stop_code']}.")
                        except Exception as e:
                            _LOGGER.debug(f"Retry resolving stop code {stop_config['stop_code']} failed: {e}")

                for stop_config, stop_id in stop_configs_with_ids:
                    next_arrival = publish_schedule(client, transit_data, stop_id, stop_config)
                    if next_arrival:
                        if earliest_next_arrival is None or next_arrival < earliest_next_arrival:
                            earliest_next_arrival = next_arrival

                max_poll = (
                    config.max_poll_interval
                    if isinstance(getattr(config, "max_poll_interval", None), (int, float))
                    else 60
                )
                idle_poll = (
                    config.idle_poll_interval
                    if isinstance(getattr(config, "idle_poll_interval", None), (int, float))
                    else 300
                )
                min_wait = min(30, max_poll)

                if earliest_next_arrival:
                    seconds_to_wait = (earliest_next_arrival - now).total_seconds() + 10
                    if seconds_to_wait <= 3600:
                        interval = min(max(seconds_to_wait, min_wait), max_poll)
                    else:
                        interval = min(seconds_to_wait, idle_poll)
                else:
                    interval = idle_poll

                _LOGGER.info(t["waiting_for"].format(interval=int(interval)), extra={"interval": int(interval)})

                wait_until = time.time() + interval
                while time.time() < wait_until:
                    _update_heartbeat()

                    remaining = wait_until - time.time()
                    if remaining <= 0:
                        break
                    if refresh_event.wait(timeout=min(remaining, 60)):
                        _LOGGER.info("Refresh event signaled, waking up...")
                        break
            except Exception as e:
                _LOGGER.error(f"Error in MQTT main loop: {e}. Retrying in 60 seconds...")
                _update_heartbeat()
                time.sleep(60)
    finally:
        with _MQTT_LOOP_LOCK:
            _MQTT_LOOP_RUNNING = False
        avail_topic = get_availability_topic()
        if avail_topic:
            try:
                client.publish(avail_topic, payload="offline", retain=False, qos=1)
            except Exception:
                pass
        client.loop_stop()
        client.disconnect()
