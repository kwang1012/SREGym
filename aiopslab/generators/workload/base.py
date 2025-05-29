import random
import string
import threading
import time
from abc import ABC, abstractmethod
from bisect import bisect_left

from pydantic.dataclasses import dataclass

# Two types of workload generators:
# 1. Constantly running workload generator
# 2. Workload generator that runs for a fixed duration
# By repeating type 2, we can always assume it's constantly running.

# Two purposes:
# 1. To generate traces
# 2. Validation

TASKED_WORKLOAD_TIMEOUT = 60 * 1.5  # 1.5 minutes


@dataclass
class WorkloadEntry:
    time: float  # Start time of the workload run
    number: int  # Number of requests generated in this workload run
    log: str  # Log of the workload run
    ok: bool  # Indicates if the workload was successful


class WorkloadGenerator(ABC):
    """
    Constantly running workload generator.
    """

    def __init__(self):
        pass

    @abstractmethod
    def start(self, *args, **kwargs):
        """
        Start the workload generator.
        """
        pass

    @abstractmethod
    def stop(self, *args, **kwargs):
        """
        Stop the workload generator.
        """
        pass

    @abstractmethod
    def collect(self, number=100, start_time=None):
        """
        Run the workload generator until collected data is sufficient.
        - Number of requests should be at least `number` starting from `start_time`.
        - If `start_time` is not provided, it should start from the current time.
        """
        pass

    @abstractmethod
    def recent_entries(self, duration=30):
        """
        Return recently collected data within the given duration (seconds).
        """
        pass


class TaskedWorkloadGenerator(WorkloadGenerator):
    log_history = []

    def __init__(self):
        pass

    @abstractmethod
    def create_task(self, *args, **kwargs):
        """
        Create a task for the workload generator.
        """
        pass

    @abstractmethod
    def wait_until_complete(self, *args, **kwargs):
        """
        Wait until the task is complete.
        """
        pass

    @abstractmethod
    def retrievelog(self) -> WorkloadEntry:
        """
        Retrieve log from the last workload run.
        """
        pass

    def start(self):
        def _run(self):
            while not self.stop_event.is_set():
                self.create_task()
                start_time = time.time()
                self.wait_until_complete()
                entry = self.retrievelog()
                entry.time = start_time
                self.log_history.append(entry)

        tempid = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

        self.stop_event = threading.Event()
        self.job_monitor = threading.Thread(
            target=_run, args=(self,), name=f"workload_gen_{tempid}", daemon=True
        )
        self.job_monitor.start()

    def stop(self):
        self.stop_event.set()
        self.job_monitor.join()

    def collect(self, number=100, start_time=None):
        """
        Run the workload generator until collected data is sufficient.
        """
        current_time = time.time()
        if start_time is None:
            start_time = current_time
        if start_time > current_time:
            raise ValueError("start_time cannot be in the future")
        if current_time - start_time > TASKED_WORKLOAD_TIMEOUT:
            raise ValueError("start_time is too far in the past")

        start_entry = bisect_left(
            self.log_history,
            start_time,
            key=lambda x: x["time"] if isinstance(x, WorkloadEntry) else x,
        )
        end_entry = start_entry
        accumulated_logs = 0

        while time.time() - start_time < TASKED_WORKLOAD_TIMEOUT:
            while end_entry < len(self.log_history):
                accumulated_logs += len(self.log_history[end_entry])
                end_entry += 1
            if accumulated_logs >= number:
                return self.log_history[start_entry:end_entry]
            time.sleep(3)

        raise TimeoutError(
            "Workload generator did not collect enough data within the timeout period."
        )

    def recent_entries(self, duration=30):
        """
        Return recently collected data within the given duration (seconds).
        """
        start_time = time.time() - duration
        start_entry = bisect_left(
            self.log_history,
            start_time,
            key=lambda x: x["time"] if isinstance(x, WorkloadEntry) else x,
        )
        return self.log_history[start_entry:]
