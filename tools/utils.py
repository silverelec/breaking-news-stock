"""
utils.py
Shared utilities for the daily market brief pipeline.
"""

import time
import requests


def retrying_get(url: str, max_attempts: int = 3, retry_delay: float = 2.0, **kwargs) -> requests.Response:
    """
    requests.get() with exponential backoff retry on network/HTTP errors.

    Raises the last exception if all attempts are exhausted.
    Call sites can wrap this in try/except to handle failure gracefully.

    Args:
        url: URL to fetch
        max_attempts: Total number of attempts (default 3)
        retry_delay: Initial delay in seconds before first retry (doubles each time)
        **kwargs: Passed directly to requests.get() (params, headers, timeout, etc.)
    """
    current_delay = retry_delay
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            if attempt < max_attempts:
                short_url = (url[:55] + "...") if len(url) > 55 else url
                print(f"    [retry {attempt}/{max_attempts}] {short_url}: {type(e).__name__} — retrying in {current_delay:.1f}s")
                time.sleep(current_delay)
                current_delay *= 2
    raise last_exc
