"""The in-memory dev store (§7): create → simulated ~4s completion →
delete, per-tenant isolation and the 200-event retention cap."""

import unittest
from datetime import datetime, timedelta, timezone

from core import memory


class TestLifecycle(unittest.TestCase):
    def setUp(self):
        memory._MEM.clear()
        memory._MEM.update({"instances": {}, "bindings": {}})
        memory._EVENTS.clear()

    def test_create_then_list_is_inflight_then_ages_ready(self):
        memory.create_instance("t1", "i1", "redis", "redis-small")
        self.assertEqual(memory.list_instances("t1")[0]["status"], "provisioning")
        # force the clock past the 4s window
        rec = memory._MEM["instances"]["t1"][0]
        rec["createdAt"] = (
            datetime.now(timezone.utc) - memory._READY_AFTER - timedelta(seconds=1)
        ).isoformat()
        self.assertEqual(memory.list_instances("t1")[0]["status"], "ready")

    def test_completion_event_emitted(self):
        memory.create_binding("t1", "b1", "i1")
        rec = memory._MEM["bindings"]["t1"][0]
        rec["createdAt"] = "2020-01-01T00:00:00+00:00"
        memory.list_bindings("t1")
        reasons = [e["reason"] for e in memory.events("t1", "ServiceBinding")]
        self.assertIn("InjectedBindCredentials", reasons)

    def test_get_returns_matching_or_none(self):
        memory.create_instance("t1", "i1", "redis", "redis-small")
        self.assertIsNotNone(memory.get_instance("t1", "i1"))
        self.assertIsNone(memory.get_instance("t1", "nope"))

    def test_delete_raises_on_missing(self):
        with self.assertRaises(LookupError):
            memory.delete_instance("t1", "nope")

    def test_tenants_are_isolated(self):
        memory.create_instance("t1", "i1", "redis", "redis-small")
        memory.create_instance("t2", "i2", "redis", "redis-small")
        self.assertEqual([r["id"] for r in memory.list_instances("t1")], ["i1"])
        self.assertEqual([r["id"] for r in memory.list_instances("t2")], ["i2"])

    def test_event_retention_cap_200(self):
        for n in range(230):
            memory.create_instance("t1", f"i{n}", "redis", "redis-small")
        self.assertLessEqual(len(memory._EVENTS), 200)
