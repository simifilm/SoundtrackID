"""A fake HTTP API for the lookup tests: canned responses, no network."""

import httpx


def mock_client(routes: dict):
    """An httpx.Client that answers from `routes`, keyed "host/path".

    A value is a JSON body (status 200), a status code, an exception to raise,
    or a function of the request that returns one of those. Unknown URLs get
    404. Returns the client and the list of requests it received.
    """
    requests = []

    def handler(request):
        requests.append(request)
        answer = routes.get(request.url.host + request.url.path, 404)
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json=answer)

    return httpx.Client(transport=httpx.MockTransport(handler)), requests
