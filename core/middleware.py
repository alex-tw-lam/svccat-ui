"""Auth/tenant gate + cache policy + visible 500s: session read -> redirect unauthenticated -> tenant resolution with visible rejection -> Cache-Control: no-store on HTML."""

import logging
import traceback

from django.http import Http404

from core.views import _tenant_choices
from django.shortcuts import redirect

from core import kube

logger = logging.getLogger(__name__)


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path in ("/auth/login", "/auth/oidc") and request.session.get("sub"):
            return redirect(
                "/services"
            )  # never show the sign-in page to a signed-in visitor
        if not any(
            request.path == p or request.path.startswith(p)
            for p in ("/auth/", "/s/")
        ):  # paths that never require a session: login round-trip + the static prefix (a missing vendored file must fall through to a clean 404, not a login redirect)
            if not (session := request.session).get("sub"):
                return redirect("/auth/login")
            tenants = (
                kube.tenant_namespaces()
                if session.get("local") and kube.kube
                else (session.get("tenants") or [])
            )
            request.tenant_list = _tenant_choices(tenants)
            requested = request.GET.get("tenant") or session.get("last_tenant")
            tenant = (
                requested if requested in tenants else (request.tenant_list[0] if request.tenant_list else "")
            )
            if tenant and session.get("last_tenant") != tenant:
                session["last_tenant"] = tenant  # land where the user last was
            request.tenant = tenant
            # tenant isolation with visible semantics: a ?tenant= the
            # caller's token does not allow falls back to their own tenant
            # AND says so — never silent, never another tenant's data
            if requested and requested != tenant:
                request.tenant_rejected = requested
        response = self.get_response(request)
        if "text/html" in response.get("Content-Type", ""):
            response["Cache-Control"] = (
                "no-store"  # authenticated pages must never come from browser cache (tenant links, status rows and deployments change under the same URL)
            )
        return response

    def process_exception(self, request, exception):
        if isinstance(exception, Http404) or getattr(exception, "status", None) == 404:
            return None  # Http404 (a not-yet-vendored asset, see core/public/README.md) and k8s 404s are expected — fall through to the clean 404 pages, no log entry
        logger.error(
            "unhandled error %s %s: %s",
            request.method,
            request.path,
            traceback.format_exc(),
        )
        from core import views

        return views._error_page(
            request, 500, "services", str(exception)
        )  # 500s must be visible server-side; the page alone is not enough to debug transient failures
