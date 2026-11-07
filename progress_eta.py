"""Recent batch throughput, without carrying a fast cache pass into the ETA."""
from collections import deque
from time import monotonic


def duration(seconds):
    seconds = max(0, round(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class ScanETA:
    WINDOW_SECONDS = 10
    REFRESH_SECONDS = 5

    def __init__(self):
        self.started = self.stage_started = self.updated = monotonic()
        self.stage = None
        self.done = self.total = 0
        self.samples = deque([(self.started, 0)])
        self.estimated_at = self.started
        self.seconds_per_file = None

    def update(self, stage, done, total):
        now = monotonic()
        if stage != self.stage or done < self.done:
            # The preceding stage ended on its final progress callback. Include
            # the first batch's processing time instead of starting at that batch.
            self.stage_started = self.updated
            self.stage = stage
            self.samples = deque([(self.stage_started, 0)])
            self.estimated_at = self.stage_started
            self.seconds_per_file = None
        self.updated = now
        self.done, self.total = done, total
        if now == self.samples[-1][0]:
            self.samples[-1] = (now, done)
        else:
            self.samples.append((now, done))
        self._trim(now)

    def _trim(self, now):
        # Keep the one point before the window for interpolation of a batch
        # that straddles its boundary. Sparse, slow batches remain measurable.
        cutoff = now-self.WINDOW_SECONDS
        while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
            self.samples.popleft()

    def _refresh_rate(self, now):
        if now-self.estimated_at < self.REFRESH_SECONDS:
            return
        self.estimated_at = now
        self._trim(now)
        start = max(self.stage_started, now-self.WINDOW_SECONDS)
        first_time, first_done = self.samples[0]
        if first_time < start and len(self.samples) > 1:
            next_time, next_done = self.samples[1]
            fraction = (start-first_time)/(next_time-first_time)
            baseline = first_done+(next_done-first_done)*fraction
        else:
            baseline = first_done
        # Time since the last completed batch also belongs to the window. If
        # nothing has advanced in it, avoid extrapolating an obsolete fast rate.
        processed = self.done-baseline
        self.seconds_per_file = (now-start)/processed if processed > 0 else None

    def text(self):
        now = monotonic()
        elapsed = duration(now-self.started)
        self._refresh_rate(now)
        if self.total > 0 and self.done >= self.total:
            remaining = "этап завершён"
        elif self.seconds_per_file is not None:
            estimate = (self.total-self.done)*self.seconds_per_file
            remaining = "≈ "+duration(estimate)
        else:
            remaining = "считаем…"
        return f"Прошло {elapsed} · Осталось на этапе: {remaining}"
