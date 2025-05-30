import time
from abc import abstractmethod
from bisect import bisect_left

from pydantic.dataclasses import dataclass

from aiopslab.generators.workload.base import WorkloadManager

STREAM_WORKLOAD_TIMEOUT = 60 * 1.5  # 1.5 minutes
STREAM_WORKLOAD_EPS = 5  # 5 seconds


@dataclass
class WorkloadEntry:
    time: float  # Start time of the workload run
    number: int  # Number of requests generated in this workload run
    log: str  # Log of the workload run
    ok: bool  # Indicates if the workload was successful


class StreamWorkloadManager(WorkloadManager):
    """
    Stream-like workload manager
    """

    log_history: list[WorkloadEntry] = []

    def __init__(self):
        super().__init__()

        self.last_log_time = None

    @abstractmethod
    def retrievelog(self, start_time: float | None = None) -> list[WorkloadEntry]:
        """
        Retrieve new logs. Like a stream, it should return only new logs since the last retrieval.
        """

        raise NotImplementedError("Subclasses must implement this method.")

    def _extractlog(self):
        """
        Stream-like log extraction.
        """
        while True:
            # In case of byte limits
            new_logs = self.retrievelog(self.last_log_time)

            if not new_logs:
                return

            if not sorted(new_logs, key=lambda x: x.time):
                raise ValueError("Logs are not sorted by time.")

            first_greater = 0
            while first_greater < len(new_logs) and new_logs[first_greater].time <= self.last_log_time:
                first_greater += 1

            if first_greater < len(new_logs):
                self.log_history.extend(new_logs[first_greater:])
                self.last_log_time = new_logs[-1].time

    def collect(self, number=100, start_time=None):
        """
        Run the workload generator until collected data is sufficient.
        """
        current_time = time.time()
        if start_time is None:
            start_time = current_time
        if start_time > current_time:
            raise ValueError("start_time cannot be in the future")
        if current_time - start_time > STREAM_WORKLOAD_TIMEOUT:
            raise ValueError("start_time is too far in the past")

        start_entry = bisect_left(
            self.log_history,
            start_time,
            key=lambda x: x.time if isinstance(x, WorkloadEntry) else x,
        )
        end_entry = start_entry
        accumulated_logs = 0

        while time.time() - start_time < STREAM_WORKLOAD_TIMEOUT:
            self._extractlog()
            while end_entry < len(self.log_history):
                accumulated_logs += len(self.log_history[end_entry])
                end_entry += 1
            if accumulated_logs >= number:
                return self.log_history[start_entry:end_entry]
            time.sleep(3)

        raise TimeoutError("Workload generator did not collect enough data within the timeout period.")

    def recent_entries(self, duration=30):
        """
        Return recently collected data within the given duration (seconds).
        """
        start_time = time.time() - duration
        self._extractlog()
        start_entry = bisect_left(
            self.log_history,
            start_time,
            key=lambda x: x.time if isinstance(x, WorkloadEntry) else x,
        )
        return self.log_history[start_entry:]
