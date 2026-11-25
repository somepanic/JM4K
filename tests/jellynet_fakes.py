"""
A stand-in for requests.Session: hands out canned responses in order and records every call, so jellynet tests
run without a network or a Jellyfin server.
"""

import json as json_lib

from requests.structures import CaseInsensitiveDict


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None, raw=None):
        self.status_code = status
        self._body = body
        self._raw = raw
        self.headers = CaseInsensitiveDict(headers or {})
        if raw is not None:
            self.text = raw if isinstance(raw, str) else raw.decode('utf-8', errors='replace')
            self.content = raw.encode('utf-8') if isinstance(raw, str) else raw
        elif body is not None:
            if 'Content-Type' not in self.headers:
                self.headers['Content-Type'] = 'application/problem+json' if status >= 400 else 'application/json'
            encoded = json_lib.dumps(body)
            self.text = encoded
            self.content = encoded.encode('utf-8')
        else:
            self.text = ''
            self.content = b''

    def json(self):
        if self._raw is not None or self._body is None:
            raise ValueError('no JSON body')
        return self._body


def problem(status, title='', detail='', headers=None):
    """An ASP.NET Core ProblemDetails response the way Jellyfin returns one."""
    body = {
        'type': f'https://tools.ietf.org/html/rfc9110#section-15.{status}',
        'title': title,
        'status': status,
    }
    if detail:
        body['detail'] = detail
    return FakeResponse(status, body, headers)


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def close(self):
        self.closed = True

    def queue(self, *responses):
        self.responses.extend(responses)

    def put(self, url, **kwargs):
        return self.request('PUT', url, **kwargs)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(
            {
                'method': method,
                'url': url,
                'params': params,
                'json': json,
                'headers': dict(headers or {}),
                'timeout': timeout,
            }
        )
        if not self.responses:
            raise AssertionError(f'unexpected request: {method} {url}')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
