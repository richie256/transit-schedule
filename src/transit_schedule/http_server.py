import datetime
import logging

from flask import Flask, jsonify, request

import transit_schedule.data_parser as data_parser
from transit_schedule.const import _LOGGER


def create_app(transit_data=None):
    if transit_data is None:
        try:
            transit_data = data_parser.ParseTransitData()
        except Exception as e:
            _LOGGER.exception(e)
            # In case of initialization error, we still want to be able to start the server
            # to serve health checks, even if other endpoints fail.
            transit_data = None
    app = Flask(__name__)

    if __name__ != "__main__":
        gunicorn_logger = logging.getLogger("gunicorn.error")
        _LOGGER.handlers = gunicorn_logger.handlers
        _LOGGER.setLevel(gunicorn_logger.level)

    @app.before_request
    def log_request_info():
        if request.path == "/health":
            return
        _LOGGER.info(f"Received request: {request.method} {request.path} from {request.remote_addr}")

    @app.route("/transit-schedule/nextstop/<int:stop_code>", methods=["GET"])
    def get_next_stop(stop_code: int):
        if transit_data is None:
            return jsonify({"error": "Transit data not initialized"}), 500
        if stop_code <= 0:
            return jsonify({"error": "Stop code must be a positive integer"}), 400
        stop_id = transit_data.get_stop_id(stop_code)
        if stop_id is None:
            return jsonify({"error": "Stop code not found"}), 404
        current_datetime = datetime.datetime.now().replace(microsecond=0)
        next_stop_row = transit_data.get_next_stop(stop_id, current_datetime, stop_code=stop_code)

        if next_stop_row is not None:
            difference = next_stop_row.arrival_datetime - current_datetime
            nbr_minutes, nbr_seconds = divmod(difference.total_seconds(), 60)

            result = {
                "nextstop_nbrmins": int(nbr_minutes),
                "nextstop_nbrsecs": int(nbr_seconds),
                "route_id": str(next_stop_row.route_id),
                "arrival_time": str(next_stop_row.arrival_time),
                "trip_headsign": str(next_stop_row.trip_headsign),
                "current_time": str(current_datetime.time()),
            }
            return jsonify(result)
        return jsonify({"error": "No more buses for today"})

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "ok"}), 200

    return app


def start_http_server():
    app = create_app()
    app.run(host="0.0.0.0", port=80)
