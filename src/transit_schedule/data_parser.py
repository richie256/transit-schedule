import datetime
import os
import zipfile

import pandas
import requests
from pandas import Series, read_csv

from transit_schedule.config import config
from transit_schedule.const import (
    _LOGGER,
    GTFS_URL,
)
from transit_schedule.hastus_scraper import HastusScraper
from transit_schedule.util import is_file_expired


class NoServiceFoundError(ValueError):
    """Exception raised when no service is found for a given date."""
    pass

class ParseTransitData:
    def __init__(self):
        self.schedule_zipfile = config.gtfs_zip_file
        _LOGGER.info("ParseTransitData init")

        self.data_dir = config.gtfs_data_dir
        # Ensure data directory exists
        if not os.path.exists(self.data_dir):
            _LOGGER.info(f"Creating data directory: {self.data_dir}")
            os.makedirs(self.data_dir, exist_ok=True)
            
        self.file_path = os.path.join(self.data_dir, self.schedule_zipfile)
        self.scraper = HastusScraper()
        self.stops = pandas.DataFrame()
        self.calendar = pandas.DataFrame()
        self.stop_times = pandas.DataFrame()
        self.trips = pandas.DataFrame()
        self.calendar_dates = pandas.DataFrame()
        self.min_date = None
        self.max_date = None
        
        try:
            self._load_data()
        except Exception as e:
            if config.retrieval_method == "live":
                _LOGGER.warning(f"GTFS initialization failed: {e}. Continuing in LIVE mode.")
            else:
                raise


    def _load_data(self, force_download=False):
        """Download and load GTFS data into memory."""
        try:
            if force_download or not (os.path.isfile(self.file_path)) or is_file_expired(self.file_path):
                _LOGGER.info(f"Downloading a new zip file from [{GTFS_URL}]")
                self._download_gtfs_file(self.file_path)

            with zipfile.ZipFile(self.file_path) as my_zip:
                _LOGGER.info(f"Loading GTFS data from {self.file_path} into memory...")
                self.stops = read_csv(my_zip.open('stops.txt'), dtype={'stop_code': str}, index_col='stop_code')
                self.calendar = read_csv(my_zip.open('calendar.txt'), dtype={'service_id': str})
                self.stop_times = read_csv(
                    my_zip.open('stop_times.txt'),
                    dtype={'stop_id': str, 'trip_id': str},
                    index_col='stop_id'
                )
                self.trips = read_csv(
                    my_zip.open('trips.txt'),
                    dtype={'trip_id': str, 'service_id': str, 'route_id': str}
                )
                
                # Calculate global schedule range for diagnostics
                self.min_date = self.calendar['start_date'].min()
                self.max_date = self.calendar['end_date'].max()

                _LOGGER.info(f"Successfully loaded stops ({len(self.stops)}), calendar ({len(self.calendar)}), stop_times ({len(self.stop_times)}), and trips ({len(self.trips)})")
                _LOGGER.info(f"Global GTFS schedule range: {self.min_date} to {self.max_date}")
                
                # Load calendar_dates if it exists (it's optional in GTFS but common in RTL)
                try:
                    self.calendar_dates = read_csv(my_zip.open('calendar_dates.txt'), dtype={'service_id': str})
                    _LOGGER.info(f"Loaded calendar_dates.txt ({len(self.calendar_dates)} entries)")
                except KeyError:
                    self.calendar_dates = pandas.DataFrame(columns=['service_id', 'date', 'exception_type'])
                    _LOGGER.info("calendar_dates.txt not found in GTFS, using empty DataFrame")

        except FileNotFoundError:
            _LOGGER.error(f"GTFS file not found at {self.file_path}. Please check the file path and permissions.")
            raise
        except (zipfile.BadZipFile, pandas.errors.ParserError) as e:
            _LOGGER.error(f"An error occurred while parsing the GTFS file: {e}")
            raise

    def refresh(self, force=False):
        """Check if data needs to be refreshed and reload if necessary."""
        if force or is_file_expired(self.file_path):
            _LOGGER.info(f"Refreshing GTFS data (force={force})...")
            self._load_data(force_download=True)

    @staticmethod
    def _download_gtfs_file(zipfile_location) -> None:
        """ Download the GTFS file from the website, write it on disk. """
        my_file = requests.get(GTFS_URL, allow_redirects=True, timeout=60)
        my_file.raise_for_status()
        with open(zipfile_location, 'wb') as my_zip:
            my_zip.write(my_file.content)

    def get_stop_id(self, stop_code: int) -> str | None:
        """ Retrieve the stop_id based on a stop_code """
        self.refresh()

        if self.stops.empty:
            _LOGGER.debug(f"Stops data is empty, cannot resolve stop_code {stop_code}")
            return None
            
        sc_str = str(stop_code)
        if sc_str not in self.stops.index:
            _LOGGER.error(f"Stop code {stop_code} not found in the GTFS data.")
            return None
        
        stop_info = self.stops.loc[sc_str]
        if isinstance(stop_info, pandas.DataFrame):
            return str(stop_info.iloc[0]["stop_id"])
        return str(stop_info["stop_id"])

    def _get_service_ids(self, date: datetime.date) -> list[str]:
        """ Retrieve the service_ids for a given date, handling exceptions in calendar_dates.txt """
        curr_weekday = date.weekday()
        curr_date_int = int(date.strftime("%Y%m%d"))
        matching_service_ids = []

        # 1. Check calendar_dates.txt for explicit additions (exception_type=1)
        if not self.calendar_dates.empty:
            added_services = self.calendar_dates[
                (self.calendar_dates["date"] == curr_date_int) &
                (self.calendar_dates["exception_type"] == 1)
            ]
            matching_service_ids.extend(added_services["service_id"].tolist())

        # 2. Check calendar.txt for regular service
        weekday_map = {
            0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday",
            4: "friday", 5: "saturday", 6: "sunday"
        }
        weekday_str = weekday_map.get(curr_weekday)

        if weekday_str:
            regular_services = self.calendar[
                (self.calendar[weekday_str] == 1) &
                (self.calendar["end_date"] >= curr_date_int) &
                (self.calendar["start_date"] <= curr_date_int)
            ]
            
            # 3. Filter out regular services explicitly removed in calendar_dates.txt (exception_type=2).
            # Pre-compute the removed set once instead of filtering per service inside the loop.
            removed_ids: set[str] = set()
            if not self.calendar_dates.empty:
                removed_ids = set(
                    self.calendar_dates[
                        (self.calendar_dates["date"] == curr_date_int) &
                        (self.calendar_dates["exception_type"] == 2)
                    ]["service_id"]
                )

            for _, service_row in regular_services.iterrows():
                service_id = service_row["service_id"]
                if service_id not in removed_ids:
                    matching_service_ids.append(service_id)
            
        if not matching_service_ids:
            raise NoServiceFoundError(f"No service found for date {date}")
            
        return list(set(matching_service_ids))

    def _get_today_schedule(self, service_ids: list[str], stop_id: str) -> pandas.DataFrame:
        """Get the schedule for a given list of service IDs and stop ID."""
        try:
            stop_times_for_stop = self.stop_times.loc[[stop_id]]
        except KeyError:
            return pandas.DataFrame()

        results = stop_times_for_stop.merge(self.trips, how='left', on='trip_id', validate='many_to_one')

        # Try exact match first
        final_results = results[results['service_id'].isin(service_ids)].copy()

        # If no results, try fuzzy match (many agencies append extra info to service_id in trips.txt)
        if final_results.empty and not results.empty:
            base_service_ids = [str(sid).split('-')[0] for sid in service_ids]
            final_results = results[results['service_id'].astype(str).str.split('-').str[0].isin(base_service_ids)].copy()

        return final_results


    def _calculate_arrival_datetimes(self, schedule, date):
        """Calculate the arrival datetimes for the schedule."""
        
        def calculate_arrival(row):
            try:
                time_str = row["arrival_time"]
                h, m, s = map(int, time_str.split(':'))
                # GTFS allows times like 25:30:00 for trips that start on one day 
                # and end on the next. h can be >= 24.
                return datetime.datetime.combine(date, datetime.time.min) + datetime.timedelta(hours=h, minutes=m, seconds=s)
            except (ValueError, TypeError) as e:
                _LOGGER.error(f"Error calculating arrival datetime for {row.get('arrival_time')}: {e}")
                return None

        schedule['arrival_datetime'] = schedule.apply(calculate_arrival, axis=1)
        return schedule.dropna(subset=['arrival_datetime']).sort_values(by=['arrival_datetime'])

    def _get_stop_date_range(self, stop_id: str):
        """Find the oldest and newest dates in the schedule for a given stop_id."""
        # 1. Get all trip_ids for this stop
        stop_id_str = str(stop_id)
        stop_times_for_stop = self.stop_times.loc[self.stop_times.index == stop_id_str]
        if stop_times_for_stop.empty:
            return None, None
        
        trip_ids = stop_times_for_stop['trip_id'].unique()
        
        # 2. Get all service_ids for these trips
        service_ids = self.trips[self.trips['trip_id'].isin(trip_ids)]['service_id'].unique()
        
        # 3. Find date ranges in calendar.txt
        relevant_calendar = self.calendar[self.calendar['service_id'].isin(service_ids)]
        min_date = relevant_calendar['start_date'].min() if not relevant_calendar.empty else None
        max_date = relevant_calendar['end_date'].max() if not relevant_calendar.empty else None
        
        # 4. Find date ranges in calendar_dates.txt
        if not self.calendar_dates.empty:
            relevant_dates = self.calendar_dates[self.calendar_dates['service_id'].isin(service_ids)]
            if not relevant_dates.empty:
                min_exception = relevant_dates['date'].min()
                max_exception = relevant_dates['date'].max()
                
                if min_date is None or min_exception < min_date:
                    min_date = min_exception
                if max_date is None or max_exception > max_date:
                    max_date = max_exception
        
        return min_date, max_date

    def get_next_stop(self, stop_id: str, parm_datetime: datetime.datetime, stop_code: str | None = None, is_lookahead: bool = False, target_route: str | None = None, target_direction: str | None = None) -> Series | None:
        """Retrieve the next stop information, optionally looking ahead to the next day."""
        self.refresh()
        
        stop_id = str(stop_id)

        # If stop_code isn't provided, try to find it from stop_id (inefficient but good for logs)
        if stop_code is None and not self.stops.empty:
            matches = self.stops[self.stops['stop_id'] == stop_id]
            if not matches.empty:
                stop_code = matches.index[0]

        display_stop = f"{stop_code} (ID: {stop_id})" if stop_code else f"ID: {stop_id}"
        _LOGGER.info(f"Retrieving next stop for stop {display_stop} at {parm_datetime} (Method: {config.retrieval_method})")

        if config.retrieval_method != "live" and not self.stops.empty:
            try:
                today_service_ids = self._get_service_ids(parm_datetime.date())
                today_schedule = self._get_today_schedule(today_service_ids, stop_id)

                if today_schedule.empty:
                    raise NoServiceFoundError(f"Empty schedule for service_ids {today_service_ids}")

                today_schedule_with_arrivals = self._calculate_arrival_datetimes(today_schedule, parm_datetime.date())

                # Apply filters if provided
                if target_route:
                    today_schedule_with_arrivals = today_schedule_with_arrivals[today_schedule_with_arrivals['route_id'].astype(str) == str(target_route)]
                
                if target_direction:
                    today_schedule_with_arrivals = today_schedule_with_arrivals[today_schedule_with_arrivals['trip_headsign'].str.contains(target_direction, case=False, na=False)]

                next_stop = today_schedule_with_arrivals[today_schedule_with_arrivals['arrival_datetime'] > parm_datetime]

                if not next_stop.empty:
                    result = next_stop.iloc[0].copy()
                    result['retrieve_method'] = 'GTFS'
                    return result

                _LOGGER.info(f"No more buses matching filters in GTFS for stop {display_stop} after {parm_datetime}")

            except NoServiceFoundError as e:
                _LOGGER.info(f"GTFS check failed for {parm_datetime.date()}: {e}")
                if config.transit == "RTL":
                    _LOGGER.info("Trying live scraper fallback...")
        else:
            if config.transit == "RTL":
                _LOGGER.info("Skipping GTFS check as RETRIEVAL_METHOD is 'live'")
            else:
                _LOGGER.warning(f"RETRIEVAL_METHOD is 'live' but scraper is not available for {config.transit}. No data will be retrieved.")

        # Fallback to Hastus Scraper (RTL Only)
        if config.transit == "RTL":
            live_arrivals = self.scraper.get_schedule(stop_id, parm_datetime.date(), target_route=target_route, target_direction=target_direction)
            if live_arrivals:
                _LOGGER.info(f"Found {len(live_arrivals)} arrivals via live scraper for stop {display_stop}")
                for arrival_obj in live_arrivals:
                    if arrival_obj['arrival_datetime'] > parm_datetime:
                        # Return a Series-like object compatible with existing code
                        return Series({
                            'arrival_datetime': arrival_obj['arrival_datetime'],
                            'arrival_time': arrival_obj['arrival_time'],
                            'route_id': arrival_obj['route_id'],
                            'trip_headsign': arrival_obj['trip_headsign'],
                            'retrieve_method': 'live scraper'
                        })

        # --- Look-ahead logic ---
        if not is_lookahead:
            _LOGGER.info(f"No more buses for {parm_datetime.date()}. Checking next day...")
            # Create a datetime for the beginning of the next day
            next_day_start = datetime.datetime.combine(
                parm_datetime.date() + datetime.timedelta(days=1),
                datetime.time.min
            )
            return self.get_next_stop(stop_id, next_day_start, stop_code=stop_code, is_lookahead=True, target_route=target_route, target_direction=target_direction)

        min_d, max_d = self._get_stop_date_range(stop_id)
        _LOGGER.error(f"No service found for {parm_datetime.date()} (GTFS & Live). Global GTFS range: {self.min_date} to {self.max_date}. Stop {display_stop} range: {min_d} to {max_d}")
        return None