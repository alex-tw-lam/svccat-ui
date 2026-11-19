"""Settings for svccat-ui: server-rendered Django templates + htmx fragment swaps + Alpine.js client sprinkles + a vendored prebuilt DaisyUI css (the "PyHAT" stack). No database — sessions are signed cookies, catalog state lives in Kubernetes."""

import importlib.util
import os

SECRET_KEY = (
    os.environ.get("SESSION_SECRET") or "dev-insecure-session-secret"
)  # SESSION_SECRET must survive pod restarts (set via env); a random fallback would log everyone out on every restart — dev only
DEBUG = os.environ.get("DJANGO_DEBUG") == "1"  # dev pages/tooling; §10 visible 500s come from TenantMiddleware, not DEBUG
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.staticfiles",  # WhiteNoise: serves core/public at STATIC_URL="/"
    "template_partials",
    "django_htmx",  # request.htmx for the fragment/partial branches
    "core",
]  # signed-cookie sessions; partials wrap the loaders for inline {% partialdef %} fragments
# dev-only apps: absent in the production image (uv sync --no-dev), so the
# find_spec probe skips them there without an extra env var
INSTALLED_APPS += [
    app
    for app in ("django_extensions",)
    if importlib.util.find_spec(app.replace("-", "_"))
]
if DEBUG and importlib.util.find_spec("debug_toolbar"):
    INSTALLED_APPS.append("debug_toolbar")
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # static files never reach the tenant gate; misses fall through to a clean 404
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "core.middleware.TenantMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]
if DEBUG and importlib.util.find_spec("debug_toolbar"):
    MIDDLEWARE.insert(1 + 2, "debug_toolbar.middleware.DebugToolbarMiddleware")  # after CommonMiddleware, before the tenant gate
# NOTE: no CsrfViewMiddleware by design — mutations are session-gated server-side, session cookie SameSite=Lax
ROOT_URLCONF = "service_manager.urls"
STATIC_URL = "/s/"  # asset URLs (/s/app.js, /s/daisyui.css) — WhiteNoise serves them
MEDIA_URL = "/media/"  # unused (no uploads); must sit outside STATIC_URL
STATICFILES_DIRS = [os.path.join(os.path.dirname(os.path.dirname(__file__)), "core", "public")]
STATIC_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "staticfiles")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"
    },  # collectstatic precompresses .gz (gunicorn has no compress layer)
}
WHITENOISE_MAX_AGE = 0  # unhashed first-party URLs: revalidate every time, same contract the old view had
INTERNAL_IPS = ["127.0.0.1"]  # debug toolbar panels
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
