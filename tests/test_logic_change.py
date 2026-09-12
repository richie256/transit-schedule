import datetime
import logging
import os
from unittest.mock import patch

# Mock environment variable for the test
os.environ["RETRIEVAL_METHOD"] = "live"

from transit_schedule.const import RETRIEVAL_METHOD
from transit_schedule.data_parser import ParseTransitData

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("transit-schedule")


@patch("transit_schedule.data_parser.HastusScraper")
@patch("transit_schedule.data_parser.requests.get")
def test_retrieval_logic(mock_requests_get, mock_hastus_scraper):
    # Prevent any real HTTP calls during init (no zip on disk in CI)
    mock_requests_get.return_value.raise_for_status.return_value = None

    # Simulate the live scraper returning no results
    mock_hastus_scraper.return_value.get_schedule.return_value = []

    print(f"Testing with RETRIEVAL_METHOD: {RETRIEVAL_METHOD}")

    parser = ParseTransitData()
    stop_id = 2752  # Stop 32752
    now = datetime.datetime(2026, 3, 30, 12, 0)

    # This should log "Skipping GTFS check"
    next_stop = parser.get_next_stop(stop_id, now)

    if next_stop is not None:
        print(f"Result method: {next_stop.get('retrieve_method')}")
    else:
        print("No next stop found.")


if __name__ == "__main__":
    test_retrieval_logic()
