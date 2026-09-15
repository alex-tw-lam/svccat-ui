"""Record mappers (CR → UI dicts), tenant derivation (§2) and slugs (§7)."""

import os
import unittest
from unittest import mock

from core import views
from core import kube


def instance_cr(status=None, reason=None, conditions=(), deleting=False):
    cr = {
        "metadata": {
            "name": "svc-abc12345",
            "creationTimestamp": "2026-09-01T10:00:00Z",
        },
        "spec": {
            "clusterServiceClassExternalName": "redis",
            "clusterServicePlanExternalName": "redis-small",
            "parameters": {"maxmemory_policy": "allkeys-lru"},
        },
        "status": {"conditions": list(conditions)},
    }
    if status is not None:
        cr["status"]["conditions"].append({"type": "Ready", "status": status})
        if reason is not None:
            cr["status"]["conditions"][-1]["reason"] = reason
    if deleting:
        cr["metadata"]["deletionTimestamp"] = "2026-09-02T00:00:00Z"
    return cr


class TestInstanceRecord(unittest.TestCase):
    def status_of(self, **kw):
        return views.instance_record(instance_cr(**kw))["status"]

    def test_deletion_wins(self):
        self.assertEqual(self.status_of(status="True", deleting=True), "deprovisioning")

    def test_ready(self):
        self.assertEqual(self.status_of(status="True", reason="Provisioned"), "ready")

    def test_failed_on_nontransient_reason(self):
        self.assertEqual(
            self.status_of(status="False", reason="CreateFailed"), "failed"
        )

    def test_provisioning_on_inflight_family(self):
        self.assertEqual(
            self.status_of(status="False", reason="Provisioning"), "provisioning"
        )
        self.assertEqual(
            self.status_of(status="False", reason="ProvisionRequestInFlight"),
            "provisioning",
        )

    def test_updating(self):
        self.assertEqual(
            self.status_of(status="False", reason="UpdatingInstance"), "updating"
        )

    def test_missing_condition_means_provisioning(self):
        self.assertEqual(self.status_of(), "provisioning")

    def test_free_params_only(self):
        rec = views.instance_record(instance_cr(status="True"))
        self.assertEqual(rec["parameters"], {"maxmemory_policy": "allkeys-lru"})
        self.assertEqual(rec["offeringId"], "redis")
        self.assertEqual(rec["status"], "ready")


class TestBindingRecord(unittest.TestCase):
    def cr(self, status=None, reason=None, deleting=False):
        cr = {
            "metadata": {
                "name": "binding-1",
                "creationTimestamp": "2026-09-02T11:30:00Z",
            },
            "spec": {"instanceRef": {"name": "svc-abc12345"}, "secretName": "sec1"},
            "status": {},
        }
        if status is not None:
            cr["status"]["conditions"] = [
                {"type": "Ready", "status": status, "reason": reason or ""}
            ]
        if deleting:
            cr["metadata"]["deletionTimestamp"] = "x"
        return cr

    def test_ladder(self):
        self.assertEqual(
            views.binding_record(self.cr(deleting=True))["status"], "deleting"
        )
        self.assertEqual(views.binding_record(self.cr("True"))["status"], "ready")
        self.assertEqual(
            views.binding_record(self.cr("False", "BindFailed"))["status"], "failed"
        )
        self.assertEqual(
            views.binding_record(self.cr("False", "InjectingBindCredentials"))[
                "status"
            ],
            "creating",
        )


class TestAllowedTenants(unittest.TestCase):
    def call(self, groups, env):
        with mock.patch.dict(os.environ, env):
            return views.allowed_tenants({"groups": groups})

    def test_groups_filtered_to_allowlist(self):
        self.assertEqual(
            self.call(["team-a", "team_c"], {"ALLOWED_TENANTS": "team-a,team-b"}),
            ["team-a"],
        )

    def test_underscore_group_can_never_be_a_tenant(self):
        self.assertEqual(self.call(["team_a"], {"ALLOWED_TENANTS": "team-a"}), [])

    def test_no_allowlist_means_all_valid_groups(self):
        self.assertEqual(self.call(["/team-a"], {}), ["team-a"])

    def test_fallback_only_when_no_scoped_group(self):
        self.assertEqual(
            self.call(
                ["team-b"], {"ALLOWED_TENANTS": "team-a", "FALLBACK_TENANTS": "team-x"}
            ),
            ["team-x"],
        )
        self.assertEqual(
            self.call([], {"ALLOWED_TENANTS": "team-a", "FALLBACK_TENANTS": ""}), []
        )

    def test_dedupe_preserves_order(self):
        self.assertEqual(self.call(["team-a", "team-a"], {}), ["team-a"])


class TestSlugs(unittest.TestCase):
    def test_valid_name_passes_through(self):
        self.assertEqual(
            kube.instance_slug("my-redis"), {"id": "my-redis", "displayName": None}
        )

    def test_invalid_name_becomes_stable_slug(self):
        s = kube.instance_slug("My Redis!")
        self.assertTrue(s["id"].startswith("svc-"))
        self.assertEqual(s["displayName"], "My Redis!")
        # stable: same input → same slug
        self.assertEqual(kube.instance_slug("My Redis!"), s)

    def test_binding_slug_uses_b_prefix(self):
        self.assertTrue(kube.binding_slug("Ünïcode Name")["id"].startswith("b-"))


class TestFmtCreated(unittest.TestCase):
    def test_formats_utc(self):
        self.assertEqual(
            views.fmt_created("2026-09-01T10:00:00Z"), "9/1/2026, 10:00:00 AM"
        )

    def test_empty_and_garbage(self):
        self.assertEqual(views.fmt_created(""), "")
        self.assertEqual(views.fmt_created("garbage"), "garbage")
