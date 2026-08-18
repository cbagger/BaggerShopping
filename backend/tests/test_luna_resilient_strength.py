import json
import os
from types import SimpleNamespace

from app import luna_resilient_strong_worker as worker


def test_strength_defaults_preserve_previous_luna_breadth():
    assert os.environ["LUNA_RESILIENT_MAX_REQUESTS_PER_CYCLE"] == "20"
    assert os.environ["LUNA_RESILIENT_MAX_PAGE_AUDITS_PER_CYCLE"] == "10"
    assert os.environ["LUNA_RESILIENT_MAX_PRICING_CROPS_PER_CYCLE"] == "10"
    assert os.environ["LUNA_RESILIENT_MAX_FALLBACK_PER_CYCLE"] == "20"
    assert os.environ["LUNA_RESILIENT_MAX_VARIANT_CROPS_PER_CYCLE"] == "5"


def test_unresolved_work_gets_one_retry_before_quarantine(monkeypatch, tmp_path):
    retry_path = tmp_path / "retry.json"
    monkeypatch.setattr(worker, "_retry_path", lambda: retry_path)
    monkeypatch.setenv("LUNA_RESILIENT_FAILURE_ATTEMPTS", "2")

    quarantined = []
    monkeypatch.setattr(
        worker,
        "_original_quarantine",
        lambda kind, publication, candidate, reason: quarantined.append(
            (kind, publication.id, candidate.fingerprint, reason)
        ),
    )
    monkeypatch.setattr(
        worker.base,
        "_quarantine_key",
        lambda kind, publication, candidate: (
            f"{kind}|{publication.id}|{publication.generation}|{candidate.fingerprint}"
        ),
    )
    monkeypatch.setattr(
        worker.base,
        "_source_fingerprint",
        lambda publication: publication.generation,
    )
    monkeypatch.setattr(
        worker.base,
        "_candidate_fingerprint",
        lambda candidate: candidate.fingerprint,
    )

    publication = SimpleNamespace(id="lidl-34", generation="source-a")
    candidate = SimpleNamespace(fingerprint="crop-1")

    worker._bounded_quarantine("pricing", publication, candidate, "temporary-failure")
    assert quarantined == []

    worker._bounded_quarantine("pricing", publication, candidate, "temporary-failure")
    assert quarantined == [
        ("pricing", "lidl-34", "crop-1", "temporary-failure")
    ]

    payload = json.loads(retry_path.read_text("utf-8"))
    assert next(iter(payload["items"].values()))["attempts"] == 2


def test_retry_budget_is_scoped_to_new_source_generation(monkeypatch, tmp_path):
    retry_path = tmp_path / "retry.json"
    monkeypatch.setattr(worker, "_retry_path", lambda: retry_path)
    monkeypatch.setenv("LUNA_RESILIENT_FAILURE_ATTEMPTS", "2")

    quarantined = []
    monkeypatch.setattr(
        worker,
        "_original_quarantine",
        lambda kind, publication, candidate, reason: quarantined.append(publication.generation),
    )
    monkeypatch.setattr(
        worker.base,
        "_quarantine_key",
        lambda kind, publication, candidate: (
            f"{kind}|{publication.id}|{publication.generation}|{candidate.fingerprint}"
        ),
    )
    monkeypatch.setattr(
        worker.base,
        "_source_fingerprint",
        lambda publication: publication.generation,
    )
    monkeypatch.setattr(
        worker.base,
        "_candidate_fingerprint",
        lambda candidate: candidate.fingerprint,
    )

    candidate = SimpleNamespace(fingerprint="crop-1")
    first = SimpleNamespace(id="lidl", generation="source-a")
    second = SimpleNamespace(id="lidl", generation="source-b")

    worker._bounded_quarantine("pricing", first, candidate, "ambiguous")
    worker._bounded_quarantine("pricing", second, candidate, "ambiguous")

    assert quarantined == []
    payload = json.loads(retry_path.read_text("utf-8"))
    assert len(payload["items"]) == 2


def test_deterministic_unit_equivalence_never_spends_retry_budget(monkeypatch, tmp_path):
    retry_path = tmp_path / "retry.json"
    monkeypatch.setattr(worker, "_retry_path", lambda: retry_path)

    quarantined = []
    monkeypatch.setattr(
        worker,
        "_original_quarantine",
        lambda kind, publication, candidate, reason: quarantined.append(reason),
    )

    publication = SimpleNamespace(id="lidl")
    candidate = SimpleNamespace(fingerprint="crop-1")

    worker._bounded_quarantine(
        "pricing",
        publication,
        candidate,
        "deterministic-provider-unit-equivalence",
    )

    assert quarantined == ["deterministic-provider-unit-equivalence"]
    assert not retry_path.exists()


def test_strength_policy_is_explicit_not_import_side_effect(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(worker.base, "_quarantine", sentinel)

    assert worker.base._quarantine is sentinel
    worker.install_strength_policy()
    assert worker.base._quarantine is worker._bounded_quarantine
