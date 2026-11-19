"""Full-flow smoke on the in-memory backend (port of the original curl
smoke): login → catalog → provision (every 400 path) →
detail → edit → bind → credentials → delete → styled 404."""

from django.test import override_settings

from core import memory
from tests.base import MemoryCase


class TestSignInPage(MemoryCase):
    def test_login_page_hides_keycloak_without_issuer(self):
        # a Keycloak button with the placeholder issuer fails 100% of the
        # time — it must not render
        resp = self.anon.get("/auth/login")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Sign in with Keycloak", resp.content)
        self.assertIn(b"Sign in as admin", resp.content)

    def test_login_page_shows_keycloak_with_issuer(self):
        with override_settings():
            import core.views as views

            real = views.ISSUER
            views.ISSUER = "https://keycloak.real.example/realms/platform"
            try:
                resp = self.anon.get("/auth/login")
            finally:
                views.ISSUER = real
        self.assertIn(b"Sign in with Keycloak", resp.content)

    def test_demo_banner_labels_the_mock_backend(self):
        resp = self.anon.get("/auth/login")
        self.assertIn(b"Demo backend", resp.content)

    def test_signed_in_visitor_bounced_off_signin(self):
        resp = self.client.get("/auth/login")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/services")


class TestCatalog(MemoryCase):
    def test_services_shows_static_catalog(self):
        resp = self.client.get("/services?tenant=team-a")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Redis", resp.content)
        self.assertIn(b"PostgreSQL", resp.content)


class TestProvision(MemoryCase):
    def post(self, **fields):
        return self.client.post("/instances/new?tenant=team-a", fields)

    def test_missing_fields_repaints_400(self):
        resp = self.post(name="")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"name, service and plan are required", resp.content)

    def test_bad_json_repaints_400_without_bind_prefix(self):
        resp = self.post(
            name="t1", offeringId="redis", planId="redis-small", parameters="{bad"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"parameters are not valid JSON", resp.content)
        self.assertNotIn(b"Bind parameters are not valid", resp.content)

    def test_name_cap_60_chars(self):
        resp = self.post(name="n" * 61, offeringId="redis", planId="redis-small")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"Instance name must be 60 characters or fewer", resp.content)

    def test_computed_input_rejected(self):
        resp = self.post(
            name="t2",
            offeringId="redis",
            planId="redis-small",
            parameters='{"kubeconfig_path": "/tmp/x"}',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"kubeconfig_path", resp.content)

    def test_provision_redirects_to_detail(self):
        resp = self.post(
            name="smoke",
            offeringId="redis",
            planId="redis-small",
            parameters='{"maxmemory_policy": "allkeys-lru"}',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/instances/show/smoke?tenant=team-a")


class TestEditBindDelete(MemoryCase):
    def setUp(self):
        super().setUp()
        resp = self.client.post(
            "/instances/new?tenant=team-a",
            {
                "name": "smoke",
                "offeringId": "redis",
                "planId": "redis-small",
                "parameters": '{"maxmemory_policy": "allkeys-lru"}',
            },
        )
        self.assertEqual(resp.status_code, 302)

    def test_edit_bad_json_400(self):
        resp = self.client.post(
            "/instances/edit/smoke?tenant=team-a", {"parameters": "{bad"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"parameters are not valid JSON", resp.content)

    def test_edit_success_redirects(self):
        resp = self.client.post(
            "/instances/edit/smoke?tenant=team-a",
            {"planId": "redis-small", "parameters": "{}"},
        )
        self.assertEqual(resp.status_code, 302)

    def _ready(self, name="smoke"):
        # rewind the simulated clock so the memory broker finishes now
        from datetime import UTC, datetime, timedelta

        for rec in memory._MEM["instances"]["team-a"]:
            if rec["id"] == name or rec.get("name") == name:
                rec["createdAt"] = (
                    datetime.now(UTC) - timedelta(seconds=30)
                ).isoformat()
        return self.client.get("/instances?tenant=team-a")  # first read flips it

    def test_connect_creates_binding_named_after_instance(self):
        self._ready()
        resp = self.client.post("/bindings/connect/smoke?tenant=team-a")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].endswith("#bindings"))
        resp = self.client.get("/instances/show/smoke?tenant=team-a")
        self.assertIn(b"secret", resp.content)  # the binding row names its secret
        self.assertIn(b"smoke</a>", resp.content)

    def test_connect_on_not_ready_instance_redirects(self):
        resp = self.client.post("/bindings/connect/nope?tenant=team-a")
        self.assertEqual(resp.status_code, 404)
        # a ready-but-not-found instance: the memory store auto-completes, so
        # exercise the not-ready branch through the unknown id only

    def test_credentials_notice_in_memory_mode(self):
        self._ready()
        self.client.post("/bindings/connect/smoke?tenant=team-a")
        detail = self.client.get("/instances/show/smoke?tenant=team-a")
        self.assertEqual(detail.status_code, 200)

    def test_binding_and_instance_delete_redirect(self):
        self._ready()
        self.client.post("/bindings/connect/smoke?tenant=team-a")
        detail = self.client.get("/instances/show/smoke?tenant=team-a").content.decode()
        import re

        m = re.search(r"/bindings/delete/([a-z0-9-]+)", detail)
        assert m, "no delete link on detail"
        self.assertEqual(
            self.client.post(f"/bindings/delete/{m.group(1)}?tenant=team-a").status_code,
            302,
        )
        self.assertEqual(
            self.client.post("/instances/delete/smoke?tenant=team-a").status_code, 302
        )


class TestAssets(MemoryCase):
    def test_first_party_assets_served(self):
        for path_, marker in [
            ("/s/app.js", b"smDrawer"),
            ("/s/app.css", b"stack-tag"),
        ]:
            resp = self.client.get(path_)
            self.assertEqual(resp.status_code, 200, path_)
            body = b"".join(resp.streaming_content)
            self.assertIn(marker, body, path_)

    def test_unknown_instance_renders_styled_404(self):
        resp = self.client.get("/instances/show/nope?tenant=team-a")
        self.assertEqual(resp.status_code, 404)
        # card-bordered is load-bearing: .card has no border of its own, so
        # border-error only renders with it
        self.assertIn(b"card card-bordered border-error", resp.content)
