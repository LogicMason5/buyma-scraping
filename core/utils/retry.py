import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_with_backoff(fn: Callable[[], T], retries: int = 3, base_delay: float = 1.0) -> T:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == retries - 1:
                break
            time.sleep(base_delay * (2**attempt))
    assert last_error is not None
    raise last_error
