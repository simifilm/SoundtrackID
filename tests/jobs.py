"""Following a job the server started, as the page does."""

import json


def job_events(client, response) -> list[dict]:
    """The events of the job `response` (of /analyze, /rerun or /resume)
    started, from its start to its end; returns once the job ended."""
    assert response.status_code == 200, response.text
    r = client.get(f"/jobs/{response.json()['job_id']}/events")
    return [json.loads(e.removeprefix("data: ")) for e in r.text.split("\n\n") if e]
