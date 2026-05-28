"""Unit tests for network_timings.py tool."""

import json
import tempfile
from pathlib import Path

import pytest

from webwright.tools.network_timings import NetworkTimingsCapture


def test_network_timings_initialization():
    """Test NetworkTimingsCapture initialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        assert capture.workspace_dir == Path(tmpdir)
        assert capture.timings == []
        assert not capture.is_capturing


def test_start_and_stop_capture():
    """Test starting and stopping capture."""
    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        assert not capture.is_capturing

        capture.start_capture()
        assert capture.is_capturing
        assert capture.start_time is not None

        capture.stop_capture()
        assert not capture.is_capturing


def test_log_request():
    """Test logging requests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        capture.start_capture()

        capture.log_request("https://example.com", method="GET", request_size=100)

        assert len(capture.timings) == 1
        timing = capture.timings[0]
        assert timing["type"] == "request"
        assert timing["url"] == "https://example.com"
        assert timing["method"] == "GET"
        assert timing["request_size"] == 100


def test_log_response():
    """Test logging responses."""
    import time

    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        capture.start_capture()

        start_time = time.time()
        capture.log_request("https://example.com", method="GET")
        time.sleep(0.01)
        end_time = time.time()

        capture.log_response("https://example.com", status=200, end_time=end_time, response_size=1024)

        assert len(capture.timings) == 1
        timing = capture.timings[0]
        assert timing["status"] == 200
        assert timing["response_size"] == 1024
        assert "duration_ms" in timing
        assert timing["duration_ms"] >= 10  # At least 10ms from sleep


def test_get_summary_empty():
    """Test summary with no timings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        summary = capture.get_summary()

        assert summary["total_requests"] == 0
        assert summary["total_time_ms"] == 0
        assert summary["average_request_time_ms"] == 0


def test_get_summary_with_timings():
    """Test summary calculation with captured timings."""
    import time

    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        capture.start_capture()

        # Log 3 requests
        for i in range(3):
            start = time.time()
            capture.log_request(f"https://example.com/api/{i}", method="GET")
            time.sleep(0.01)
            end = time.time()
            capture.log_response(f"https://example.com/api/{i}", status=200, end_time=end, response_size=500)

        summary = capture.get_summary()
        assert summary["total_requests"] == 3
        assert summary["total_time_ms"] >= 30  # At least 30ms total
        assert summary["average_request_time_ms"] > 0
        assert summary["min_request_time_ms"] > 0
        assert summary["max_request_time_ms"] > 0
        assert summary["total_size_bytes"] == 1500  # 3 * 500


def test_export_to_file():
    """Test exporting timings to JSON file."""
    import time

    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        capture.start_capture()

        capture.log_request("https://example.com", method="GET")
        time.sleep(0.01)
        capture.log_response("https://example.com", status=200, response_size=1024)

        output_file = capture.export_to_file("test_timings.json")
        assert Path(output_file).exists()

        with open(output_file) as f:
            data = json.load(f)

        assert "metadata" in data
        assert "summary" in data
        assert "timings" in data
        assert len(data["timings"]) == 1
        assert data["summary"]["total_requests"] == 1


def test_get_timings():
    """Test retrieving captured timings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        capture.start_capture()

        capture.log_request("https://example.com/1", method="GET")
        capture.log_request("https://example.com/2", method="POST")

        timings = capture.get_timings()
        assert len(timings) == 2
        assert timings[0]["url"] == "https://example.com/1"
        assert timings[1]["url"] == "https://example.com/2"


def test_multiple_requests_same_url():
    """Test logging multiple requests to the same URL."""
    import time

    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)
        capture.start_capture()

        url = "https://example.com/api"
        for i in range(3):
            capture.log_request(url, method="GET")
            time.sleep(0.01)

        assert len(capture.timings) == 3
        for timing in capture.timings:
            assert timing["url"] == url


def test_capture_not_logging_when_disabled():
    """Test that requests are not logged when capture is stopped."""
    with tempfile.TemporaryDirectory() as tmpdir:
        capture = NetworkTimingsCapture(tmpdir)

        capture.log_request("https://example.com", method="GET")
        assert len(capture.timings) == 0

        capture.start_capture()
        capture.log_request("https://example.com", method="GET")
        assert len(capture.timings) == 1

        capture.stop_capture()
        capture.log_request("https://example.com", method="GET")
        assert len(capture.timings) == 1  # Still 1, not logged


def test_export_creates_parent_directories():
    """Test that export creates parent directories if needed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = Path(tmpdir) / "subdir" / "nested"
        capture = NetworkTimingsCapture(str(subdir))
        capture.start_capture()

        capture.log_request("https://example.com", method="GET")

        output_file = capture.export_to_file("timings.json")
        assert Path(output_file).exists()
        assert Path(output_file).parent == subdir
