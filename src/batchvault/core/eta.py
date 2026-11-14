import time

# Wait for this much real run time before trusting the measured speed.
MIN_SECONDS = 5.0


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


class EtaEstimator:
    """ETA from the average speed of the whole run so far.

    Speed is bytes completed divided by the time since the first file started,
    so per-file overhead is included.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started_at = None
        self.skipped = 0  # bytes finished before this run (a resumed run)

    def start(self):
        if self.started_at is None:
            self.started_at = self.clock()

    def skip(self, size):
        """Count work an earlier run did, so it does not inflate the speed."""
        self.skipped += size

    def estimate(self, done_bytes, total_bytes):
        """Seconds remaining, or None while there is not enough data yet."""
        done_bytes -= self.skipped
        total_bytes -= self.skipped
        if self.started_at is None or done_bytes <= 0:
            return None

        elapsed = self.clock() - self.started_at
        if elapsed < MIN_SECONDS:
            return None

        remaining = max(0, total_bytes - done_bytes)
        return remaining / (done_bytes / elapsed)
