import os
from unittest.mock import patch

from transit_schedule.config import Config


def test_config_default():
    with patch.dict(os.environ, {}, clear=True):
        cfg = Config()
        assert cfg.transit == "RTL"
        assert cfg.mqtt_port == 1883


def test_config_custom():
    with patch.dict(os.environ, {"TRANSIT": "STM", "MQTT_PORT": "1234"}):
        cfg = Config()
        assert cfg.transit == "STM"
        assert cfg.mqtt_port == 1234


def test_config_invalid_port():
    with patch.dict(os.environ, {"MQTT_PORT": "invalid"}):
        cfg = Config()
        assert cfg.mqtt_port == 1883


def test_config_invalid_stop_code():
    with patch.dict(os.environ, {"STOP_CODE": "invalid"}):
        cfg = Config()
        assert cfg.stop_code is None


def test_config_to_dict():
    cfg = Config()
    d = cfg.to_dict()
    assert isinstance(d, dict)
    assert "transit" in d
    assert all(not k.startswith("_") for k in d.keys())


def test_config_poll_intervals_default():
    with patch.dict(os.environ, {}, clear=True):
        cfg = Config()
        assert cfg.max_poll_interval == 60
        assert cfg.idle_poll_interval == 300
        assert cfg.max_init_retries is None


def test_config_poll_intervals_custom():
    with patch.dict(os.environ, {"MAX_POLL_INTERVAL": "45", "IDLE_POLL_INTERVAL": "600", "MAX_INIT_RETRIES": "3"}):
        cfg = Config()
        assert cfg.max_poll_interval == 45
        assert cfg.idle_poll_interval == 600
        assert cfg.max_init_retries == 3


def test_config_poll_intervals_invalid():
    with patch.dict(
        os.environ, {"MAX_POLL_INTERVAL": "bad", "IDLE_POLL_INTERVAL": "invalid", "MAX_INIT_RETRIES": "nope"}
    ):
        cfg = Config()
        assert cfg.max_poll_interval == 60
        assert cfg.idle_poll_interval == 300
        assert cfg.max_init_retries is None
