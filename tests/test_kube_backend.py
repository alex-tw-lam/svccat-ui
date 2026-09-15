"""The kube backend's write paths against a recording fake API client —
spec shapes, pinned-params-last, plan ref surgery, and the error semantics
views rely on. No cluster needed."""

import unittest
from unittest import mock

from core import kube


class SimpleNs:
    def __init__(self, items):
        self.items = items


class FakeApi:
    """Records every call; returns queued results or raises queued errors."""

    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def __getattr__(self, name):
        def call(*args, **kw):
            self.calls.append((name, args))
            result = self.results.pop(0) if self.results else {}
            if isinstance(result, Exception):
                raise result
            if (
                isinstance(result, dict)
                and "items" not in result
                and name.startswith("list_")
            ):
                return {"items": []}  # k8s list responses carry .items
            return result

        return call


class KubeCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeApi()
        self.kube = {"custom": self.fake, "core": self.fake, "rbac": self.fake}
        patcher = mock.patch.object(kube, "kube", self.kube)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestCreateInstance(KubeCase):
    def test_cluster_refs_and_annotation(self):
        kube.create_instance(
            "t1", "my-redis", "redis", "small", display_name="My Redis"
        )
        name, args = self.fake.calls[0]
        self.assertEqual(name, "create_namespaced_custom_object")
        body = args[4]
        self.assertEqual(body["kind"], "ServiceInstance")
        self.assertEqual(
            body["metadata"]["annotations"],
            {"service-manager.io/display-name": "My Redis"},
        )
        self.assertEqual(body["spec"]["clusterServiceClassExternalName"], "redis")

    def test_platform_pinned_params_come_last(self):
        # tenant isolation: a user-supplied namespace must be overridden
        kube.create_instance(
            "t1",
            "x",
            "redis",
            "small",
            extra_params={"namespace": "evil", "instance_name": "evil"},
        )
        body = self.fake.calls[0][1][4]
        params = body["spec"]["parameters"]
        self.assertEqual(params["namespace"], "t1")
        self.assertEqual(params["instance_name"], "x")
        # literally last keys in the dict
        self.assertEqual(list(params)[-2:], ["namespace", "instance_name"])

    def test_namespaced_refs_use_ref_fields(self):
        kube.create_instance(
            "t1",
            "n1",
            "redis",
            "small",
            namespaced_refs={"classRef": "c1", "planRef": "p1"},
        )
        spec = self.fake.calls[0][1][4]["spec"]
        self.assertEqual(spec["serviceClassName"], "c1")
        self.assertEqual(spec["servicePlanName"], "p1")
        self.assertNotIn("clusterServiceClassExternalName", spec)


class TestCreateBinding(KubeCase):
    def test_binding_body_shape(self):
        kube.create_binding("t1", "b1", "inst1", display_name="B", bind_params={"k": 1})
        body = self.fake.calls[0][1][4]
        self.assertEqual(body["kind"], "ServiceBinding")
        self.assertEqual(body["spec"]["instanceRef"], {"name": "inst1"})
        self.assertEqual(body["spec"]["secretName"], "binding-b1")
        self.assertEqual(body["spec"]["parameters"], {"k": 1})


class TestEnsureNamespace(KubeCase):
    def test_creates_only_on_404(self):
        err = kube.ApiException(status=404)
        ok = {}  # creates succeed
        self.fake.results = [
            err,
            ok,
            err,
            ok,
        ]  # read(ns)=404, create, read(rb)=404, create
        kube.ensure_namespace("t1")
        verbs = [c[0] for c in self.fake.calls]
        self.assertEqual(
            verbs,
            [
                "read_namespace",
                "create_namespace",
                "read_namespaced_role_binding",
                "create_namespaced_role_binding",
            ],
        )
        rb = self.fake.calls[3][1][1]
        self.assertEqual(rb["metadata"]["name"], "service-manager-binding-secrets")

    def test_other_errors_escalate(self):
        self.fake.results = [kube.ApiException(status=403)]
        with self.assertRaises(kube.ApiException):
            kube.ensure_namespace("t1")


class TestListEvents(KubeCase):
    class E:
        def __init__(self, lt=None, ft=None, et=None):
            class Obj:
                kind, name = "ServiceInstance", "x"

            self.involved_object = Obj()
            self.reason, self.type, self.message = "Provisioned", "Normal", "m"
            self.last_timestamp, self.first_timestamp, self.event_time = lt, ft, et

    def test_timestamp_preference_and_iso(self):
        from datetime import datetime, timezone

        self.fake.results = [
            SimpleNs(items=[self.E(lt=datetime(2026, 1, 1, tzinfo=timezone.utc))])
        ]
        out = kube.list_events("t1")
        self.assertEqual(out[0]["lastTimestamp"], "2026-01-01T00:00:00+00:00")

    def test_no_timestamp_is_empty_string(self):
        self.fake.results = [SimpleNs(items=[self.E()])]
        self.assertEqual(kube.list_events("t1")[0]["lastTimestamp"], "")


class TestResolveRefs(KubeCase):
    def classes(self):
        return [{"metadata": {"name": "c-uuid"}, "spec": {"externalName": "redis"}}]

    def test_found(self):
        self.fake.results = [
            {"items": self.classes()},
            {
                "items": [
                    {
                        "metadata": {"name": "p-uuid"},
                        "spec": {
                            "externalName": "small",
                            "serviceClassRef": {"name": "c-uuid"},
                        },
                    }
                ]
            },
        ]
        self.assertEqual(
            kube.resolve_namespaced_refs("t1", "redis", "small"),
            {"classRef": "c-uuid", "planRef": "p-uuid"},
        )

    def test_missing_class_returns_none_without_listing_plans(self):
        self.fake.results = [{"items": []}]
        self.assertIsNone(kube.resolve_namespaced_refs("t1", "nope", "small"))
        self.assertEqual(len(self.fake.calls), 1)  # plans never listed

    def test_plan_of_other_class_rejected(self):
        self.fake.results = [
            {"items": self.classes()},
            {
                "items": [
                    {
                        "metadata": {"name": "p2"},
                        "spec": {
                            "externalName": "small",
                            "serviceClassRef": {"name": "some-other-class"},
                        },
                    }
                ]
            },
        ]
        self.assertIsNone(kube.resolve_namespaced_refs("t1", "redis", "small"))


class TestEnrichNamespaced(KubeCase):
    def test_attaches_external_names(self):
        self.fake.results = [
            {
                "items": [
                    {"metadata": {"name": "c1"}, "spec": {"externalName": "redis"}}
                ]
            },
            {
                "items": [
                    {"metadata": {"name": "p1"}, "spec": {"externalName": "small"}}
                ]
            },
        ]
        items = [
            {
                "metadata": {"name": "i1"},
                "spec": {
                    "serviceClassRef": {"name": "c1"},
                    "servicePlanRef": {"name": "p1"},
                },
            }
        ]
        out = kube._enrich_namespaced("t1", items)
        self.assertEqual(out[0]["_offeringExternalName"], "redis")
        self.assertEqual(out[0]["_planExternalName"], "small")

    def test_no_refs_passes_through_without_catalog_reads(self):
        out = kube._enrich_namespaced("t1", [{"metadata": {"name": "i1"}, "spec": {}}])
        self.assertEqual(out[0], {"metadata": {"name": "i1"}, "spec": {}})
        self.assertEqual(self.fake.calls, [])
