import logging

from transit_schedule.config import config

_LOGGER = logging.getLogger("transit-schedule")

TRANSIT = config.transit
GTFS_URL = config.gtfs_url
GTFS_ZIP_FILE = config.gtfs_zip_file
DEFAULT_TIMEZONE = config.timezone
RETRIEVAL_METHOD = config.retrieval_method
LANGUAGE = config.language
TARGET_DIRECTION = config.target_direction
TARGET_ROUTE = config.target_route

TRANSLATIONS = {
    "en": {
        "next_bus_at_stop": "Stop {stop_code}",
        "transit_schedule": f"Next {TRANSIT} Bus",
        "gtfs": "GTFS",
        "live_scraper": "Live Scraper",
        "no_more_buses": "No more buses for today.",
        "refresh_action_received": "Refresh action received",
        "refresh_period_ended": "Refresh period ended",
        "hass_status_received": "Home Assistant status change received",
        "waiting_for": "Waiting for {interval} seconds...",
    },
    "fr": {
        "next_bus_at_stop": "Arrêt {stop_code}",
        "transit_schedule": f"Prochain bus {TRANSIT}",
        "gtfs": "GTFS",
        "live_scraper": "Scraper en direct",
        "no_more_buses": "Plus de bus pour aujourd'hui.",
        "refresh_action_received": "Action de rafraîchissement reçue",
        "refresh_period_ended": "Période de rafraîchissement terminée",
        "hass_status_received": "Changement de statut Home Assistant reçu",
        "waiting_for": "Attente de {interval} secondes...",
    }
}

