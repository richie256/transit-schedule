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

def get_translation():
    """Returns the translation dictionary for the configured language."""
    lang = config.language if config.language in TRANSLATIONS else "fr"
    return TRANSLATIONS[lang]

def publish_hass_discovery_config(client, stop_config, discovery_prefix):
    """Publishes the Home Assistant discovery configuration for the bus stop sensor."""
    stop_code = stop_config['stop_code']
    route_id = stop_config.get('route_id')
    
    unique_id_parts = ["transit_schedule", str(stop_code)]
    if route_id:
        unique_id_parts.append(str(route_id))
    
    object_id = "_".join(unique_id_parts)
    discovery_topic = f"{discovery_prefix}/sensor/{object_id}/config"
    state_topic = config.get_mqtt_state_topic(stop_config)
    
    t = get_translation()

    name = t["next_bus_at_stop"].format(stop_code=stop_code)
    if route_id:
        name += f" ({route_id})"

    payload = {
        "name": name,
        "state_topic": state_topic,
        "value_template": "{{ value_json.arrival_datetime_iso }}",
        "json_attributes_topic": state_topic,
        "unique_id": object_id,
        "icon": "mdi:bus-clock",
        "device_class": "timestamp",
        "json_attributes_template": "{{ {'trip_headsign': value_json.trip_headsign, 'route_id': value_json.route_id, 'stop_code': value_json.stop_code} | tojson }}",
        "device": {
            "identifiers": ["transit_schedule"],
            "name": t["transit_schedule"],
            "manufacturer": TRANSIT
        }
    }

    client.publish(discovery_topic, json.dumps(payload), retain=True)
    _LOGGER.info("Published Home Assistant discovery configuration", extra={"topic": discovery_topic, "payload": payload})

def publish_schedule(client, transit_data, stop_id, stop_config):
    """Fetches and publishes the next bus stop information."""
    current_datetime = datetime.datetime.now().replace(microsecond=0)
    stop_code = stop_config['stop_code']
    target_route = stop_config.get('route_id')
    target_direction = stop_config.get('direction')

    next_stop_row = transit_data.get_next_stop(
        stop_id, 
        current_datetime, 
        stop_code=stop_code,
        target_route=target_route,
        target_direction=target_direction
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
            'nextstop_nbrmins': int(nbr_minutes),
            'nextstop_nbrsecs': int(nbr_seconds),
            'route_id': str(next_stop_row.route_id),
            'arrival_time': str(next_stop_row.arrival_time),
            'arrival_datetime_iso': next_stop_row.arrival_datetime.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE)).isoformat(),
            'trip_headsign': str(next_stop_row.trip_headsign),
            'current_time': str(current_datetime.time()),
            'stop_code': stop_code,
            'retrieve_method': localized_method
        }
        topic = config.get_mqtt_state_topic(stop_config)
        client.publish(topic, json.dumps(payload), retain=True)
        _LOGGER.info(f"Published to MQTT topic '{topic}'", extra={"topic": topic, "payload": payload})
        return next_stop_row.arrival_datetime
    else:
        _LOGGER.info(f"{t['no_more_buses']} for stop {stop_code}")
        return None

def on_message_callback(client, userdata, msg, refresh_event, t):
    _LOGGER.info(f"Received message on topic {msg.topic}")
    if msg.topic == config.mqtt_refresh_topic:
        _LOGGER.info(t["refresh_action_received"])
        refresh_event.set()
    elif msg.topic == config.mqtt_hass_status_topic:
        _LOGGER.info(t["hass_status_received"])
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

    try:
        transit_data = ParseTransitData()
    except Exception as e:
        _LOGGER.error(f"Failed to initialize: {e}. Retrying in 30 seconds...")
        time.sleep(30)
        return

    _LOGGER.info("Starting MQTT publisher", extra={"config": config.to_safe_dict()})
    
    t = get_translation()

    refresh_event = threading.Event()

    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)

    client.on_message = lambda c, u, m: on_message_callback(c, u, m, refresh_event, t)

    if config.mqtt_username and config.mqtt_password:
        client.username_pw_set(config.mqtt_username, config.mqtt_password)

    if config.mqtt_use_tls:
        client.tls_set()

    client.connect(config.mqtt_host, config.mqtt_port)
    client.subscribe(config.mqtt_refresh_topic)
    client.subscribe(config.mqtt_hass_status_topic)
    client.loop_start()

    # Resolve stop IDs once
    stop_configs_with_ids = []
    for stop_config in config.stops:
        stop_id = transit_data.get_stop_id(stop_config['stop_code'])
        if stop_id is None:
            _LOGGER.error(f"Stop code {stop_config['stop_code']} not found.")
            continue
        stop_configs_with_ids.append((stop_config, stop_id))

    if not stop_configs_with_ids:
        _LOGGER.error("No valid stops found. Exiting.")
        return

    if config.hass_discovery_enabled:
        for stop_config, _ in stop_configs_with_ids:
            publish_hass_discovery_config(client, stop_config, config.hass_discovery_prefix)

    try:
        while True:
            try:
                refresh_event.clear()
                now = datetime.datetime.now()
                earliest_next_arrival = None

                for stop_config, stop_id in stop_configs_with_ids:
                    next_arrival = publish_schedule(client, transit_data, stop_id, stop_config)
                    if next_arrival:
                        if earliest_next_arrival is None or next_arrival < earliest_next_arrival:
                            earliest_next_arrival = next_arrival
                
                if earliest_next_arrival:
                    seconds_to_wait = (earliest_next_arrival - now).total_seconds() + 10
                    interval = max(seconds_to_wait, 30)
                else:
                    interval = 3600
                
                _LOGGER.info(t["waiting_for"].format(interval=int(interval)), extra={"interval": int(interval)})
                
                wait_until = time.time() + interval
                while time.time() < wait_until:
                    try:
                        with open("/tmp/mqtt_heartbeat", "w") as f:
                            f.write(str(time.time()))
                    except Exception as e:
                        _LOGGER.error(f"Failed to update heartbeat file: {e}")
                    
                    remaining = wait_until - time.time()
                    if remaining <= 0:
                        break
                    if refresh_event.wait(timeout=min(remaining, 60)):
                        _LOGGER.info("Refresh event signaled, waking up...")
                        break
            except Exception as e:
                _LOGGER.error(f"Error in MQTT main loop: {e}. Retrying in 60 seconds...")
                time.sleep(60)
    finally:
        with _MQTT_LOOP_LOCK:
            _MQTT_LOOP_RUNNING = False
        client.loop_stop()
        client.disconnect()
