"""Cache only Google's public certificate responses, following their HTTP cache headers."""

import threading

import requests
from cachecontrol import CacheControl
from google.auth.transport.requests import Request


class GoogleCertificateRequest:
    """Serialize a shared cache/session; JWTs and verified user records are never cached."""

    def __init__(self):
        self._session = CacheControl(requests.Session())
        self._request = Request(session=self._session)
        self._lock = threading.Lock()

    def __call__(self, url, method="GET", body=None, headers=None, **_kwargs):
        # google-auth uses this transport solely for public certificate GETs.
        with self._lock:
            return self._request(url, method=method, body=body, headers=headers, timeout=5)


certificate_request = GoogleCertificateRequest()
