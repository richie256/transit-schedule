import datetime
import os
from unittest.mock import patch

import pytest

from transit_schedule.data_parser import ParseTransitData


# Helper to check if real GTFS data is available
def has_gtfs_data(transit):
    filename = "gtfs.zip" if transit == "RTL" else f"gtfs_{transit.lower()}.zip"
    path = f"data/{filename}"
    return os.path.exists(path)


def run_transit_integration(transit_name, stop_code):
    """Integration logic to verify real GTFS data compatibility."""
    with patch("transit_schedule.data_parser.config") as mock_cfg:
        mock_config = mock_cfg
        mock_config.transit = transit_name
        mock_config.retrieval_method = "gtfs"
        mock_config.target_direction = ""
        mock_config.gtfs_data_dir = "data"
        mock_config.gtfs_zip_file = "gtfs.zip" if transit_name == "RTL" else f"gtfs_{transit_name.lower()}.zip"

        parser = ParseTransitData()
        stop_id = parser.get_stop_id(stop_code)

        assert stop_id is not None, f"Stop code {stop_code} not found in {transit_name} GTFS"

        # Use a fixed date known to be in the downloaded GTFS range
        test_date = datetime.datetime(2026, 4, 20, 4, 0, 0)

        next_stop = parser.get_next_stop(stop_id, test_date, stop_code=str(stop_code))

        assert next_stop is not None, f"No bus found for {transit_name} stop {stop_code} at {test_date}"
        assert next_stop.route_id is not None
        assert next_stop.arrival_datetime > test_date


@pytest.mark.skipif(not has_gtfs_data("STM"), reason="STM GTFS data not found in data/")
def test_stm_integration():
    run_transit_integration("STM", 54117)


@pytest.mark.skipif(not has_gtfs_data("STL"), reason="STL GTFS data not found in data/")
def test_stl_integration():
    run_transit_integration("STL", 40002)


@pytest.mark.skipif(not has_gtfs_data("RTL"), reason="RTL GTFS data not found in data/")
def test_rtl_integration():
    # Use a known RTL stop code
    run_transit_integration("RTL", 32752)
