"""
A stand-in for requests.Session: hands out canned responses in order and records every call, so silonet tests
run without a network or a Silo server.
"""

from requests.structures import CaseInsensitiveDict


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = CaseInsensitiveDict(headers or {})
        if body is not None and 'Content-Type' not in self.headers:
            self.headers['Content-Type'] = 'application/problem+json' if status >= 400 else 'application/json'
        self.content = b'' if body is None else b'{}'

    def json(self):
        if self._body is None:
            raise ValueError('no body')
        return self._body


def problem(status, name, detail='', headers=None):
    """A Problem Details response the way Silo sends one: the type's last path segment names the problem."""
    body = {
        'type': 'https://siloserver.org/problems/' + name,
        'title': name.replace('_', ' '),
        'status': status,
        'detail': detail,
        'instance': 'urn:silo:request:test',
    }
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

    def put(self, url, **kwargs):  # plexnet passes requests' session methods along (method=server.session.put)
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
