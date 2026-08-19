from types import SimpleNamespace

from app import flyer_push_quality
from app import luna_member_coverage as coverage
from app import luna_resilient_strong_worker as worker


def test_notification_waits_for_exact_flyer_coverage(monkeypatch, tmp_path):
    path = tmp_path / "coverage.json"
    monkeypatch.setattr(coverage, "_store_path", lambda: path)

    coverage.ensure_pending(
        publication_id="netto-35",
        fingerprint="source-a",
        retailer="Netto",
        title="Netto uge 35",
    )
    record = {"publication_id": "netto-35", "fingerprint": "source-a"}
    assert coverage.notification_ready(record) is False

    row = coverage.update_snapshot(
        publication_id="netto-35",
        fingerprint="source-a",
        retailer="Netto",
        title="Netto uge 35",
        pages_remaining=0,
        pricing_remaining=0,
        member_fallback_remaining=0,
        hard_quarantined=0,
    )
    assert row["status"] == "complete"
    assert coverage.notification_ready(record) is True

    # A changed source generation must earn fresh coverage before a new push.
    assert coverage.notification_ready(
        {"publication_id": "netto-35", "fingerprint": "source-b"}
    ) is False


def test_degraded_coverage_is_terminal_without_blocking_flyer_forever(monkeypatch, tmp_path):
    path = tmp_path / "coverage.json"
    monkeypatch.setattr(coverage, "_store_path", lambda: path)

    row = coverage.update_snapshot(
        publication_id="lidl-35",
        fingerprint="source-a",
        retailer="Lidl",
        title="Lidl uge 35",
        pages_remaining=0,
        pricing_remaining=0,
        member_fallback_remaining=0,
        hard_quarantined=1,
    )
    assert row["status"] == "degraded"
    assert coverage.notification_ready(
        {"publication_id": "lidl-35", "fingerprint": "source-a"}
    ) is True


def test_quality_push_filters_pending_records(monkeypatch):
    rows = [
        {"publication_id": "ready", "fingerprint": "a"},
        {"publication_id": "pending", "fingerprint": "b"},
    ]
    monkeypatch.setattr(
        flyer_push_quality,
        "notification_ready",
        lambda row: row["publication_id"] == "ready",
    )
    assert flyer_push_quality._quality_ready_records(rows) == [rows[0]]


def test_budget_policy_sets_approved_100_dkk_once(monkeypatch):
    saved = []
    monkeypatch.setattr(
        worker,
        "load_config",
        lambda: {
            "monthly_budget_dkk": 20.0,
            "recommended_monthly_budget_dkk": 20.0,
        },
    )
    monkeypatch.setattr(
        worker,
        "save_config",
        lambda updates: saved.append(dict(updates)) or dict(updates),
    )

    result = worker.ensure_budget_policy()
    assert result["monthly_budget_dkk"] == 100.0
    assert result["page_audit_max_failures"] == 3
    assert worker.DEFAULT_FAILURE_ATTEMPTS == 3
    assert saved[0]["recommended_monthly_budget_dkk"] == 100.0
    assert saved[0]["page_audit_max_failures"] == 3
    assert saved[0]["kurv_budget_policy_version"] == worker.BUDGET_POLICY_VERSION


def test_budget_policy_respects_later_operator_change(monkeypatch):
    saved = []
    config = {
        "monthly_budget_dkk": 75.0,
        "recommended_monthly_budget_dkk": 100.0,
        "page_audit_max_failures": 4,
        "kurv_budget_policy_version": worker.BUDGET_POLICY_VERSION,
    }
    monkeypatch.setattr(worker, "load_config", lambda: dict(config))
    monkeypatch.setattr(worker, "save_config", lambda updates: saved.append(updates))

    assert worker.ensure_budget_policy() == config
    assert saved == []


def test_newest_ready_flyer_gets_coverage_priority(monkeypatch):
    older = SimpleNamespace(
        id="older",
        status="current",
        retailer="Netto",
        title="Netto uge 34",
        valid_from="13.08.2026",
        fingerprint="old",
    )
    newer = SimpleNamespace(
        id="newer",
        status="current",
        retailer="Netto",
        title="Netto uge 35",
        valid_from="20.08.2026",
        fingerprint="new",
    )
    monkeypatch.setattr(worker, "publication_is_ready", lambda publication: True)
    monkeypatch.setattr(worker, "publication_fingerprint", lambda publication: publication.fingerprint)
    monkeypatch.setattr(
        worker.coverage,
        "get",
        lambda publication_id, fingerprint: {"status": "pending"},
    )

    assert worker._coverage_focus([older, newer]) is newer
