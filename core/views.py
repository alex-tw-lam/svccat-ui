"""All routes: OIDC auth, vendored asset views, catalog/instance/binding pages."""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode
import requests
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST
from core import kube, memory
from core.input_schema import (
    bind_schema_for,
    drawer_contract,
    form_schema_for,
    input_schema_for,
    validate_bind_params,
    validate_user_params,
)

logger = logging.getLogger(__name__)
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")
IN_FLIGHT = ["provisioning", "updating", "deprovisioning"]
NAMESPACE_RE = re.compile(
    r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"
)  # tenants: token groups ∩ ALLOWED_TENANTS, else FALLBACK_TENANTS (fail closed); names must be RFC 1123 # labels (an underscore group like "team_a" can never be a namespace — filtered here vs a 422 mid-provision)


def _env_tenants(var):
    return [
        t
        for t in (s.strip() for s in os.environ.get(var, "").split(","))
        if t and NAMESPACE_RE.fullmatch(t)
    ]


def allowed_tenants(token_payload):
    allow = _env_tenants("ALLOWED_TENANTS")
    groups = [
        g
        for g in (g.lstrip("/") for g in (token_payload.get("groups") or []))
        if NAMESPACE_RE.fullmatch(g)
    ]
    scoped = [g for g in groups if g in allow] if allow else groups
    return list(dict.fromkeys(scoped)) or _env_tenants(
        "FALLBACK_TENANTS"
    )  # dedupe, else explicit fallback


