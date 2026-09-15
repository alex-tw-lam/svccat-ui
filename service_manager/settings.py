"""Settings for svccat-ui: server-rendered Django templates + htmx fragment swaps + Alpine.js client sprinkles + a vendored prebuilt DaisyUI css (the "PyHAT" stack). No database — sessions are signed cookies, catalog state lives in Kubernetes."""

import os

SECRET_KEY = (
    os.environ.get("SESSION_SECRET") or "dev-insecure-session-secret"
)  # SESSION_SECRET must survive pod restarts (set via env); a random fallback would log everyone out on every restart — dev only
DEBUG = False
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = [
    "django.contrib.sessions",
    "template_partials",
    "core",
]  # signed-cookie sessions; partials wrap the loaders for inline {% partialdef %} fragments
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.middleware.TenantMiddleware",
]  # NOTE: no CsrfViewMiddleware by design — mutations are session-gated server-side, session cookie SameSite=Lax
ROOT_URLCONF = "service_manager.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": ["django.template.context_processors.request"]
        },
    }
]
DATABASES = {}  # No database: catalog/instances/bindings are Service Catalog CRs in the tenant namespaces; dev falls back to core/memory
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_NAME = "sm_session"
SESSION_COOKIE_AGE = 43200  # Server session: signed cookie, httpOnly, Lax — the browser never holds a token (12h)
SECURE_PROXY_SSL_HEADER = (
    "HTTP_X_FORWARDED_PROTO",
    "https",
)  # TLS terminates at the ingress; EXTERNAL_URL (core/views.py base_url) overrides the request scheme/host for redirects when set.
