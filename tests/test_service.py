"""The service: the output schema over HTTP, and errors that are typed.

A bad configuration name is the caller's mistake and gets a 400. Anything else is our
bug and gets a 500, not a run status wearing a disguise.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.schemas import HealthResponse, RunOutput
from service.app import VERSION, create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIGS = REPO_ROOT / "configs"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        config_root=CONFIGS,
        trace_dir=tmp_path / "traces",
        notes_root=tmp_path / "notes",
    )
    with TestClient(app) as test_client:
        yield test_client


def test_healthz_answers_the_schema(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    health = HealthResponse.model_validate(response.json())
    assert health.status == "ok"
    assert health.version == VERSION
    assert health.uptime_s >= 0
    assert health.model_reachable is True
    assert health.tools_loaded >= 1


def test_run_answers_the_output_schema(client: TestClient) -> None:
    response = client.post("/run", json={"task": "What is 21 * 2?", "config": "default"})
    assert response.status_code == 200
    output = RunOutput.model_validate(response.json())
    assert output.status == "ok"
    assert output.output == "21 * 2 is 42."
    assert output.trace_id


def test_run_records_a_trace(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/run", json={"task": "go", "config": "default"})
    trace_id = RunOutput.model_validate(response.json()).trace_id
    assert (tmp_path / "traces" / f"{trace_id}.jsonl").is_file()


def test_an_unknown_configuration_is_a_client_error(client: TestClient) -> None:
    response = client.post("/run", json={"task": "go", "config": "no-such-config"})
    assert response.status_code == 400
    assert "no such configuration" in response.json()["detail"]


def test_a_malformed_request_is_rejected(client: TestClient) -> None:
    response = client.post("/run", json={"config": "default"})
    assert response.status_code == 422


def test_an_extra_request_field_is_rejected(client: TestClient) -> None:
    """Contracts are closed: an unexpected field is a violation, not a payload to tolerate."""
    response = client.post("/run", json={"task": "go", "config": "default", "surprise": True})
    assert response.status_code == 422


def test_a_guardrail_refusal_is_still_a_successful_response(client: TestClient) -> None:
    """A refusal is a feature, so it is a 200 carrying status=refused."""
    response = client.post("/run", json={"task": "   ", "config": "default"})
    assert response.status_code == 200
    output = RunOutput.model_validate(response.json())
    assert output.status == "refused"
    assert output.failure_classes == ["guardrail_trip"]


def test_configs_are_listed(client: TestClient) -> None:
    response = client.get("/configs")
    assert response.status_code == 200
    assert "default" in response.json()["configs"]


def test_a_side_effect_is_refused_without_a_token(client: TestClient) -> None:
    response = client.post("/run", json={"task": "save a note", "config": "stateful_notes"})
    output = RunOutput.model_validate(response.json())
    assert output.status == "refused"
    assert output.failures[0].guardrail == "confirmation_missing"
