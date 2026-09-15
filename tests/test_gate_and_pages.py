"""The request gate (§1/§2/§10) and the page GET renders the old throwaway
smoke covered but the committed suite did not: anonymous redirect,
tenant-rejection visibility, no-store, every page/fragment render, OIDC
round-trip shapes, and the gzip asset branch."""

from django.test import Client

from tests.base import MemoryCase

import core.views as views


class TestGate(MemoryCase):
    def test_anonymous_redirected_to_login(self):
        c = Client()
        resp = c.get("/instances?tenant=team-a")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/auth/login")

    def test_unknown_tenant_falls_back_with_visible_banner(self):
        resp = self.client.get("/instances?tenant=not-mine")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"not-mine", resp.content)
        self.assertIn(b"team-a", resp.content)  # the fallback tenant shown

    def test_html_responses_are_no_store(self):
        resp = self.client.get("/instances?tenant=team-a")
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_underscore_tenant_can_never_be_selected(self):
        # an RFC1123-invalid ?tenant= is not in any list -> visible rejection
        resp = self.client.get("/instances?tenant=team_a")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"team_a", resp.content)


class TestPageRenders(MemoryCase):
    def provision(self, name="pg"):
        self.client.post(
            "/instances/new?tenant=team-a",
            {
                "name": name,
                "offeringId": "redis",
                "planId": "redis-small",
                "parameters": '{"maxmemory_policy": "allkeys-lru"}',
            },
        )

    def test_root_redirects_to_instances(self):
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_instance_list_renders_table(self):
        self.provision()
        resp = self.client.get("/instances?tenant=team-a")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"pg", resp.content)

    def test_instance_list_partial_fragment(self):
        self.provision()
        resp = self.client.get("/instances?tenant=team-a&partial=table")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"pg", resp.content)

    def test_provision_form_renders_locked_and_unlocked(self):
        self.assertEqual(
            self.client.get("/instances/new?tenant=team-a").status_code, 200
        )
        resp = self.client.get("/instances/new?tenant=team-a&offering=redis")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"redis", resp.content)

    def test_unknown_offering_404(self):
        resp = self.client.get("/instances/new?tenant=team-a&offering=nope")
        self.assertEqual(resp.status_code, 404)

    def test_plan_fields_fragment(self):
        resp = self.client.get(
            "/instances/new/plan?tenant=team-a&offeringId=redis&planId=small",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"plan-fields", resp.content)

    def test_schema_download_json(self):
        resp = self.client.get("/instances/new/schema?offeringId=redis&plan=small")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn(b"maxmemory_policy", resp.content)

    def test_detail_page_renders_events_and_poll(self):
        self.provision()
        resp = self.client.get("/instances/show/pg?tenant=team-a")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Events", resp.content)
        self.assertIn(b"hx-get", resp.content)  # poll while in flight

    def test_detail_partial_fragment(self):
        self.provision()
        resp = self.client.get("/instances/show/pg?tenant=team-a&partial=body")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"instance-detail", resp.content)

    def test_bind_form_get(self):
        self.provision()
        resp = self.client.get("/bindings/new?tenant=team-a&instance=pg")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"free bind inputs", resp.content)

    def test_gzip_variant_of_daisyui(self):
        import os

        gz = os.path.join(os.path.dirname(views.__file__), "public", "daisyui.css.gz")
        if not os.path.isfile(gz):
            self.skipTest("daisyui.css.gz not vendored")
        resp = self.client.get("/daisyui.css", HTTP_ACCEPT_ENCODING="gzip")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Encoding"], "gzip")

    def test_missing_vendored_asset_is_clean_404(self):
        from django.http import Http404

        with self.assertRaises(Http404):
            views._public_file("nope.css", "text/css")


class TestOidc(MemoryCase):
    def test_oidc_redirect_sets_pkce_cookie(self):
        resp = self.anon.get("/auth/oidc")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("location", [k.lower() for k in resp.headers])
        self.assertIn("realms/platform/protocol/openid-connect/auth", resp["Location"])
        self.assertIn("code_challenge_method=S256", resp["Location"])
        self.assertIn("sm_pkce", resp.cookies)

    def test_callback_state_mismatch_400(self):
        self.anon.get("/auth/oidc")  # sets sm_pkce
        resp = self.anon.get("/auth/callback?code=x&state=wrong")
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"missing code/state", resp.content)

    def test_jwt_payload_decodes(self):
        import base64
        import json

        payload = base64.urlsafe_b64encode(json.dumps({"sub": "u1"}).encode())
        self.assertEqual(views.jwt_payload(f"h.{payload.decode()}.s"), {"sub": "u1"})

    def test_logout_redirects_local_admin_to_login(self):
        resp = self.client.get("/auth/logout")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/auth/login")
