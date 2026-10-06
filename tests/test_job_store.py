"""job_store.py — state machine, TTL, idempotency, queue-full (spec §7)."""

import time

from job_store import JobStore, JobStatus, QueueFull, JobStateError


def _store(**kw):
    return JobStore(ttl_seconds=kw.get("ttl", 3600),
                    idempotency_window_seconds=kw.get("idem", 600),
                    max_queue_size=kw.get("q", 2))


def test_create_returns_queued_record():
    s = _store(); r = s.create(idempotency_key=None)
    assert r.status == JobStatus.QUEUED and r.result is None
    ...


def test_idempotency_window_dedups():
    s = _store()
    a = s.create(idempotency_key="K")
    b = s.create(idempotency_key="K")
    assert a.job_id == b.job_id
    # and: different key -> different job_id
    ...


def test_idempotency_key_expires_after_window(monkeypatch):
    # monkeypatch time.time ahead by window+1 -> new job_id for same key
    ...


def test_state_transitions_illegal_jump_raises():
    s = _store(); r = s.create(idempotency_key=None); s.start(r.job_id)
    # extracting -> summarizing (skipping analyzing) -> JobStateError
    ...


def test_complete_stores_result_and_finish_ts():
    s = _store(); r = s.create(idempotency_key=None); s.start(r.job_id)
    s.complete(r.job_id, {"a": 1})
    rec = s.get(r.job_id)
    assert rec.status == JobStatus.COMPLETED and rec.result == {"a": 1} \
       and rec.finished_at is not None
    ...


def test_fail_stores_typed_error():
    s = _store(); r = s.create(idempotency_key=None)
    s.fail(r.job_id, code="no_frames", message="ffmpeg failed")
    rec = s.get(r.job_id)
    assert rec.error_code == "no_frames" and rec.status == JobStatus.FAILED
    ...


def test_ttl_expiry_returns_none():
    s = _store(ttl=0)  # everything terminal is immediately expired
    r = s.create(idempotency_key=None); s.complete(r.job_id, {"x": 1})
    assert s.get(r.job_id) is None
    ...


def test_queue_full_signal():
    s = _store(q=1)
    s.reserve_slot(); r = s.create(idempotency_key=None)
    try:
        s.reserve_slot()
    except QueueFull:
        pass
    else:
        raise AssertionError("expected QueueFull")
    ...


def test_to_public_dict_shape():
    s = _store(); r = s.create(idempotency_key=None)
    d = s.to_public_dict(r)
    # contains job_id/status/created_at/status_url?; NO idempotency_key leaked
    assert "idempotency_key" not in d and d["job_id"] == r.job_id
    ...