def _safe(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return ()


def _offerings(tenant):
    # kube mode shows the cluster catalog as-is: an empty or unreadable
    # catalog is an honest empty marketplace, never the dev mock — mock data
    # must not leak into a real deployment
    if not kube.kube:
        return memory.STATIC_CATALOG
    return catalog_from_crs(
        _safe(kube.list_cluster_service_classes),
        _safe(kube.list_cluster_service_plans),
    ) + catalog_from_crs(
        _safe(kube.list_service_classes, tenant),
        _safe(kube.list_service_plans, tenant),
        namespaced=True,
    )


def _bindings(tenant):
    return (
        memory.list_bindings(tenant)
        if not kube.kube
        else [binding_record(r) for r in kube.list_bindings(tenant)]
    )


def _instances(tenant):
    name_by_id, binds = (
        {o["id"]: o["name"] for o in _offerings(tenant)},
        _bindings(tenant),
    )
    return [
        {
            **r,
            "offeringName": name_by_id.get(r["offeringId"], r["offeringId"]),
            "bindingCount": sum(b["instanceId"] == r["id"] for b in binds),
        }
        for r in (
            memory.list_instances(tenant)
            if not kube.kube
            else [instance_record(r) for r in kube.list_instances(tenant)]
        )
    ]


def _events(tenant, kind, name=None):
    try:
        evs = sorted(
            memory.events(tenant, kind, name)
            if not kube.kube
            else [
                e
                for e in kube.list_events(tenant)
                if e["kind"] == kind and (name is None or e["name"] == name)
            ],
            key=lambda e: str(e.get("lastTimestamp") or ""),
            reverse=True,
        )  # With a name: that CR's events, newest first, cap 20; without: newest event per name, as a dict.
    except Exception:
        return {} if name is None else []
    return (
        {e["name"]: e for e in reversed(evs)}
        if name is None
        else [dict(e, ts=fmt_created(e.get("lastTimestamp"))) for e in evs[:20]]
    )  # newest per name


def _is404(err):
    return getattr(err, "status", None) == 404


def _finder(mem_get, kube_get, record):
    def find(tenant, id_):
        try:
            return (
                mem_get(tenant, id_) if not kube.kube else record(kube_get(tenant, id_))
            )
        except Exception as err:
            if _is404(err):
                return None
            raise

    return find


def catalog_from_crs(classes, plans, namespaced=False):
    # Class/Plan CRs -> the UI's Offering shape, shared by cluster-wide and
    # namespaced (per-tenant broker) classes; namespaced=True flags
    # tenant-exclusive tiles. UUID class/plan refs are resolved at provision
    # time (kube.resolve_namespaced_refs).
    class_ref = "serviceClassRef" if namespaced else "clusterServiceClassRef"
    offerings = []
    for c in classes:
        spec = c.get("spec") or {}
        plans_for_class = []
        for p in plans:
            ps = p.get("spec") or {}
            if (ps.get(class_ref) or {}).get("name") != c["metadata"]["name"]:
                continue
            plans_for_class.append(
                {
                    "id": ps.get("externalName"),
                    "name": ps.get("externalName"),
                    "description": ps.get("description") or "",
                }
            )
        offerings.append(
            {
                "id": spec.get("externalName"),
                "name": (spec.get("externalMetadata") or {}).get("displayName")
                or spec.get("externalName"),
                "description": spec.get("description") or "",
                "tags": spec.get("tags") or [],
                "namespaced": namespaced,
                "plans": plans_for_class,
                "inputSchema": input_schema_for(spec.get("externalName")),
            }
        )
    return offerings


def _cr_state(cr):
    condition = next(
        (
            c
            for c in (cr.get("status") or {}).get("conditions") or []
            if c.get("type") == "Ready"
        ),
        None,
    )
    return (
        condition,
        condition.get("status") if condition else None,
        (condition.get("reason") if condition else None) or "",
        cr.get("metadata") or {},
        cr.get("spec") or {},
    )  # shared CR preamble for both record mappers: (condition, ready, reason, metadata, spec)


def instance_record(cr):
    condition, ready, reason, metadata, spec = _cr_state(cr)
    if metadata.get("deletionTimestamp"):
        status = "deprovisioning"
    elif ready == "True":
        status = "ready"
    elif (
        ready == "False"
        and reason not in ("Provisioning", "Deprovisioning", "UpdatingInstance")
        and not reason.endswith("InFlight")
    ):
        # Ready=False with a non-transient reason (the InFlight-suffix family
        # and the named in-flight reasons stay "provisioning")
        status = "failed"
    elif ready == "False" and reason == "UpdatingInstance":
        status = "updating"
    else:
        status = "provisioning"
    display_name = (metadata.get("annotations") or {}).get(
        "service-manager.io/display-name"
    )
    offering = (
        cr.get("_offeringExternalName")
        or spec.get("clusterServiceClassExternalName")
        or ""
    )
    schema = input_schema_for(offering)
    free_inputs = [f["name"] for f in (schema or {}).get("userInputs") or []]
    current_params = {
        k: v for k, v in (spec.get("parameters") or {}).items() if k in free_inputs
    }
    conditions = (cr.get("status") or {}).get("conditions") or []
    message = (
        (condition or {}).get("message")
        or (conditions[0].get("message") if conditions else "")
        or ""
    )
    return {
        "id": metadata.get("name"),
        "name": display_name or metadata.get("name"),
        "resourceName": metadata.get("name"),
        "parameters": current_params or None,
        "offeringId": offering,
        "planId": cr.get("_planExternalName")
        or spec.get("clusterServicePlanExternalName"),
        "status": status,
        "createdAt": metadata.get("creationTimestamp"),
        "reason": reason,
        "message": message,
        "dashboardUrl": (cr.get("status") or {}).get("dashboardURL"),
    }  # free user inputs only (pins/plan/computed are not user-editable), for the edit drawer's re-submit


def binding_record(cr):
    _, ready, reason, metadata, spec = _cr_state(cr)
    if metadata.get("deletionTimestamp"):
        status = "deleting"
    elif ready == "True":
        status = "ready"
    elif (
        ready == "False"
        and reason != "InjectingBindCredentials"
        and not reason.endswith("InFlight")
    ):
        status = "failed"
    else:
        status = "creating"
    return {
        "id": metadata.get("name"),
        "name": (metadata.get("annotations") or {}).get(
            "service-manager.io/display-name"
        )
        or metadata.get("name"),
        "resourceName": metadata.get("name"),
        "instanceId": ((spec.get("instanceRef") or {}).get("name"))
        or ((spec.get("serviceInstanceRef") or {}).get("name"))
        or "",
        "status": status,
        "secretName": spec.get("secretName") or f"binding-{metadata.get('name')}",
        "createdAt": metadata.get("creationTimestamp"),
    }


def fmt_created(ts):
    if not ts:
        return (
            ""  # creationTimestamp → the UI's m/d/Y 12-hour format; containers run UTC.
        )
    try:
        dt = datetime.fromisoformat(str(ts))
    except ValueError:
        return str(ts)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%-m/%-d/%Y, %-I:%M:%S %p")


_find_instance = _finder(memory.get_instance, kube.get_instance, instance_record)
_find_binding = _finder(memory.get_binding, kube.get_binding, binding_record)


def _page_ctx(request, title, **extra):
    return {
        "title": title,
        "cluster_name": os.environ.get("CLUSTER_NAME", "in-cluster"),
        # demo: no cluster configured -> the in-memory mock backend is serving
        "demo": not kube.kube,
        "session_name": request.session.get("name", ""),
        "tenants": getattr(request, "tenant_list", None)
        or request.session.get("tenants")
        or [],
        "tenant": getattr(request, "tenant", ""),
        "tenant_rejected": getattr(request, "tenant_rejected", None),
        "flash": request.GET.get("flash"),
        **extra,
    }  # tenants: middleware-resolved list (admin: cluster-wide in kube mode), else the session's


def _error_page(request, status, back, message):
    return render(
        request,
        "core/error.html",
        _page_ctx(request, "Error", status=status, back=back, message=message),
        status=status,
    )


def _page(request, title, template, key, ctx, crumbs, status=200):
    return render(
        request,
        template,
        _page_ctx(request, title, crumbs=crumbs, **{key: ctx}),
        status=status,
    )


def _crumbs(tenant, *pairs):
    return [{"label": tenant, "href": f"/instances?tenant={tenant}"}] + [
        {"label": label, "href": href} for label, href in pairs
    ]


def _tmpl(request, page, partial, key):
    return (
        f"{page}#{partial}" if request.GET.get("partial") == key else page
    )  # ?partial= selects the {% partialdef %} fragment


def base_url(request):
    return os.environ.get("EXTERNAL_URL") or f"{request.scheme}://{request.get_host()}"


KC_CLIENT_ID = os.environ.get("KC_CLIENT_ID", "service-manager")
ISSUER = f"{os.environ.get('KC_URL', 'https://keycloak.example.com')}/realms/{os.environ.get('KC_REALM', 'platform')}/protocol/openid-connect"
AUTHORIZE_URL, TOKEN_URL, LOGOUT_URL = (
    f"{ISSUER}/auth",
    f"{ISSUER}/token",
    f"{ISSUER}/logout",
)


def jwt_payload(token):
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(
        base64.urlsafe_b64decode(part.encode("ascii"))
    )  # Unverified decode: the token arrived over TLS straight from the token endpoint.


def _admin_credentials_ok(user, password):
    expected_user, expected_pass = (
        os.environ.get("ADMIN_USER", "admin"),
        os.environ.get("ADMIN_PASSWORD") or "",
    )
    return (
        bool(expected_pass)
        and hmac.compare_digest(str(user or "").encode(), expected_user.encode())
        and hmac.compare_digest(str(password or "").encode(), expected_pass.encode())
    )


def _login_page(request, status=200, **extra):
    return render(
        request,
        "core/login.html",
        _page_ctx(
            request,
            "Sign in",
            local_enabled=bool(os.environ.get("ADMIN_PASSWORD")),
            **extra,
        ),
        status=status,
    )


auth_login = require_GET(_login_page)


@require_GET
def auth_oidc(request):
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    state = secrets.token_urlsafe(16)
    params = urlencode(
        {
            "client_id": KC_CLIENT_ID,
            "response_type": "code",
            "scope": "openid profile",
            "redirect_uri": f"{base_url(request)}/auth/callback",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    resp = redirect(f"{AUTHORIZE_URL}?{params}")
    resp.set_cookie(
        "sm_pkce",
        f"{state}.{verifier}",
        path="/auth/callback",
        httponly=True,
        samesite="Lax",
        max_age=600,
    )
    return resp  # S256 challenge: base64url(sha256(verifier)), padding stripped; short-lived state cookie pairs login↔callback; the verifier stays server-side


@require_POST
def auth_local(request):
    if not _admin_credentials_ok(
        user := request.POST.get("username", ""), request.POST.get("password", "")
    ):
        logger.warning(
            "local admin login failed (user=%r addr=%s)",
            user,
            request.META.get("REMOTE_ADDR", ""),
        )
        return _login_page(request, 401, local_error="Invalid username or password.")
    request.session.update(
        {
            "sub": f"local:{os.environ.get('ADMIN_USER', 'admin')}",
            "name": os.environ.get("ADMIN_USER", "admin"),
            "tenants": _env_tenants("ALLOWED_TENANTS"),
            "local": True,
        }
    )
    logger.info("local admin login (user=%r)", user)
    return redirect("/services")


@require_GET
def auth_callback(request):
    code, state = request.GET.get("code"), request.GET.get("state")
    pair = request.COOKIES.get("sm_pkce")
    cookie_state, verifier = pair.split(".", 1) if pair else (None, None)
    if not code or not pair or state != cookie_state:
        return _error_page(
            request, 400, "auth/login", "missing code/state — restart login"
        )
    try:
        if (
            resp := requests.post(
                TOKEN_URL,
                timeout=15,
                data={
                    "grant_type": "authorization_code",
                    "client_id": KC_CLIENT_ID,
                    "code": code,
                    "redirect_uri": f"{base_url(request)}/auth/callback",
                    "code_verifier": verifier,
                },
            )
        ).status_code >= 400:
            raise RuntimeError(f"token exchange failed: {resp.status_code} {resp.text}")
        tokens = resp.json()
    except Exception as err:
        return _error_page(request, 502, "auth/login", f"token exchange failed: {err}")
    id_token, access_token = (
        jwt_payload(tokens["id_token"]),
        jwt_payload(tokens["access_token"]),
    )
    request.session.update(
        {
            "sub": id_token.get("sub"),
            "name": id_token.get("name")
            or id_token.get("preferred_username")
            or "unknown",
            "tenants": allowed_tenants(access_token),
        }
    )
    resp = redirect("/services")
    resp.delete_cookie("sm_pkce", path="/auth/callback")
    return resp


@require_GET
def auth_logout(request):
    local = bool(request.session.get("local"))
    request.session.flush()
    return (
        redirect("/auth/login")
        if local
        else redirect(
            f"{LOGOUT_URL}?{urlencode({'client_id': KC_CLIENT_ID, 'post_logout_redirect_uri': f'{base_url(request)}/services'})}"
        )
    )  # a local admin has no Keycloak session to end


def _public_file(name, content_type):
    if not os.path.isfile(path := os.path.join(PUBLIC_DIR, name)):
        raise Http404(f"{name} is not vendored yet — see core/public/README.md")
    resp = FileResponse(open(path, "rb"), content_type=content_type)
    resp["Cache-Control"] = "no-cache"
    return resp  # operator drops vendored assets in (core/public/README.md); first-party → always revalidate


def asset(name, content_type):
    return lambda request: _public_file(name, content_type)


def daisyui_css(
    request,
):  # serve the precompressed .gz when the client accepts gzip (gunicorn has no compress layer)
    gz = "gzip" in request.META.get("HTTP_ACCEPT_ENCODING", "") and os.path.isfile(
        os.path.join(PUBLIC_DIR, "daisyui.css.gz")
    )
    resp = _public_file("daisyui.css.gz" if gz else "daisyui.css", "text/css")
    if gz:
        resp["Content-Encoding"], resp["Vary"] = "gzip", "Accept-Encoding"
    return resp


@require_GET
def services(request):
    return _page(
        request,
        "Add Service Instance",
        "core/services.html",
        "offerings",
        _offerings(request.tenant),
        _crumbs(request.tenant, ("Add Service Instance", None)),
    )


@require_GET
def instances_page(request):
    latest = _events(request.tenant, "ServiceInstance")
    rows = [
        dict(
            r,
            createdFmt=fmt_created(r.get("createdAt")),
            latestEvent=latest.get(r["resourceName"]),
        )
        for r in sorted(
            _instances(request.tenant),
            key=lambda r: str(r.get("createdAt") or ""),
            reverse=True,
        )
    ]
    return _page(
        request,
        "Instances",
        _tmpl(request, "core/instances_page.html", "instances_table", "table"),
        "table",
        {
            "rows": rows,
            "tenant": request.tenant,
            "poll": any(r["status"] in IN_FLIGHT for r in rows) or not rows,
        },
        None,
    )  # poll while in flight OR empty — an empty list's staleness is the one state a user cannot see through


def _plan_fields(form):
    return [
        (n, p.get("default"))
        for n, p in (form or {}).get("schema", {}).get("properties", {}).items()
        if p.get("x-layer") == "plan" and p.get("readOnly")
    ]


def _free_input_hints(form):
    # one "name — type — one of: … — default: … — required" hint per editable
    # free input, rendered next to the raw-JSON drawer
    hints = []
    for name in (form or {}).get("editable", []):
        p = form["schema"]["properties"].get(name) or {}
        bits = [name]
        if p.get("type"):
            bits.append(str(p["type"]))
        if p.get("enum"):
            bits.append("one of: " + ", ".join(p["enum"]))
        if p.get("default") is not None:
            bits.append("default: " + json.dumps(p["default"]))
        if name in form["schema"].get("required", []):
            bits.append("required")
        hints.append(" — ".join(bits))
    return hints


def _contract_json(offering_id, op):
    # the Alpine drawer's client-side mirror of the server validators,
    # embedded as JSON; empty when the offering is unknown
    contract = drawer_contract(offering_id, op)
    return json.dumps(contract) if contract else ""


def _offering_ctx(cats, offering_id, plan_default=None, error="", values=None):
    offering = next((o for o in cats if o["id"] == offering_id), None)
    plan_id = (values or {}).get("planId") or (
        plan_default
        if plan_default is not None
        else ((offering or {}).get("plans") or [{}])[0].get("id")
    )
    form = form_schema_for(offering_id, "provision", plan_id) if offering else None
    schema = (offering or {}).get("inputSchema")
    precedence = (
        {
            "computed": ", ".join(f["name"] for f in schema["computedInputs"])
            or "none",
            "plan": ", ".join(f["name"] for f in schema["planInputs"]) or "none",
        }
        if schema
        else None
    )
    return {
        "offering": offering,
        "plan_id": plan_id,
        "error": error,
        "values": values or {},
        "precedence": precedence,
        "hints": _free_input_hints(form),
        "plan_fields": _plan_fields(form),
        "schema_json": json.dumps(form or {}, indent=2) if offering else "",
        "contract_json": _contract_json(offering_id if offering else None, "provision"),
    }  # shared base of the provision and edit form contexts: the offering lookup, plan, contract fields


def _provision_form_ctx(cats, offering_id, tenant, error="", values=None, locked=False):
    return {
        "cats": cats,
        "offering_id": offering_id,
        "locked": locked,
        "tenant": tenant,
        **_offering_ctx(cats, offering_id, values=values, error=error),
    }


def _provision_page(
    request, cats, offering_id, tenant, error="", values=None, locked=False, status=200
):
    form = _provision_form_ctx(cats, offering_id, tenant, error, values, locked)
    name = (form["offering"] or {}).get("name") or "New service instance"
    return _page(
        request,
        f"Provision {name}" if form["offering"] else "Provision a service instance",
        "core/provision_page.html",
        "form",
        form,
        _crumbs(tenant, (name, None)),
        status,
    )


def provision_get(request):
    tenant = request.tenant
    cats = _offerings(tenant)
    requested = request.GET.get("offering")
    return (
        _error_page(
            request,
            404,
            "services",
            f'service "{requested}" is not in this tenant\'s catalog',
        )
        if requested and not any(o["id"] == requested for o in cats)
        else _provision_page(
            request,
            cats,
            requested or (cats[0]["id"] if cats else ""),
            tenant,
            values={"planId": request.GET.get("plan")}
            if request.GET.get("plan")
            else {},
            locked=bool(requested),
        )
    )  # ?offering= locks the service (no dropdown); ?plan= preselects a plan


def _dispatch(get, post):
    # one URL per form: GET renders the form, POST submits it

    def view(request, *args, **kw):
        handler = post if request.method == "POST" else get
        return handler(request, *args, **kw)

    return view


@require_GET
def provision_form_fragment(request):
    # offering-select change fragment (only used in the unlocked/generic form)
    return render(
        request,
        "core/provision_form.html",
        {
            "form": _provision_form_ctx(
                _offerings(request.tenant),
                request.GET.get("offeringId") or "",
                request.tenant,
            )
        },
    )


@require_GET
def plan_fields_fragment(request):
    return (
        redirect("/instances/new")
        if not request.headers.get("HX-Request")
        else render(
            request,
            "core/plan_fields.html",
            {
                "plan_id": request.GET.get("planId"),
                "plan_fields": _plan_fields(
                    form_schema_for(
                        request.GET.get("offeringId"),
                        "provision",
                        request.GET.get("planId"),
                    )
                ),
            },
        )
    )


@require_GET
def schema_download(request):
    offering_id = request.GET.get("offeringId")
    plan = request.GET.get("plan") or ""
    return (
        _error_page(request, 404, "services", f'no form schema for "{offering_id}"')
        if not (form := form_schema_for(offering_id, "provision", plan or None))
        else JsonResponse(
            form,
            json_dumps_params={"indent": 2},
            headers={
                "Content-Disposition": f'attachment; filename="{offering_id}-{plan or "any"}-provision-schema.json"'
            },
        )
    )


class FormError(Exception):
    pass  # POST-validation failure → repaint the form, 400


def _checked(res):
    if error := res[1]:
        raise FormError(error)
    return res[0]


def _form_params(body, noun):
    name = body.get("name", "")
    parameters = (body.get("parameters") or "").strip()
    if len(str(name).strip()) > 60:
        raise FormError(f"{noun} name must be 60 characters or fewer")
    try:
        return None if not parameters else json.loads(parameters)
    except json.JSONDecodeError:
        raise FormError(
            f"{'Bind ' if noun == 'Binding' else ''}parameters are not valid JSON — fix before submitting"
        ) from None  # shared POST preamble: 60-char name cap + raw-JSON parse; violation → FormError


def provision_post(request):
    tenant = request.tenant
    body = request.POST
    name, offering_id, plan_id = (
        body.get(k, "") for k in ("name", "offeringId", "planId")
    )
    try:
        if not name or not offering_id or not plan_id:
            raise FormError("name, service and plan are required")
        params = _form_params(body, "Instance")
        checked = _checked(validate_user_params(offering_id, params))
        slug = kube.instance_slug(name)
        if kube.kube:
            kube.ensure_namespace(tenant)
            refs = kube.resolve_namespaced_refs(tenant, offering_id, plan_id)
            if not refs:
                try:
                    cluster_ids = [
                        x["spec"].get("externalName")
                        for x in kube.list_cluster_service_classes()
                    ]  # not namespaced → must be in the cluster-wide catalog; transient kube errors surface as themselves, never a catalog 400
                except Exception as err:
                    if not _is404(err):
                        raise
                    cluster_ids = []
                if offering_id not in cluster_ids:
                    raise FormError(
                        f'service "{offering_id}" is not available in this tenant\'s catalog'
                    )
            kube.create_instance(
                tenant,
                slug["id"],
                offering_id,
                plan_id,
                checked,
                slug["displayName"],
                refs,
            )
        else:
            memory.create_instance(tenant, str(name).strip(), offering_id, plan_id)
    except Exception as err:
        return _provision_page(
            request,
            _offerings(tenant),
            str(offering_id or ""),
            tenant,
            error=str(err) if isinstance(err, FormError) else _kube_err_message(err),
            values={
                "name": str(name or ""),
                "planId": str(plan_id or ""),
                "parameters": (body.get("parameters") or "").strip(),
            },
            locked=bool(offering_id),
            status=400,
        )  # provision repaints every error with its message; instance_edit re-raises non-400s as visible 500s (§10)
    redirect_id = slug["id"] if kube.kube else str(name).strip()
    return redirect(f"/instances/show/{redirect_id}?tenant={tenant}")


provision_dispatch = _dispatch(provision_get, provision_post)


def _kube_err_message(err):
    try:
        return json.loads(getattr(err, "body", None) or "{}").get("message") or str(err)
    except (AttributeError, ValueError):
        return str(err)


@require_GET
def instance_show(request, inst_id):
    if not (inst := _find_instance(tenant := request.tenant, inst_id)):
        return _error_page(request, 404, "instances", "instance not found")
    cats, all_bindings = _safe(_offerings, tenant), _safe(_bindings, tenant)
    offering_name = next(
        (o["name"] for o in cats if o["id"] == inst["offeringId"]), inst["offeringId"]
    )
    own = [
        dict(b, createdFmt=fmt_created(b.get("createdAt")))
        for b in all_bindings
        if b["instanceId"] == inst["id"]
    ]
    poll = inst["status"] in IN_FLIGHT or any(
        b["status"] in ("creating", "deleting") for b in own
    )
    detail = {
        "inst": inst,
        "offering_name": offering_name,
        "own": own,
        "tenant": tenant,
        "events": _events(tenant, "ServiceInstance", inst["resourceName"]),
        "poll": poll,
        "params_json": json.dumps(inst.get("parameters") or {}, indent=2),
        "created_fmt": fmt_created(inst.get("createdAt")),
    }
    return _page(
        request,
        inst["name"],
        _tmpl(request, "core/instance_page.html", "instance_detail", "body"),
        "detail",
        detail,
        _crumbs(tenant, (inst["name"], None)),
    )  # poll while the status can still change: instance OR any binding in flight — terminal states stop the timer


def _edit_page(request, tenant, inst, inst_id, error="", values=None, status=200):
    ctx = _offering_ctx(
        _safe(_offerings, tenant), inst["offeringId"], inst["planId"], error, values
    )
    ctx.update(
        inst=inst,
        tenant=tenant,
        params_json=json.dumps(inst.get("parameters") or {}, indent=2),
        plans=(ctx["offering"] or {}).get("plans")
        or [{"id": inst["planId"], "name": inst["planId"], "description": ""}],
    )
    return _page(
        request,
        f"Change {inst['name']}",
        "core/edit_page.html",
        "edit",
        ctx,
        _crumbs(
            tenant,
            (inst["name"], f"/instances/show/{inst_id}?tenant={tenant}"),
            ("Change plan", None),
        ),
        status,
    )


def instance_edit_post(request, inst_id):
    if not (inst := _find_instance(tenant := request.tenant, inst_id)):
        return _error_page(request, 404, "instances", "instance not found")
    try:
        body = request.POST
        values = {
            "planId": str(body.get("planId") or ""),
            "parameters": (body.get("parameters") or "").strip(),
        }
        params = _form_params(body, "Instance")
        checked = _checked(validate_user_params(inst["offeringId"], params))
        if kube.kube:
            kube.update_instance(tenant, inst_id, body.get("planId"), checked)
        else:
            inst.update(planId=str(body.get("planId") or inst["planId"]))
    except (FormError, ValueError) as err:
        return _edit_page(
            request, tenant, inst, inst_id, str(err), values, status=400
        )  # ValueError: namespaced plan not in tenant catalog
    except kube.ApiException as err:
        msg = _kube_err_message(err)
        if err.status == 403 and re.search(r"in progress", msg, re.I):
            return redirect(
                f"/instances/show/{inst_id}?tenant={tenant}"
            )  # 403 in-progress = double-submit racing the accepted update → land on detail, it polls to convergence
        if err.status == 400:
            return _edit_page(request, tenant, inst, inst_id, msg, values, status=400)
        raise
    return redirect(f"/instances/show/{inst_id}?tenant={tenant}")


def instance_edit(request, inst_id):
    inst = _find_instance(request.tenant, inst_id)
    if not inst:
        return _error_page(request, 404, "instances", "instance not found")
    return _edit_page(request, request.tenant, inst, inst_id)


instance_edit_dispatch = _dispatch(require_GET(instance_edit), instance_edit_post)


@require_POST
def instance_delete(request, inst_id):
    # pessimistic delete: row shows deprovisioning until the broker finishes;
    # the redirect carries the notification banner
    tenant = request.tenant
    if _find_instance(tenant, inst_id):
        if kube.kube:
            kube.delete_instance(tenant, inst_id)
        else:
            memory.delete_instance(tenant, inst_id)
    return redirect(f"/instances?tenant={tenant}&flash=deletion-started")


def _bind_form_ctx(tenant, insts, preset, error="", values=None):
    values = values or {}
    selected_id = (
        values.get("instanceId") or preset or (insts[0]["id"] if insts else "")
    )
    selected = next((i for i in insts if i["id"] == selected_id), None)
    form, bind = (
        (
            form_schema_for(selected["offeringId"], "bind"),
            bind_schema_for(selected["offeringId"]),
        )
        if selected
        else (None, None)
    )
    free = form["editable"] if form else []
    computed = [f["name"] for f in (bind or {}).get("computedInputs", [])]
    hint = (
        "free bind inputs: none"
        if not form and not bind
        else f"free bind inputs: {', '.join(free) or 'none'}"
        + (
            f" (computed bind inputs projected from the instance: {', '.join(computed)})"
            if computed
            else ""
        )
    )
    return {
        "insts": insts,
        "selected_id": selected_id,
        "error": error,
        "values": values,
        "tenant": tenant,
        "hint": hint,
        "contract_json": _contract_json(
            selected["offeringId"] if selected else None, "bind"
        ),
    }


def _bind_page(request, tenant, preset, error="", values=None, status=200):
    insts = _instances(tenant)
    p = next((i for i in insts if i["id"] == preset), None)
    crumbs = (
        _crumbs(tenant)
        + (
            [{"label": p["name"], "href": f"/instances/show/{preset}?tenant={tenant}"}]
            if p
            else []
        )
        + [{"label": "Create binding", "href": None}]
    )
    return _page(
        request,
        "Create binding",
        "core/bind_page.html",
        "form",
        _bind_form_ctx(tenant, insts, preset, error, values),
        crumbs,
        status,
    )


@require_GET
def bind_new(request):
    return _bind_page(request, request.tenant, request.GET.get("instance"))


@require_GET
def bind_freeinputs(request):
    # instance-select change fragment: re-renders the hint + validation
    # contract from the selected instance's offering
    return render(
        request,
        "core/bind_freeinputs.html",
        _bind_form_ctx(
            request.tenant,
            _instances(request.tenant),
            request.GET.get("instanceId") or "",
        ),
    )


def bind_post(request):
    tenant = request.tenant
    body = request.POST
    name, instance_id = (body.get(k, "") for k in ("name", "instanceId"))
    try:
        if not name or not instance_id:
            raise FormError("name and instance are required")
        params = _form_params(body, "Binding")
        inst = _find_instance(tenant, instance_id)
        if not inst:
            raise FormError("instance not found — pick one from the list")
        checked = _checked(validate_bind_params(inst["offeringId"], params))
        slug = kube.binding_slug(name)
        if kube.kube:
            kube.ensure_namespace(tenant)
            kube.create_binding(
                tenant, slug["id"], instance_id, slug["displayName"], checked or {}
            )
        else:
            memory.create_binding(tenant, str(name).strip(), instance_id)
    except Exception as err:
        return _bind_page(
            request,
            tenant,
            str(instance_id or ""),
            str(err) if isinstance(err, FormError) else _kube_err_message(err),
            {
                "name": str(name or ""),
                "instanceId": str(instance_id or ""),
                "parameters": (body.get("parameters") or "").strip(),
            },
            status=400,
        )
    return redirect(f"/instances/show/{instance_id}?tenant={tenant}#bindings")


bind_dispatch = _dispatch(bind_new, bind_post)


@require_GET
def binding_credentials(request, binding_id):
    def _creds_err(message):
        return render(request, "core/credentials.html", {"message": message})

    if not kube.kube:
        return _creds_err("credentials need the cluster (not available in memory mode)")
    if not (b := _find_binding(request.tenant, binding_id)):
        return _creds_err("binding not found")
    try:
        secret = kube.read_secret(request.tenant, b["secretName"])
    except Exception as err:
        if _is404(err):
            return _creds_err("credentials not ready yet (binding still in progress)")
        logger.error(
            "credentials read failed for %s/%s: %s", request.tenant, b["id"], err
        )
        return _creds_err(
            "credentials could not be read just now — click Reveal again"
        )  # transient kube errors → retryable banner, not a hard 500 leaving the panel blank
    creds = {
        k: base64.b64decode(v).decode("utf-8")
        for k, v in (secret.data or {}).items()
        if isinstance(v, str)
    }
    return render(
        request, "core/credentials.html", {"creds_json": json.dumps(creds, indent=2)}
    )


@require_POST
def binding_delete(request, binding_id):
    tenant = request.tenant
    b = _find_binding(tenant, binding_id)
    if b:
        if kube.kube:
            kube.delete_binding(tenant, binding_id)
        else:
            memory.delete_binding(tenant, binding_id)
        return redirect(
            f"/instances/show/{b['instanceId']}?tenant={tenant}&flash=deletion-started#bindings"
        )
    return redirect(f"/instances?tenant={tenant}")


@require_GET
def root(request):
    # home = the current namespace's instance list
    return redirect("/instances")
