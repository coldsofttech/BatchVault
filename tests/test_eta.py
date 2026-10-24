from batchvault.eta import MIN_SECONDS, EtaEstimator, format_duration


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_format_duration():
    assert format_duration(3725) == "1:02:05"
    assert format_duration(-5) == "0:00:00"


def test_no_estimate_before_warmup():
    clock = FakeClock()
    eta = EtaEstimator(clock)
    eta.start()
    clock.now = MIN_SECONDS - 1
    assert eta.estimate(10, 100) is None


def test_estimate_uses_average_speed():
    clock = FakeClock()
    eta = EtaEstimator(clock)
    eta.start()
    clock.now = 10
    assert eta.estimate(50, 100) == 10
