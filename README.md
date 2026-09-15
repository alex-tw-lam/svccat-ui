# svccat-ui

A self-service catalog UI for Kubernetes Service Catalog (Dryic fork): browse service offerings,
provision/update/deprovision instances, create bindings and reveal their credentials — backed by
Service Catalog CRs, gated by OIDC login and per-user tenant namespaces. Classic **PyHAT** stack: server-rendered **Django**
templates + **htmx** fragment swaps + **Alpine.js** sprinkles + **DaisyUI** (vendored prebuilt css). Docs: `REQUIREMENTS.md`
(binding contract — read first) · `docs/design.md` · `docs/architecture.md` · `core/public/README.md` (vendored slots).

## Quickstart

```sh
uv sync  # creates .venv from the committed uv.lock
# Drop in vendored assets (core/public/README.md), then run; tenancy fails closed: use admin form + explicit tenants:
ADMIN_PASSWORD=dev ALLOWED_TENANTS=team-a,team-b uv run python manage.py runserver 8000
```

Docker (gunicorn on port 8080):

```sh
docker build -t svccat-ui .
docker run --rm -p 8080:8080 -e SESSION_SECRET=dev-secret -e EXTERNAL_URL=http://localhost:8080 svccat-ui
```

For a production image containing the assets, mount/copy them into `core/public/` before building
(see the commented `COPY --from=assets` line in the `Dockerfile`).

## Configuration (environment variables)

| Variable                  | Where                         | Default                        | Meaning                                                                                                                                                                                                                               |
| ------------------------- | ----------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SESSION_SECRET`          | `service_manager/settings.py` | `dev-insecure-session-secret`  | Django `SECRET_KEY` — signs the session cookie. **Set in production**; a random per-pod key would log everyone out on every restart.                                                                                                  |
| `EXTERNAL_URL`            | `core/views.py`               | request scheme/host            | Canonical external origin for OIDC redirect URIs behind a proxy; takes precedence over the request host.                                                                                                                              |
| `KC_URL`                  | `core/views.py`               | `https://keycloak.example.com` | Keycloak base URL; the issuer is derived as `${KC_URL}/realms/${KC_REALM}`.                                                                                                                                                           |
| `KC_REALM`                | `core/views.py`               | `platform`                     | Keycloak realm name.                                                                                                                                                                                                                  |
| `KC_CLIENT_ID`            | `core/views.py`               | `service-manager`              | OIDC client (public, PKCE).                                                                                                                                                                                                           |
| `ADMIN_USER`              | `core/views.py`               | `admin`                        | Local admin sign-in username.                                                                                                                                                                                                         |
| `ADMIN_PASSWORD`          | `core/views.py`               | _(unset)_                      | Enables the local admin sign-in form when set; unset = the sign-in page shows the Keycloak button only.                                                                                                                               |
| `KUBERNETES_SERVICE_HOST` | `core/kube.py`                | —                              | Standard in-cluster env; presence enables the Kubernetes backend.                                                                                                                                                                     |
| `KUBE_IN_CLUSTER`         | `core/kube.py`                | —                              | Set to `true` to force the Kubernetes backend: uses the in-cluster service-account config when available, otherwise falls back to the current kubectl context (logged at startup; for running against a real cluster from a dev box). |
| `POD_NAMESPACE`           | `core/kube.py`                | `service-manager`              | Namespace of this deployment's ServiceAccount (fallback when the SA token file is not mounted); used for the per-tenant secret-reader RoleBinding subjects.                                                                           |
| `CLUSTER_NAME`            | `core/views.py`               | `in-cluster`                   | Label shown in the topbar's Cluster chip.                                                                                                                                                                                             |
| `ALLOWED_TENANTS`         | `core/views.py`               | _(empty)_                      | Comma-separated tenant namespaces a login may see; empty means every valid namespace-name group in the token is allowed.                                                                                                              |
| `FALLBACK_TENANTS`        | `core/views.py`               | _(empty)_                      | Tenants granted when the token's groups yield no allowed tenant. Explicit config only — empty (the default) fails closed: such logins see no tenants.                                                                                 |

## Keycloak client setup

OIDC Authorization Code + PKCE (S256) **server-side** — the browser never sees a token; the session
is an httpOnly signed cookie (`sm_session`, 12 h, SameSite=Lax). In your Keycloak realm create (or reuse) a client:

- **Type**: public — PKCE means no client secret. **Client ID**: `KC_CLIENT_ID` (default `service-manager`).
- **Standard flow**: enabled; **Direct access grants**: off.
- **PKCE code challenge method**: S256 (enforced by the app; the client always sends a code_verifier).
- **Valid redirect URIs**: `${EXTERNAL_URL}/auth/callback` (trailing `/*` works too) · **Web origins**: your `EXTERNAL_URL` origin.

## Kubernetes RBAC requirements

Run as a ServiceAccount bound to a ClusterRole covering the Service Catalog CRs (group
`servicecatalog.k8s.io`, Dryic fork) plus the per-tenant secret reads. Essentials:

```yaml
{
  apiVersion: rbac.authorization.k8s.io/v1,
  kind: ClusterRole,
  metadata: { name: service-manager },
  rules:
    [
      {
        apiGroups: ["servicecatalog.k8s.io"],
        resources:
          [
            clusterserviceclasses,
            clusterserviceplans,
            serviceclasses,
            serviceplans,
          ],
        verbs: [get, list, watch],
      },
      {
        apiGroups: ["servicecatalog.k8s.io"],
        resources: [serviceinstances, servicebindings],
        verbs: [get, list, watch, create, update, patch, delete],
      },
      { apiGroups: [""], resources: [secrets], verbs: [get] },
      { apiGroups: [""], resources: [events], verbs: [get, list] },
      {
        apiGroups: [""],
        resources: [namespaces],
        verbs: [get, list, watch, create],
      },
      {
        apiGroups: [rbac.authorization.k8s.io],
        resources: [rolebindings],
        verbs: [get, create],
      },
      {
        apiGroups: [rbac.authorization.k8s.io],
        resources: [clusterroles],
        resourceNames: [service-manager-binding-secret-reader],
        verbs: [bind],
      },
    ],
}
```

On first touch of a tenant namespace the app creates a `service-manager-binding-secrets` RoleBinding there, pointing at the
shared `service-manager-binding-secret-reader` ClusterRole — the `bind` verb above lets the app grant that role to itself.
