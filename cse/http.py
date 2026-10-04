"""Polite, read-only client for the undocumented JSON API behind cse.lk.

Rules (from the brief and docs/API_NOTES.md): sequential requests, a pause between calls,
a descriptive User-Agent, 3 retries with exponential backoff, 30-second timeouts.
Parameters are form-encoded. A handful of endpoints only accept GET.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests

BASE_URL = "https://www.cse.lk/api/"
USER_AGENT = "cse-terminal/0.1 (personal research dashboard; +https://github.com/MinimumLizard/CSE)"
GET_ENDPOINTS = frozenset({
    "allSecurityCode", "cntSecurity", "lastUpdateTime", "previousUpdateTime",
    "corporateAnnouncementCategory", "returnAspiSnp",
})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class ApiError(RuntimeError):
    """The API could not be reached or returned something unusable."""


@dataclass(frozen=True)
class Response:
    endpoint: str
    params: dict[str, Any]
    status: int
    raw: bytes          # untouched body, written to data/raw/
    data: Any           # parsed JSON, or None for 204 No Content


class CseClient:
    def __init__(self, pause: float = 1.0, retries: int = 3, timeout: float = 30.0,
                 session: requests.Session | None = None, sleep=time.sleep):
        self.pause = pause
        self.retries = retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._sleep = sleep
        self.calls = 0

    def call(self, endpoint: str, **params: Any) -> Response:
        method = "GET" if endpoint in GET_ENDPOINTS else "POST"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(2 ** attempt)  # 2, 4, 8 s
            try:
                if method == "GET":
                    r = self.session.get(BASE_URL + endpoint, timeout=self.timeout)
                else:
                    r = self.session.post(BASE_URL + endpoint, data=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                continue
            finally:
                self.calls += 1
                self._sleep(self.pause)
            if r.status_code in RETRY_STATUSES:
                last_error = ApiError(f"{endpoint}: HTTP {r.status_code}")
                continue
            if r.status_code == 204:
                return Response(endpoint, params, 204, b"", None)
            if r.status_code != 200:
                raise ApiError(f"{endpoint} {params}: HTTP {r.status_code}: {r.text[:200]}")
            try:
                data = json.loads(r.content)
            except ValueError as exc:
                raise ApiError(f"{endpoint} {params}: response is not JSON") from exc
            return Response(endpoint, params, 200, r.content, data)
        raise ApiError(f"{endpoint} {params}: failed after {self.retries} retries: {last_error}")
