# arate-limit

A flexible and robust rate limiting library for Python applications, offering multiple implementation strategies including leaky bucket, token bucket, and Redis-based sliding window rate limiters.

## Features

- Multiple rate limiting strategies:
  - Leaky bucket rate limiter
  - Token bucket rate limiter
  - Redis-based sliding window rate limiter
- Async/await support using `asyncio`
- Configurable time windows and burst allowances
- Safe for concurrent access within asyncio applications
- Redis integration for distributed rate limiting

## Installation

```bash
pip install arate-limit
```

## Usage

### Leaky Bucket Rate Limiter

A rate limiter that implements the leaky bucket algorithm, which smooths out bursts of requests and processes them at a steady rate:

```python
import asyncio

from rate_limiter import LeakyBucketRateLimiter

async def example():
    # Allow 100 requests per minute with some slack
    limiter = LeakyBucketRateLimiter(event_count=100, time_window=60, slack=10)

    async def limited_task():
        await limiter.wait()
        # Your rate-limited code here
        print("Task executed")

    # Execute multiple tasks
    tasks = [limited_task() for _ in range(10)]
    await asyncio.gather(*tasks)
```

### Token Bucket Rate Limiter

More sophisticated rate limiting with burst support:

```python
from datetime import timedelta

from rate_limiter import TokenBucketRateLimiter

async def example():
    # Allow 1000 requests per hour with burst of 100
    limiter = TokenBucketRateLimiter(
        event_count=1000,
        time_window=timedelta(hours=1),
        burst=100
    )

    await limiter.wait()  # Wait for rate limit
```

### Redis Sliding Window Rate Limiter

Distributed rate limiting using Redis:

```python
from rate_limiter import RedisSlidingWindowRateLimiter
import redis.asyncio as redis

async def example():
    redis_client = redis.Redis(host='localhost', port=6379)

    # Allow 1000 requests per minute with slack of 10
    limiter = RedisSlidingWindowRateLimiter(
        redis=redis_client,
        event_count=1000,
        time_window=60,
        slack=10
    )

    await limiter.wait()  # Wait for rate limit
```

## Configuration Options

All rate limiters accept these common parameters:

- `event_count`: Maximum number of events allowed in the time window
- `time_window`: Time period for the rate limit (accepts int/float seconds or timedelta)

Additional options per implementation:

### LeakyBucketRateLimiter
- `slack`: Additional allowance for brief bursts (default: 10)

### TokenBucketRateLimiter
- `burst`: Maximum burst size (default: 100)

### RedisSlidingWindowRateLimiter
- `slack`: Additional allowance for brief bursts (default: 10)
- `key_prefix`: Prefix for Redis keys (default: "rate_limiter:")

## Error Handling

The rate limiters raise appropriate exceptions for invalid configurations:

- `TypeError`: When parameters are of incorrect type
- `ValueError`: When parameters have invalid values

## Performance Considerations

- `LeakyBucketRateLimiter`: Best for scenarios requiring steady, predictable request rates
- `TokenBucketRateLimiter`: Efficient for bursty workloads
- `RedisSlidingWindowRateLimiter`: Suitable for distributed systems, but requires Redis

## License

This project is licensed under the MIT License - see the LICENSE file for details.
