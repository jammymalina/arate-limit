import asyncio
import time
from datetime import timedelta


def seconds_to_nanoseconds(seconds: float) -> int:
    return int(seconds * 1e9)


def nanoseconds_to_seconds(nanoseconds: int) -> float:
    return nanoseconds / 1e9


class AtomicInt:
    _value: int
    _lock: asyncio.Lock

    def __init__(self, value: int = 0) -> None:
        self._value = value
        self._lock = asyncio.Lock()

    async def inc(self, delta: int = 1) -> int:
        async with self._lock:
            self._value += delta
            return self._value

    async def dec(self, delta: int = 1) -> int:
        return await self.inc(-delta)

    async def get_value(self) -> int:
        async with self._lock:
            return self._value

    async def set_value(self, value: int) -> None:
        async with self._lock:
            self._value = value

    async def compare_and_swap(self, old: int, new: int) -> bool:
        async with self._lock:
            if self._value == old:
                self._value = new
                return True

            return False


class AtomicIntRateLimiter:
    _per_request: int
    _max_slack: int
    _state: AtomicInt

    def __init__(self, rate_limit: int, time_window: int | float | timedelta = 1.0, slack: int = 10) -> None:
        """
        Initialize a rate limiter with specified parameters.

        Args:
            rate_limit (int): Maximum number of requests allowed in the time window
            time_window (int | float | timedelta): Time period in seconds for the rate limit (default: 1.0)
            slack (int): Additional allowance for brief bursts (default: 10)
        """
        if not isinstance(rate_limit, int):
            raise TypeError("rate_limit must be an integer")
        if not isinstance(slack, int):
            raise TypeError("slack must be an integer")

        if rate_limit <= 0:
            raise ValueError("Rate limit must be positive")
        if slack < 0:
            raise ValueError("Slack must be non-negative")

        if isinstance(time_window, (int, float)):
            tw = timedelta(seconds=time_window)
        elif isinstance(time_window, timedelta):
            tw = time_window
        else:
            raise TypeError("time_window must be an int, float, or timedelta")

        if tw.total_seconds() <= 0:
            raise ValueError("Time window must be positive")

        self._per_request = seconds_to_nanoseconds(tw.total_seconds()) // rate_limit
        self._max_slack = slack * self._per_request
        self._state = AtomicInt()

    async def take(self) -> None:
        new_time_of_next_permission_issue = 0

        while True:
            now = time.monotonic_ns()
            time_of_next_permission_issue = await self._state.get_value()

            if time_of_next_permission_issue == 0 or (
                self._max_slack == 0 and now - time_of_next_permission_issue > self._per_request
            ):
                new_time_of_next_permission_issue = now
            elif self._max_slack > 0 and now - time_of_next_permission_issue > self._max_slack + self._per_request:
                new_time_of_next_permission_issue = now - self._max_slack
            else:
                new_time_of_next_permission_issue = time_of_next_permission_issue + self._per_request

            if await self._state.compare_and_swap(time_of_next_permission_issue, new_time_of_next_permission_issue):
                break

        sleep_duration = new_time_of_next_permission_issue - now
        if sleep_duration > 0:
            await asyncio.sleep(nanoseconds_to_seconds(sleep_duration))
