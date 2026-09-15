"""Full-flow smoke on the in-memory backend (the committed port of the
campaign's curl smoke): login → catalog → provision (every 400 path) →
detail → edit → bind → credentials → delete → styled 404."""

from tests.base import MemoryCase


class TestSignInPage(MemoryCase):
    def test_login_page_shows_keycloak_button(self):
        resp = self.anon.get("/auth/login")
        self.assertEqual(resp.status_code, 200)
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

    def test_bind_missing_name_400(self):
        resp = self.client.post("/bindings/new?tenant=team-a", {"instanceId": "smoke"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"name and instance are required", resp.content)

    def test_bind_bad_json_uses_bind_prefix(self):
        resp = self.client.post(
            "/bindings/new?tenant=team-a",
            {"name": "smoke-b", "instanceId": "smoke", "parameters": "{bad"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"Bind parameters are not valid JSON", resp.content)

    def test_full_bind_and_credentials_notice(self):
        resp = self.client.post(
            "/bindings/new?tenant=team-a",
            {"name": "smoke-b", "instanceId": "smoke", "parameters": "{}"},
        )
        self.assertEqual(resp.status_code, 302)
        resp = self.client.get("/bindings/smoke-b/credentials?tenant=team-a")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"not available in memory mode", resp.content)

    def test_binding_and_instance_delete_redirect(self):
        self.client.post(
            "/bindings/new?tenant=team-a",
            {"name": "smoke-b", "instanceId": "smoke", "parameters": "{}"},
        )
        self.assertEqual(
            self.client.post("/bindings/delete/smoke-b?tenant=team-a").status_code, 302
        )
        self.assertEqual(
            self.client.post("/instances/delete/smoke?tenant=team-a").status_code, 302
        )


class TestAssets(MemoryCase):
    def test_first_party_assets_served(self):
        for path_, marker in [
            ("/app.js", b"smDrawer"),
            ("/app.css", b"stack-tag"),
        ]:
            resp = self.client.get(path_)
            self.assertEqual(resp.status_code, 200, path_)
            body = b"".join(resp.streaming_content)
            self.assertIn(marker, body, path_)

    def test_unknown_instance_renders_styled_404(self):
        resp = self.client.get("/instances/show/nope?tenant=team-a")
        self.assertEqual(resp.status_code, 404)
        self.assertIn(b"card border-error", resp.content)
