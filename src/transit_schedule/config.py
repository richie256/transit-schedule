
import logging
import os
from typing import Any

_LOGGER = logging.getLogger("transit-schedule")

class Config:
    def __init__(self):
        # Transit Configuration
        self.transit = os.environ.get("TRANSIT", "RTL").upper()
        
        # GTFS Configuration
        default_urls = {
            "RTL": "http://www.rtl-longueuil.qc.ca/transit/latestfeed/RTL.zip",
            "STM": "https://www.stm.info/sites/default/files/gtfs/gtfs_stm.zip",
            "STL": "https://www.stlaval.ca/datas/opendata/GTF_STL.zip"
        }
        self.gtfs_url = os.environ.get("GTFS_URL", default_urls.get(self.transit, default_urls["RTL"]))
        self.gtfs_zip_file = os.environ.get("GTFS_ZIP_FILE", f"gtfs_{self.transit.lower()}.zip")
        self.gtfs_data_dir = os.environ.get("GTFS_DATA_DIR", "data")
        self.retrieval_method = os.environ.get("RETRIEVAL_METHOD", "gtfs" if self.transit != "RTL" else "live").lower()
        self.timezone = os.environ.get("TZ", "America/Montreal")
        self.language = os.environ.get("LANGUAGE", "fr").lower()
        
        # Filtering Configuration
        default_directions = {
            "RTL": "Direction Terminus Panama",
            "STM": "",
            "STL": ""
        }
        self.force_cache_refresh = os.environ.get("FORCE_CACHE_REFRESH", "False").lower() == "true"

        # MQTT Configuration
        self.mqtt_host = os.environ.get("MQTT_HOST")
        try:
            self.mqtt_port = int(os.environ.get("MQTT_PORT", 1883))
        except (ValueError, TypeError) as e:
            _LOGGER.error(f"Error parsing MQTT_PORT: {e}. Using default 1883.")
            self.mqtt_port = 1883
        self.mqtt_username = os.environ.get("MQTT_USERNAME")
        self.mqtt_password = os.environ.get("MQTT_PASSWORD")
        self.mqtt_use_tls = os.environ.get("MQTT_USE_TLS", "False").lower() == "true"
        
        # Stop Configuration
        self.stops = []
        stops_config = os.environ.get("STOPS_CONFIG")
        if stops_config:
            try:
                import json
                self.stops = json.loads(stops_config)
                # Ensure each stop has required fields and proper types
                for stop in self.stops:
                    stop['stop_code'] = str(stop['stop_code'])
            except Exception as e:
                _LOGGER.error(f"Error parsing STOPS_CONFIG: {e}")

        if not self.stops:
            stop_code_env = os.environ.get("STOP_CODE")
            if stop_code_env:
                try:
                    # Validate it's an integer for legacy reasons
                    int(stop_code_env)
                    self.stops.append({
                        "stop_code": str(stop_code_env),
                        "route_id": os.environ.get("TARGET_ROUTE"),
                        "direction": os.environ.get("TARGET_DIRECTION", default_directions.get(self.transit, ""))
                    })
                except ValueError:
                    _LOGGER.error("STOP_CODE must be an integer")

        # Compatibility for single stop code
        self._stop_code = self.stops[0]['stop_code'] if self.stops else None

        # Home Assistant Discovery
        self.hass_discovery_enabled = os.environ.get("HASS_DISCOVERY_ENABLED", "False").lower() == "true"
        self.hass_discovery_prefix = os.environ.get("HASS_DISCOVERY_PREFIX", "homeassistant")

        # MQTT Topics
        self.mqtt_refresh_topic = os.environ.get("MQTT_REFRESH_TOPIC", f"{self.transit.lower()}/schedule/refresh")
        
        # State topic for single-stop compatibility
        self.mqtt_state_topic = os.environ.get("MQTT_STATE_TOPIC", f"home/transit/{self.transit.lower()}/stop_{self.stop_code}" if self.stop_code else f"home/transit/{self.transit.lower()}/stop_unknown")
        
        self.mqtt_hass_status_topic = os.environ.get("MQTT_HASS_STATUS_TOPIC", f"{self.hass_discovery_prefix}/status")

    @property
    def stop_code(self):
        return self._stop_code

    @property
    def target_direction(self):
        return self.stops[0].get('direction', "") if self.stops else ""

    @property
    def target_route(self):
        return self.stops[0].get('route_id') if self.stops else None

    def get_mqtt_state_topic(self, stop_config: dict) -> str:
        """Returns the MQTT state topic for a specific stop configuration."""
        stop_code = stop_config['stop_code']
        route_id = stop_config.get('route_id')
        if route_id:
            return f"home/transit/{self.transit.lower()}/stop_{stop_code}_{route_id}"
        return f"home/transit/{self.transit.lower()}/stop_{stop_code}"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def to_safe_dict(self) -> dict[str, Any]:
        d = self.to_dict()
        if d.get('mqtt_password'):
            d['mqtt_password'] = '***'
        return d

# Global config instance
config = Config()
