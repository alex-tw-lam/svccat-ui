"""Shared fixtures: every test runs on the in-memory backend with a local
admin session and one tenant (the same quickstart env REQUIREMENTS §12
promises), against a throwaway in-process catalog (memory resets per test via
_clear)."""

import os
import unittest
from unittest import mock

from core import memory


def dev_env(**extra):
    env = {"ADMIN_PASSWORD": "devpass", "ALLOWED_TENANTS": "team-a,team-b"}
    env.update(extra)
    return env


class MemoryCase(unittest.TestCase):
    """In-memory backend + a signed-in local admin client."""

    def setUp(self):
        # isolate the dev catalog per test (the module store is global)
        self._mem, self._events = (
            {k: dict(v) for k, v in memory._MEM.items()},
            list(memory._EVENTS),
        )
        env = dev_env(**getattr(self, "env_extra", {}))
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

        from django.test import Client

        self.client = Client()
        # sign out first: Django's test client persists cookies across tests
        # in the same process, and a signed-in visitor is bounced off /auth/*
        fresh = Client()
        resp = fresh.post("/auth/local", {"username": "admin", "password": "devpass"})
        self.assertEqual(resp.status_code, 302, "local admin login must succeed")
        self.client.cookies.update(fresh.cookies)
        # an anonymous client for the sign-in page itself
        self.anon = Client()

    def tearDown(self):
        memory._MEM.clear()
        memory._MEM.update(self._mem)
        memory._EVENTS[:] = self._events
