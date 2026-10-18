from gateway.limits import ConcurrencyGate, FailedAuthLimiter, SlidingWindow


def test_sliding_window_blocks_then_recovers():
    w = SlidingWindow(60)
    assert [w.hit("k", 3, now=t).allowed for t in (0, 1, 2)] == [True, True, True]
    blocked = w.hit("k", 3, now=10)
    assert not blocked.allowed and blocked.remaining == 0
    assert round(blocked.reset_s) == 50
    assert blocked.headers()["Retry-After"] == "50"
    assert w.hit("k", 3, now=60.5).allowed  # first hit expired
    assert w.hit("other", 3, now=10).allowed  # keys are independent


def test_failed_auth_limiter():
    f = FailedAuthLimiter(per_minute=2)
    assert not f.is_blocked("1.2.3.4")
    f.record("1.2.3.4")
    f.record("1.2.3.4")
    assert f.is_blocked("1.2.3.4")
    assert not f.is_blocked("5.6.7.8")


def test_per_key_concurrency():
    g = ConcurrencyGate()
    assert g.try_acquire_key(1, 2) and g.try_acquire_key(1, 2)
    assert not g.try_acquire_key(1, 2)
    g.release_key(1)
    assert g.try_acquire_key(1, 2)
    assert g.snapshot() == {"per_key_in_flight": {1: 2}}
