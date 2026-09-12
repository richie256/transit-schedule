import datetime
import os
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from transit_schedule.util import get_modification_date, is_file_expired, settings_from_file


@pytest.fixture
def test_file():
    test_file = "test_settings.json"
    yield test_file
    if os.path.exists(test_file):
        os.remove(test_file)


@patch("transit_schedule.util.os.path.getmtime")
def test_get_modification_date(mock_getmtime):
    mock_getmtime.return_value = 1678886400  # March 15, 2023 12:00:00 PM
    expected_date = datetime.datetime.fromtimestamp(1678886400)
    assert get_modification_date("any_file.txt") == expected_date


@freeze_time("2023-03-15 13:00:00")
def test_is_file_expired(mocker):
    mocker.patch("transit_schedule.util.os.path.isfile", return_value=True)
    mocker.patch("transit_schedule.util.os.path.getsize", return_value=1024)

    # Case 1: File is not expired (48h old — within the 72h window)
    mocker.patch("transit_schedule.util.get_modification_date", return_value=datetime.datetime(2023, 3, 13, 12, 0, 0))
    assert not is_file_expired("any_file.txt")

    # Case 2: File is expired (96h old — beyond the 72h window)
    mocker.patch("transit_schedule.util.get_modification_date", return_value=datetime.datetime(2023, 3, 11, 12, 0, 0))
    assert is_file_expired("any_file.txt")

    # Case 3: File does not exist
    mocker.patch("transit_schedule.util.os.path.isfile", return_value=False)
    assert is_file_expired("any_file.txt")


def test_settings_from_file_read_write(test_file):
    # Test writing to a file
    config_to_write = {"key": "value", "number": 123}
    assert settings_from_file(test_file, config_to_write)

    # Test reading from the file
    read_config = settings_from_file(test_file)
    assert read_config == config_to_write


def test_settings_from_file_read_nonexistent():
    # Test reading from a non-existent file
    read_config = settings_from_file("non_existent_file.json")
    assert read_config == {}


def test_settings_from_file_write_error(mocker):
    # Test writing to a read-only or inaccessible location
    with patch("builtins.open", side_effect=OSError("Permission denied")):
        assert settings_from_file("readonly.json", {"key": "value"}) is False


def test_settings_from_file_read_error(test_file):
    # Test reading a corrupted JSON file
    with open(test_file, "w") as f:
        f.write("corrupted { json")

    assert settings_from_file(test_file) is False


def test_is_file_expired_zero_size(mocker):
    mocker.patch("transit_schedule.util.os.path.isfile", return_value=True)
    mocker.patch("transit_schedule.util.os.path.getsize", return_value=0)
    assert is_file_expired("empty_file.txt") is True
