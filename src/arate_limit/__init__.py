import abc
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

INF = float("inf")
INF_DURATION = 1 << 63 - 1


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


class RateLimiter(metaclass=abc.ABCMeta):
    """
    Abstract base class defining the interface for rate limiters.

    This class serves as a template for implementing different rate limiting strategies.
    All concrete rate limiter implementations should inherit from this class and
    implement the wait method.
    """

    async def wait(self) -> None:
        """
        Wait until the next request is allowed according to the rate limiting strategy.

        This method must be implemented by concrete rate limiter classes. It should
        block until it's acceptable to perform the next operation according to the
        rate limiting rules.

        Returns:
            None
        """
        ...


class AtomicIntRateLimiter(RateLimiter):
    _per_request: int
    _max_slack: int
    _state: AtomicInt

    def __init__(self, event_count: int, time_window: int | float | timedelta = 1.0, slack: int = 10) -> None:
        """
        Initialize a rate limiter with specified parameters.

        Args:
            event_count (int): Maximum number of events allowed in the time window
            time_window (int | float | timedelta): Time period in seconds (unless using timedelta) for the rate limit (default: 1.0)
            slack (int): Additional allowance for brief bursts (default: 10)

        Raises:
            TypeError: If event_count or slack is not an integer, or if time_window is not
                      an int, float, or timedelta
            ValueError: If event_count or time_window is not positive, or if slack is negative
        """
        if not isinstance(event_count, int):
            raise TypeError("event_count must be an integer")
        if not isinstance(slack, int):
            raise TypeError("slack must be an integer")

        if event_count <= 0:
            raise ValueError("event_count must be positive")
        if slack < 0:
            raise ValueError("slack must be non-negative")

        if isinstance(time_window, (int, float)):
            tw = timedelta(seconds=time_window)
        elif isinstance(time_window, timedelta):
            tw = time_window
        else:
            raise TypeError("time_window must be an int, float, or timedelta")

        if tw.total_seconds() <= 0:
            raise ValueError("time_window must be positive")

        self._per_request = seconds_to_nanoseconds(tw.total_seconds()) // event_count
        self._max_slack = slack * self._per_request
        self._state = AtomicInt()

    async def wait(self) -> None:
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


@dataclass
class TokenBucketReservation:
    ok: bool
    tokens: int = 0
    time_to_act: datetime = field(default_factory=lambda: datetime.min.replace(tzinfo=timezone.utc))

    def delay_from_ns(self, now: datetime) -> int:
        if not self.ok:
            return INF_DURATION
        delay = (self.time_to_act - now).total_seconds()
        if delay < 0:
            return 0
        return seconds_to_nanoseconds(delay)


class TokenBucketRateLimiter(RateLimiter):
    _limit: float
    _burst: int
    _tokens: float
    _last: datetime
    _last_event: datetime
    _lock: asyncio.Lock

    def __init__(self, event_count: int, time_window: int | float | timedelta = 1.0, burst: int = 100) -> None:
        """
        Initialize a rate limiter with specified parameters.

        Args:
            event_count (int): Maximum number of events allowed in the time window
            time_window (int | float | timedelta): Time period in seconds (unless using timedelta) for the rate limit (default: 1.0)
            burst (int): Burst allows more events to happen at once, must be greater than zero (default: 100)

        Raises:
            TypeError: If event_count or burst is not an integer, or if time_window is not
                      an int, float, or timedelta
            ValueError: If event_count or time_window is not positive, or if burst is less than or equal to 0
        """
        if not isinstance(event_count, int):
            raise TypeError("event_count must be an integer")
        if not isinstance(burst, int):
            raise TypeError("burst must be an integer")

        if event_count <= 0:
            raise ValueError("event_count must be positive")
        if burst <= 0:
            raise ValueError("burst must greater than 0")

        if isinstance(time_window, (int, float)):
            tw = timedelta(seconds=time_window)
        elif isinstance(time_window, timedelta):
            tw = time_window
        else:
            raise TypeError("time_window must be an int, float, or timedelta")

        if tw.total_seconds() <= 0:
            raise ValueError("time_window must be positive")

        self._limit = 1.0 / (tw.total_seconds() / event_count)
        self._burst = burst
        self._tokens = self._burst
        self._last = datetime.min.replace(tzinfo=timezone.utc)
        self._last_event = datetime.min.replace(tzinfo=timezone.utc)
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        await self._wait_n(1)

    async def _wait_n(self, n: int) -> None:
        async with self._lock:
            burst = self._burst
            limit = self._limit

        if n > burst and limit != INF:
            raise ValueError("n exceeds limiter's burst")

        now = datetime.now(timezone.utc)
        wait_limit = INF_DURATION

        r = await self._reserve_n(now=now, n=n, max_future_reserve=wait_limit)
        if not r.ok:
            raise ValueError("wait is way too long")

        delay_ns = r.delay_from_ns(now)
        if delay_ns == 0:
            return
        await asyncio.sleep(nanoseconds_to_seconds(delay_ns))

    async def _reserve_n(self, now: datetime, n: int, max_future_reserve: int) -> TokenBucketReservation:
        async with self._lock:
            if self._limit == INF:
                return TokenBucketReservation(ok=True, tokens=n, time_to_act=now)
            if self._limit == 0:
                ok = False
                if self._burst >= n:
                    ok = True
                    self._burst -= n
                return TokenBucketReservation(ok=ok, tokens=self._burst, time_to_act=now)

            now, last, tokens = await self._advance(now)

            tokens -= n
            wait_duration_ns = 0
            if tokens < 0:
                wait_duration_ns = self._duration_from_tokens_ns(-tokens)

            ok = n <= self._burst and wait_duration_ns <= max_future_reserve

            r = TokenBucketReservation(ok=ok)
            if ok:
                r.tokens = n
                r.time_to_act = now + timedelta(seconds=nanoseconds_to_seconds(wait_duration_ns))

                self._last = now
                self._tokens = tokens
                self._last_event = r.time_to_act
            else:
                self._last = last

            return r

    async def _advance(self, now: datetime) -> tuple[datetime, datetime, float]:
        last = self._last
        if now < last:
            last = now

        elapsed = now - last
        delta = self._tokens_from_duration(elapsed)
        tokens = self._tokens + delta
        if tokens > self._burst:
            tokens = self._burst
        return (now, last, tokens)

    def _duration_from_tokens_ns(self, tokens: float) -> int:
        if self._limit <= 0:
            return INF_DURATION
        seconds = tokens / self._limit
        return seconds_to_nanoseconds(seconds)

    def _tokens_from_duration(self, d: timedelta) -> float:
        if self._limit <= 0:
            return 0
        return d.total_seconds() * self._limit
