# REQUIREMENTS — the binding contract

This repo is under an aggressive line-reduction campaign (goal: **100 lines** — aspirational; shrink
monotonically every run). This file is the binding contract for that campaign:

- **Every requirement below must continue to exist and work** after any change. Features may be
  reimplemented (smaller, via libraries) but never dropped or weakened.
- **Tests are subordinate** — a change that breaks a test fixes the test, never bends the code back
  to it; the requirements list is the acceptance bar.
- Docs may be reorganized, merged or tightened freely; prose duplicating this file goes — this file wins.

Incorporated by reference (normative): `README.md` (Configuration table, Keycloak client setup, RBAC requirements),
`core/public/README.md` (vendored asset slots and exact versions). `docs/design.md` and `docs/architecture.md` are
descriptive, NOT binding — the binding design constraints are §9; file layout may change if the requirements hold.

## Metric

Lines = every git-tracked file except `uv.lock` (generated) and `LINE-REDUCTION.md` (this campaign's log):

```sh
git ls-files | grep -vE '^(uv\.lock|LINE-REDUCTION\.md)$' | xargs wc -l | tail -1
```

The gitignored vendored assets `core/public/htmx.min.js`, `alpine.min.js`, `daisyui.css`, `daisyui.css.gz` never count.

## Product requirements (all must exist)

1. **Identity** — OIDC Authorization Code + PKCE (S256), server-side against Keycloak; browser never sees a token;
   session = httpOnly signed cookie `sm_session`, 12 h, SameSite=Lax. Local admin login: `ADMIN_USER`/`ADMIN_PASSWORD`
   env (constant-time compare; failures log username + client address). The sign-in page always shows the Keycloak
   button; admin form only when the password env is set; signed-in visitors are redirected off the sign-in page.
2. **Tenancy** — allowed tenant namespaces derived from token groups, filtered by `ALLOWED_TENANTS`; `FALLBACK_TENANTS`
   (explicit config, default none — fail closed) when the token yields none; the local admin sees every active
   namespace. The namespace switcher keeps the current path and query params.
3. **Catalog** — cluster + namespaced service classes browsable as a marketplace tile grid; a tile
   goes straight to that offering's provision form (plan choice happens there; no catalog-detail page).
4. **Provisioning** — provision form with service/plan select, instance name, and an advanced drawer
   for raw-JSON parameters with live validation; change plan (edit); delete with confirm + deprovision.
5. **Instances** — per-namespace list with status badges, bindings count, latest event, 3 s poll while
   in-flight; detail page with parameters, breadcrumbs, an events timeline (newest first, capped at 20) and a Manage link when the broker reports a dashboard_url.
6. **Bindings** — created/revealed/deleted on the instance detail page (hierarchy: namespace → instance → binding);
   credentials revealed by an explicit htmx swap, never preloaded, disabled until ready; no global bindings page.
7. **Backends** — Kubernetes backend (Service Catalog CRs; namespaces auto-created on first tenant touch together with
   the `service-manager-binding-secrets` RoleBinding; DNS-label slug names + `service-manager.io/*` display-name
   annotation) and an in-memory dev fallback (static catalog, per-tenant lists, async completion ~4 s, simulated events)
   implementing the same verbs behind one seam.
8. **Input contract** — computed > plan > user, platform-pinned; one server-side validator mirrored by the client gate
   (server re-checks everything the client allows); form-schema modal viewer; free-input filtering.
9. **Design system** — DaisyUI 4 prebuilt + the app.css overlay; no node toolchain, no build step, no SPA; WCAG AA
   badge/muted contrast; single 720 px breakpoint. `docs/design.md` is descriptive; templates are the source of truth.
10. **Serving** — no database; the app serves `/app.js`, `/app.css` and the vendored slots itself (clean 404 for absent
    vendored files; `.gz` variant honored with `Content-Encoding: gzip`); `Cache-Control: no-store` on pages; visible
    500s; trusts `X-Forwarded-Proto`; `ALLOWED_HOSTS = *`; no CSRF layer (mutations are session-gated server-side).
11. **Config** — every variable in README's Configuration table is read with the documented meaning
    and default: `SESSION_SECRET`, `EXTERNAL_URL`, `KC_URL`, `KC_REALM`, `KC_CLIENT_ID`,
    `ADMIN_USER`, `ADMIN_PASSWORD`, `KUBERNETES_SERVICE_HOST`, `KUBE_IN_CLUSTER`, `POD_NAMESPACE`,
    `CLUSTER_NAME`, `ALLOWED_TENANTS`, `FALLBACK_TENANTS`.
12. **Runs** — `uv sync` then `manage.py runserver` boots with the memory backend; with the quickstart
    env (`ADMIN_PASSWORD` + `ALLOWED_TENANTS`) the UI is fully browsable; `manage.py check` passes on
    a fresh clone (vendored assets absent); the Docker image runs gunicorn on :8080.

## Campaign rules (for the scheduled reducer)

- Prefer libraries over custom code — Django built-ins, stdlib, existing dependencies, or a new
  dependency via `uv add` whenever it nets fewer lines. Hunt these opportunities first.
- Each change: verify (`uv run python manage.py check` + boot smoke on the memory backend), commit with a message noting
  the lines saved, append the run to `LINE-REDUCTION.md` (before/after, what changed).
- Never rewrite history, never push, never create/modify automations, never weaken or delete this
  contract. `REQUIREMENTS.md` prose may be tightened but no requirement may be removed.
