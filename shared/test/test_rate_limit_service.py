#!/usr/bin/env python3
"""
Unit tests for the rate limit service sliding window and config CRUD.

The sliding window gates spend, so a silent failure here means unbounded LLM cost.
"""

import asyncio
import os
import sys
import unittest
from collections import deque
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils import rate_limit_service
from shared.utils.rate_limit_service import (
    _cleanup_old_timestamps,
    _record_request,
    _request_count_last_minute,
    _request_count_last_minute_sync,
    _request_timestamps,
)


class TestCleanupOldTimestamps(unittest.TestCase):

    def test_drops_entries_older_than_window(self):
        with patch("shared.utils.rate_limit_service.time.monotonic", return_value=1000.0):
            ts = deque([930.0, 950.0, 980.0])
            _cleanup_old_timestamps(ts, 60)
        # Only 930.0 is older than the 940.0 cutoff.
        self.assertEqual(list(ts), [950.0, 980.0])

    def test_keeps_all_entries_inside_window(self):
        with patch("shared.utils.rate_limit_service.time.monotonic", return_value=1000.0):
            ts = deque([950.0, 999.0])
            _cleanup_old_timestamps(ts, 60)
        self.assertEqual(list(ts), [950.0, 999.0])

    def test_entry_exactly_at_cutoff_is_kept(self):
        """Cutoff comparison is strict (<), so a timestamp on the boundary survives."""
        with patch("shared.utils.rate_limit_service.time.monotonic", return_value=1000.0):
            ts = deque([940.0])
            _cleanup_old_timestamps(ts, 60)
        self.assertEqual(list(ts), [940.0])

    def test_empty_deque_is_safe(self):
        ts = deque()
        with patch("shared.utils.rate_limit_service.time.monotonic", return_value=1000.0):
            _cleanup_old_timestamps(ts, 60)
        self.assertEqual(list(ts), [])

    def test_all_entries_expired_empties_deque(self):
        with patch("shared.utils.rate_limit_service.time.monotonic", return_value=1000.0):
            ts = deque([100.0, 200.0])
            _cleanup_old_timestamps(ts, 60)
        self.assertEqual(list(ts), [])


class TestRequestCountLastMinuteSync(unittest.TestCase):
    """Key-matching logic: a user-scoped count must aggregate that user's agents only."""

    def setUp(self):
        _request_timestamps.clear()
        self.addCleanup(_request_timestamps.clear)
        patcher = patch("shared.utils.rate_limit_service.time.monotonic", return_value=1000.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_zero_when_no_requests_recorded(self):
        self.assertEqual(_request_count_last_minute_sync("alice"), 0)

    def test_counts_bare_user_key(self):
        _request_timestamps["user:alice"] = deque([990.0, 995.0])
        self.assertEqual(_request_count_last_minute_sync("alice"), 2)

    def test_user_scope_aggregates_across_that_users_agents(self):
        _request_timestamps["user:alice"] = deque([990.0])
        _request_timestamps["user:alice:agent:writer"] = deque([991.0, 992.0])
        _request_timestamps["user:alice:agent:coder"] = deque([993.0])
        self.assertEqual(_request_count_last_minute_sync("alice"), 4)

    def test_agent_scope_counts_only_that_agent(self):
        _request_timestamps["user:alice"] = deque([990.0])
        _request_timestamps["user:alice:agent:writer"] = deque([991.0, 992.0])
        _request_timestamps["user:alice:agent:coder"] = deque([993.0])
        self.assertEqual(_request_count_last_minute_sync("alice", "writer"), 2)

    def test_other_users_are_never_counted(self):
        _request_timestamps["user:alice"] = deque([990.0])
        _request_timestamps["user:bob"] = deque([991.0, 992.0])
        self.assertEqual(_request_count_last_minute_sync("alice"), 1)

    def test_user_id_prefix_collision_is_not_counted(self):
        """'alice2' must not be folded into 'alice' by prefix matching."""
        _request_timestamps["user:alice"] = deque([990.0])
        _request_timestamps["user:alice2"] = deque([991.0, 992.0])
        _request_timestamps["user:alice2:agent:writer"] = deque([993.0])
        self.assertEqual(_request_count_last_minute_sync("alice"), 1)

    def test_expired_timestamps_are_excluded(self):
        _request_timestamps["user:alice"] = deque([100.0, 200.0, 990.0])
        self.assertEqual(_request_count_last_minute_sync("alice"), 1)

    def test_unknown_agent_returns_zero(self):
        _request_timestamps["user:alice:agent:writer"] = deque([990.0])
        self.assertEqual(_request_count_last_minute_sync("alice", "missing"), 0)


class TestRecordAndCountAsync(unittest.TestCase):

    def setUp(self):
        _request_timestamps.clear()
        self.addCleanup(_request_timestamps.clear)

    def test_recorded_request_is_counted(self):
        async def scenario():
            await _record_request("user:alice")
            return await _request_count_last_minute("user:alice")

        self.assertEqual(asyncio.run(scenario()), 1)

    def test_count_for_unknown_key_is_zero(self):
        self.assertEqual(asyncio.run(_request_count_last_minute("user:nobody")), 0)

    def test_repeated_requests_accumulate(self):
        async def scenario():
            for _ in range(5):
                await _record_request("user:alice")
            return await _request_count_last_minute("user:alice")

        self.assertEqual(asyncio.run(scenario()), 5)


class TestConfigCrud(unittest.TestCase):
    """Config accessors must degrade safely when the database is unavailable."""

    def _service_without_db(self):
        service = rate_limit_service.RateLimitService()
        service._get_session = MagicMock(return_value=None)
        return service

    def test_get_config_returns_none_without_db(self):
        self.assertIsNone(self._service_without_db().get_config("user", "alice"))

    def test_get_configs_returns_empty_list_without_db(self):
        self.assertEqual(self._service_without_db().get_configs(), [])

    def test_upsert_config_returns_none_without_db(self):
        self.assertIsNone(
            self._service_without_db().upsert_config("user", "alice", requests_per_min=10)
        )

    def test_delete_config_returns_false_without_db(self):
        self.assertFalse(self._service_without_db().delete_config("user", "alice"))


if __name__ == "__main__":
    unittest.main()
