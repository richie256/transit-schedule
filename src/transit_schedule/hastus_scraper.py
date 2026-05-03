import datetime
import json
import logging
import os
import re
import ssl
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util.retry import Retry

from transit_schedule.config import config
from transit_schedule.const import TARGET_DIRECTION, TARGET_ROUTE, TRANSIT

_LOGGER = logging.getLogger("transit-schedule")
_LOGGER.propagate = False # Prevent double logging if parent has a handler

class HostnameIgnoreAdapter(HTTPAdapter):
    """
    Custom adapter for madprep_i.rtl-longueuil.qc.ca whose hostname contains
    an underscore, which Python's ssl module rejects during SNI hostname matching.
    Certificate chain verification (CERT_REQUIRED) is preserved; only the hostname
    match is disabled.
    """
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop('max_retries', None)
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False  # hostname contains underscore — not valid per RFC 952
        ctx.verify_mode = ssl.CERT_REQUIRED  # still verify the certificate chain
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
            **pool_kwargs
        )

class HastusScraper:
    BASE_URL = "https://madprep_i.rtl-longueuil.qc.ca/madOper.php"
    CACHE_FILE = "data/hastus_cache.json"
    CACHE_VERSION = "2"
    MAX_CACHE_SIZE = 500

    def __init__(self):
        self.buildtime = None
        self.stop_mappings = {} # stop_code (str) -> list of (feed_id:stop_id)
        self._stop_id_to_code: dict[str, str] = {}  # stop_id (str) -> stop_code; reverse index for O(1) lookup
        self._mappings_fetched = False
        # cache: (stop_id, pattern_id, week_start_date) -> { 'weekday': [...], 'samedi': [...], 'dimanche': [...] }
        self.schedule_cache = {}
        
        # Initialize session with custom adapter and retry logic
        self.session = requests.Session()
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HostnameIgnoreAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
        if config.force_cache_refresh:
            _LOGGER.info("FORCE_CACHE_REFRESH is enabled. Clearing existing cache file.")
            if os.path.exists(self.CACHE_FILE):
                os.remove(self.CACHE_FILE)

        self._load_cache()
        self._initialize()

    def _initialize(self):
        """Fetch current buildtime and basic metadata."""
        try:
            params = {"q": "routers", "s": "RTL", "api": "0"}
            response = self.session.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.buildtime = data.get("buildtime")
            _LOGGER.info(f"HastusScraper initialized with buildtime: {self.buildtime}")
        except requests.exceptions.RequestException as e:
            _LOGGER.error(f"Network error during HastusScraper initialization: {e}")
        except ValueError as e:
            _LOGGER.error(f"JSON parsing error during HastusScraper initialization: {e}")
        except Exception as e:
            _LOGGER.error(f"Unexpected error during HastusScraper initialization: {e}")

    def _load_cache(self):
        """Load cache from disk."""
        if not os.path.exists(self.CACHE_FILE):
            return
        
        try:
            with open(self.CACHE_FILE) as f:
                content = json.load(f)
            
            # Check version to handle logic changes/poisoned cache
            if not isinstance(content, dict) or content.get("version") != self.CACHE_VERSION:
                _LOGGER.info(f"Cache version mismatch or old format. Expected version {self.CACHE_VERSION}. Clearing old cache.")
                return

            raw_cache = content.get("data", {})
            self.schedule_cache = {}
            for key_str, data in raw_cache.items():
                # Key format: "stop|pattern|week_start"
                parts = key_str.split('|')
                if len(parts) != 3:
                    continue
                stop, pattern, week_start_str = parts
                week_start = datetime.date.fromisoformat(week_start_str)
                key = (stop, pattern, week_start)
                
                # Convert time strings back to datetime.time
                weekly_data = {}
                for cat, times in data.items():
                    weekly_data[cat] = [datetime.time.fromisoformat(t) for t in times]
                
                self.schedule_cache[key] = weekly_data
            _LOGGER.info(f"Loaded {len(self.schedule_cache)} entries from disk cache (v{self.CACHE_VERSION}).")
        except Exception as e:
            _LOGGER.error(f"Failed to load cache from disk: {e}")

    def _save_cache(self):
        """Save cache to disk."""
        try:
            os.makedirs(os.path.dirname(self.CACHE_FILE), exist_ok=True)
            serializable_data_map = {}
            for (stop, pattern, week_start), data in self.schedule_cache.items():
                key_str = f"{stop}|{pattern}|{week_start.isoformat()}"
                serializable_weekly = {}
                for cat, times in data.items():
                    serializable_weekly[cat] = [t.isoformat() for t in times]
                serializable_data_map[key_str] = serializable_weekly
            
            full_cache = {
                "version": self.CACHE_VERSION,
                "data": serializable_data_map
            }
                
            with open(self.CACHE_FILE, 'w') as f:
                json.dump(full_cache, f, indent=2)
            _LOGGER.info(f"Saved cache (v{self.CACHE_VERSION}) to disk.")
        except Exception as e:
            _LOGGER.error(f"Failed to save cache to disk: {e}")

    def fetch_stop_mappings(self):
        """Fetch all stop mappings from the server."""
        try:
            params = {"q": "stops", "s": "RTL", "web": ""}
            response = self.session.get(self.BASE_URL, params=params, timeout=20)
            response.raise_for_status()
            content = response.text
            self.stop_mappings = {}
            self._stop_id_to_code = {}
            for entry in content.split(';'):
                if not entry:
                    continue
                parts = entry.split(',')
                if len(parts) >= 6:
                    internal_id = parts[0]
                    stop_code = parts[5]
                    if stop_code not in self.stop_mappings:
                        self.stop_mappings[stop_code] = []
                    if internal_id not in self.stop_mappings[stop_code]:
                        self.stop_mappings[stop_code].append(internal_id)
                    # Build reverse index: "feed_id:stop_id" -> extract stop_id part
                    id_parts = internal_id.split(':', 1)
                    if len(id_parts) == 2:
                        self._stop_id_to_code[id_parts[1]] = stop_code
            self._mappings_fetched = True
            _LOGGER.info(f"Fetched {len(self.stop_mappings)} stop mappings.")
        except requests.exceptions.RequestException as e:
            _LOGGER.error(f"Network error while fetching stop mappings: {e}")
        except Exception as e:
            _LOGGER.error(f"Unexpected error while fetching stop mappings: {e}")

    def get_stop_code_from_id(self, stop_id: int) -> str | None:
        """Find the public stop code for a given internal stop_id."""
        if not self._mappings_fetched:
            self.fetch_stop_mappings()
        return self._stop_id_to_code.get(str(stop_id))

    def get_stop_patterns(self, stop_code: str, stop_id: int | None = None) -> list[dict]:
        """Fetch available patterns/routes for a given stop code."""
        if not self._mappings_fetched:
            self.fetch_stop_mappings()
        
        internal_ids = self.stop_mappings.get(stop_code, [])
        if not internal_ids:
            if stop_id:
                # Try common feed IDs
                internal_ids = [f"15:{stop_id}", f"14:{stop_id}", f"1:{stop_id}"]
                _LOGGER.info(f"Stop code {stop_code} not in mapping, trying fallback IDs: {internal_ids}")
            else:
                # Fallback to guessing feed 15 if not in mapping
                internal_ids = [f"15:{stop_code}"]
            
        patterns = []
        for p_id in internal_ids:
            params = {
                "q": "stops_patterns",
                "p": p_id,
                "s": "RTL",
                "web": ""
            }
                
            try:
                response = self.session.get(self.BASE_URL, params=params, timeout=15)
                new_patterns = self._parse_patterns_html(response.text)
                if not new_patterns and response.text:
                    _LOGGER.debug(f"No patterns found in response for {p_id}. Response snippet: {response.text[:200]}")
                patterns.extend(new_patterns)
            except Exception as e:
                _LOGGER.error(f"Failed to fetch patterns for {p_id}: {e}")
                
        unique_patterns = {p['pattern']: p for p in patterns}.values()
        return list(unique_patterns)

    def _parse_patterns_html(self, html: str) -> list[dict]:
        """Parse the HTML from stops_patterns to extract urlHoraireArret parameters."""
        patterns = []
        # Support both single and double quotes, and optional spaces
        pattern = re.compile(r"urlHoraireArret\s*\((.*?)\)\s*;")
        matches = pattern.findall(html)
        for match in matches:
            # More robust argument splitting
            args = [arg.strip().strip("'\"") for arg in match.split(',')]
            if len(args) >= 5:
                patterns.append({
                    "stop": args[0],
                    "pattern": args[1],
                    "code": args[2],
                    "desc": args[3],
                    "ligne": args[4],
                    "leJour": args[5] if len(args) > 5 else None
                })
        return patterns

    def _get_now(self):
        """Helper to get current datetime, easily mockable for tests."""
        return datetime.datetime.now()

    def get_schedule_by_params(self, params: dict, date: datetime.date) -> list[datetime.datetime]:
        """Fetch schedule using parameters derived from urlHoraireArret with caching."""
        if not self.buildtime:
            self._initialize()
            
        def ensure_cached(d):
            week_start = d - datetime.timedelta(days=d.weekday())
            cache_key = (params['stop'], params['pattern'], week_start)
            if cache_key not in self.schedule_cache:
                self._fetch_and_cache(params, d, cache_key)
            return cache_key

        current_key = ensure_cached(date)
        
        # If searching late at night, ensure next day is also cached.
        # This handles midnight transitions smoothly.
        if self._get_now().hour >= 20:
            next_day = date + datetime.timedelta(days=1)
            ensure_cached(next_day)

        _LOGGER.info(f"CACHE HIT: Using cached schedule for {params['ligne']} on {date}")
        return self._get_times_from_cache(self.schedule_cache[current_key], date)

    def _fetch_and_cache(self, params, date, cache_key):
        """Fetch data from network and populate cache."""
        landing_params = {
            "q": "stops_stoptimes",
            "p": params['stop'],
            "s": "RTL",
            "web": "",
            "pp": params['pattern'],
            "l": params['ligne']
        }
        
        try:
            _LOGGER.info(f"Discovering schedule links for {params['ligne']} at stop {params['stop']}")
            landing_res = self.session.get(self.BASE_URL, params=landing_params, timeout=15)
            soup = BeautifulSoup(landing_res.text, 'html.parser')
            links = soup.find_all('a', href=re.compile(r'q=stops_stoptimes'))
            
            combined_weekly_data = {'semaine': [], 'samedi': [], 'dimanche': []}
            week_start = date - datetime.timedelta(days=date.weekday())
            week_end = week_start + datetime.timedelta(days=6)
            
            found_any = False
            _LOGGER.info(f"Found {len(links)} candidate links on landing page.")
            
            for link in links:
                href = link.get('href')
                text = link.get_text(strip=True).lower()
                
                # Extract full URL
                if href.startswith('/'):
                    url = f"https://madprep_i.rtl-longueuil.qc.ca{href}"
                elif href.startswith('madOper.php'):
                    url = f"https://madprep_i.rtl-longueuil.qc.ca/{href}"
                else:
                    url = href if "://" in href else f"https://madprep_i.rtl-longueuil.qc.ca/{href}"

                # Link Classification Logic
                link_date = None
                
                # 1. Trust 'j' parameter (Jour: 1-5 Weekday, 6 Sat, 7 Sun)
                j_match = re.search(r'[&?]j=(\d)', url)
                if j_match:
                    j_val = int(j_match.group(1))
                    if j_val <= 5:
                        link_date = week_start
                    elif j_val == 6:
                        link_date = week_start + datetime.timedelta(days=5)
                    elif j_val == 7:
                        link_date = week_start + datetime.timedelta(days=6)
                    _LOGGER.debug(f"Link categorization from j={j_val}: {link_date}")

                # 2. Trust timestamp 't' if 'j' is missing
                if not link_date:
                    t_match = re.search(r'[&?]t=(\d+)', url)
                    if t_match:
                        val = t_match.group(1)
                        try:
                            if len(val) == 8:
                                link_date = datetime.datetime.strptime(val, '%Y%m%d').date()
                            elif len(val) >= 10:
                                # Import inline or use global
                                import zoneinfo
                                mtl_tz = zoneinfo.ZoneInfo("America/Montreal")
                                link_date = datetime.datetime.fromtimestamp(int(val[:10]), tz=mtl_tz).date()
                        except (ValueError, OSError):
                            pass
                        _LOGGER.debug(f"Link categorization from t={val}: {link_date}")

                # 3. Fallback to text (Strictly avoiding "lundi" matching range links)
                if not link_date:
                    # For range links like "du lundi... au dimanche", prioritize "semaine"
                    if ('semaine' in text or 'lundi' in text) and 'au' in text:
                        link_date = week_start
                    elif 'samedi' in text:
                        link_date = week_start + datetime.timedelta(days=5)
                    elif 'dimanche' in text:
                        link_date = week_start + datetime.timedelta(days=6)
                    elif 'semaine' in text or 'lundi' in text:
                        link_date = week_start
                    else:
                        link_date = date
                    _LOGGER.debug(f"Link categorization from text '{text}': {link_date}")

                # Strict Filter: Only follow links for the current week
                if link_date and (link_date < week_start or link_date > week_end):
                    _LOGGER.info(f"Skipping link outside target week: {link_date} (Link: '{text}')")
                    continue

                _LOGGER.info(f"Fetching schedule: {text} | Inferred Date: {link_date}")
                response = self.session.get(url, timeout=15)
                found_any = True
                
                try:
                    json_data = response.json()
                    if isinstance(json_data, dict) and 'data' in json_data:
                        period_data = self._parse_json_weekly_schedule(json_data, params['stop'], params['pattern'], link_date)
                        for cat in combined_weekly_data:
                            combined_weekly_data[cat].extend(period_data[cat])
                    else:
                        _LOGGER.warning(f"Response from {url[:50]}... was not valid JSON data")
                except ValueError:
                    _LOGGER.warning(f"Failed to parse JSON for {text}")

            if not found_any:
                _LOGGER.warning("No valid schedule links found.")

            # Deduplicate and sort
            for cat in combined_weekly_data:
                combined_weekly_data[cat] = sorted(set(combined_weekly_data[cat]))

            if len(self.schedule_cache) >= self.MAX_CACHE_SIZE:
                evicted = next(iter(self.schedule_cache))
                del self.schedule_cache[evicted]
                _LOGGER.debug(f"Cache at {self.MAX_CACHE_SIZE} entries, evicted oldest entry.")
            self.schedule_cache[cache_key] = combined_weekly_data
            self._save_cache()
            
        except Exception as e:
            _LOGGER.error(f"Scraping failed: {e}")
            import traceback
            _LOGGER.error(traceback.format_exc())

    def _parse_json_weekly_schedule(self, json_data: dict, stop_id: str, pattern_id: str, target_date: datetime.date, is_day_specific: bool = False) -> dict[str, list[datetime.time]]:
        """Parse the new JSON format into weekly categories with strict trip-type detection."""
        weekly_data = {'semaine': [], 'samedi': [], 'dimanche': []}
        counts = {'semaine': 0, 'samedi': 0, 'dimanche': 0}

        data_entries = json_data.get('data', [])
        if not data_entries:
            _LOGGER.warning(f"No data entries in JSON response for stop {stop_id}, pattern {pattern_id}")
            return weekly_data
            
        # EXTRA TROUBLESHOOTING: Dump the first entry to see the actual structure from RTL
        _LOGGER.info(f"Parsing {len(data_entries)} entries for target date {target_date} (Specific: {is_day_specific}). SAMPLE: {data_entries[0]}")

        # Determine the "Intended Category" of the link we followed
        target_wd = target_date.weekday()
        intended_cat = 'semaine' if target_wd < 5 else ('samedi' if target_wd == 5 else 'dimanche')

        for entry in data_entries:
            # Check stop match
            if entry.get('stopid') != stop_id:
                continue
            
            # Check pattern match
            entry_id = entry.get('id', '')
            if pattern_id:
                wrapped_id = f":{entry_id}:"
                wrapped_pattern = f":{pattern_id}:"
                if wrapped_pattern not in wrapped_id:
                    if pattern_id != entry_id and not entry_id.startswith(f"{pattern_id}:") and not entry_id.endswith(f":{pattern_id}"):
                        continue

            arrival_seconds = entry.get('scheduledarrival')
            if arrival_seconds is None:
                continue

            try:
                h, m = divmod(arrival_seconds // 60, 60)
                t = datetime.time(h % 24, m)
            except (ValueError, OverflowError):
                continue

            # IDENTIFY THE DAY TYPE
            # 1. Check trip ID for explicit markers (Most reliable for RTL)
            trip_id = entry.get('id_trip', '')
            entry_cat = None
            if '_SE_' in trip_id:
                entry_cat = 'semaine'
            elif '_SA_' in trip_id:
                entry_cat = 'samedi'
            elif '_DI_' in trip_id:
                entry_cat = 'dimanche'
            
            # 2. Use date field if no trip marker found
            if not entry_cat:
                date_str = entry.get('date')
                if date_str:
                    try:
                        base_date = date_str.split('T')[0]
                        entry_date = datetime.date.fromisoformat(base_date)
                        # If time >= 24:00, it's actually the next day
                        if h >= 24:
                            entry_date += datetime.timedelta(days=1)
                        wd = entry_date.weekday()
                        entry_cat = 'semaine' if wd < 5 else ('samedi' if wd == 5 else 'dimanche')
                    except ValueError:
                        pass

            # 3. Fallback to intended category (Trust the link)
            if not entry_cat:
                entry_cat = intended_cat

            # FINAL PROTECTION: If we followed a DAY-SPECIFIC link, we strictly confine
            # the data to that category, ignoring conflicting labels.
            if is_day_specific:
                if entry_cat != intended_cat:
                    _LOGGER.debug(f"Overriding entry cat '{entry_cat}' -> '{intended_cat}' (Strict link intent)")
                entry_cat = intended_cat

            weekly_data[entry_cat].append(t)
            counts[entry_cat] += 1

        _LOGGER.info(f"Link Classification Summary ({target_date}): {counts}")
        
        # Deduplicate and sort
        for cat in weekly_data:
            weekly_data[cat] = sorted(set(weekly_data[cat]))

        return weekly_data

    def _get_times_from_cache(self, weekly_data: dict[str, list[datetime.time]], date: datetime.date) -> list[datetime.datetime]:
        """Helper to convert cached time list to datetime list for a specific date, including early morning of next day."""
        
        def get_category_times(d: datetime.date):
            wd = d.weekday()
            if wd < 5:
                return weekly_data.get('semaine', [])
            elif wd == 5:
                return weekly_data.get('samedi', [])
            else:
                return weekly_data.get('dimanche', [])

        current_day_times = get_category_times(date)
        next_day = date + datetime.timedelta(days=1)
        next_day_times = get_category_times(next_day)
            
        result = []
        for t in current_day_times:
            result.append(datetime.datetime.combine(date, t))
        
        # Also include very early morning of next day (e.g. 00:00 to 04:00)
        # to handle late night bus searches correctly.
        for t in next_day_times:
            if t.hour < 4:
                result.append(datetime.datetime.combine(next_day, t))
            
        return sorted(result)

    def _parse_html_weekly_schedule(self, html: str) -> dict[str, list[datetime.time]]:
        """Parse the weekly HTML table into three categories: semaine, samedi, dimanche."""
        soup = BeautifulSoup(html, 'html.parser')
        weekly_data = {'semaine': [], 'samedi': [], 'dimanche': []}
        
        # Search for tables that contain category markers in their bold headers
        tables = soup.find_all('table')
        
        for table in tables:
            header_row = table.find('tr')
            if not header_row:
                continue
            
            # Categories are often in <b>Semaine</b>, <b>Samedi</b>, etc.
            category_cells = table.find_all('b')
            category = None
            for cell in category_cells:
                text = cell.get_text(strip=True).lower()
                if 'semaine' in text:
                    category = 'semaine'
                    break
                elif 'samedi' in text:
                    category = 'samedi'
                    break
                elif 'dimanche' in text:
                    category = 'dimanche'
                    break
            
            if category:
                # We found a schedule table for a category
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    for cell in cells:
                        time_str = cell.get_text(strip=True)
                        # Match HH:MM format
                        match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
                        if match:
                            h, m = map(int, match.groups())
                            t = datetime.time(h % 24, m)
                            
                            # Handle times past midnight (e.g. 25:15)
                            # These belong to the NEXT day's category.
                            if h >= 24:
                                if category == 'semaine':
                                    # If it's a weekday night, it might be Friday night -> Saturday
                                    # This is a bit tricky with the HTML format which is already aggregated.
                                    # We'll assume the most conservative approach: 
                                    # add it to both the current and the next if it crosses a boundary.
                                    weekly_data['semaine'].append(t)
                                    weekly_data['samedi'].append(t)
                                elif category == 'samedi':
                                    weekly_data['dimanche'].append(t)
                                elif category == 'dimanche':
                                    weekly_data['semaine'].append(t) # Sunday night -> Monday
                            else:
                                weekly_data[category].append(t)
        
        # Deduplicate and sort
        for cat in weekly_data:
            weekly_data[cat] = sorted(set(weekly_data[cat]))

        return weekly_data

    def get_schedule(self, stop_id: int, date: datetime.date, feed_id: int = 15, target_route: str | None = None, target_direction: str | None = None) -> list[dict[str, Any]]:
        """Smart fallback: discovers patterns for the stop and fetches all schedules."""
        if TRANSIT != "RTL":
            _LOGGER.debug(f"Live scraper not available for {TRANSIT}")
            return []

        stop_code = self.get_stop_code_from_id(stop_id)
        if not stop_code:
            _LOGGER.error(f"Could not map internal stop_id {stop_id} to a stop code.")
            return []

        _LOGGER.info(f"Fallback: Discovered stop code {stop_code} for ID {stop_id}")
        patterns = self.get_stop_patterns(stop_code, stop_id=stop_id)

        if not patterns:
            _LOGGER.warning(f"No patterns found for stop code {stop_code}")
            return []

        all_arrivals = []
        # Use provided target_route/direction or fall back to global constants
        route_filter = target_route if target_route is not None else TARGET_ROUTE
        direction_filter = target_direction if target_direction is not None else TARGET_DIRECTION

        # Filter for target direction if requested
        for p in patterns:
            # Extract route number from ligne string (e.g. " 44 Direction Terminus Panama" -> "44")
            route_match = re.search(r'(\d+)', p['ligne'])
            route_id = route_match.group(1) if route_match else "0"

            if route_filter and str(route_filter) != route_id:
                _LOGGER.debug(f"Skipping pattern {p['ligne']} (route {route_id} != {route_filter})")
                continue

            if direction_filter and direction_filter not in p['ligne']:
                _LOGGER.debug(f"Skipping pattern {p['ligne']} (not {direction_filter})")
                continue
            arrivals = self.get_schedule_by_params(p, date)
            
            for a_dt in arrivals:
                all_arrivals.append({
                    'arrival_datetime': a_dt,
                    'arrival_time': a_dt.strftime("%H:%M:%S"),
                    'route_id': route_id,
                    'trip_headsign': p['ligne'].strip()
                })
            
        # Sort by arrival time
        all_arrivals.sort(key=lambda x: x['arrival_datetime'])
        return all_arrivals
