"""
SKYNET — Shared Utilities

Common utilities used across all SKYNET components.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, TypeVar

T = TypeVar("T")


# =============================================================================
# ID Generation
# =============================================================================
def generate_job_id() -> str:
    """Generate a unique job ID."""
    return f"job_{uuid.uuid4().hex[:12]}"


def generate_worker_id() -> str:
    """Generate a unique worker ID."""
    return f"worker_{uuid.uuid4().hex[:12]}"


def generate_project_id() -> str:
    """Generate a unique project ID."""
    return f"proj_{uuid.uuid4().hex[:12]}"


def generate_task_id() -> str:
    """Generate a unique task ID."""
    return f"task_{uuid.uuid4().hex[:12]}"


# =============================================================================
# Time Utilities
# =============================================================================
def utcnow() -> str:
    """Get current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string to datetime object."""
    # Handle both timezone-aware and naive strings
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return datetime.fromisoformat(dt_str)


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"


# =============================================================================
# JSON Utilities
# =============================================================================
def to_json(obj: Any, indent: int = 2) -> str:
    """Convert object to JSON string."""
    return json.dumps(obj, indent=indent, default=str)


def from_json(json_str: str) -> Any:
    """Parse JSON string to object."""
    return json.loads(json_str)


def safe_json_loads(json_str: str, default: Any = None) -> Any:
    """Safely parse JSON string, returning default on error."""
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return default


# =============================================================================
# Async Utilities
# =============================================================================
async def run_with_timeout(
    coro: Callable[..., Any],
    timeout: float,
    *args,
    **kwargs,
) -> Any:
    """
    Run a coroutine with a timeout.
    
    Raises asyncio.TimeoutError if the coroutine takes too long.
    """
    return await asyncio.wait_for(coro(*args, **kwargs), timeout=timeout)


def async_retry(max_attempts: int = 3, delay: float = 1.0):
    """
    Decorator to retry async functions on failure.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Delay between retries in seconds
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(delay * (attempt + 1))
            raise last_exception
        return wrapper
    return decorator


# =============================================================================
# String Utilities
# =============================================================================
def truncate(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate text to maximum length."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename by removing invalid characters."""
    import re
    # Remove invalid characters for Windows/Unix
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)
    # Remove leading/trailing spaces and dots
    return filename.strip(". ")


def snake_to_camel(text: str) -> str:
    """Convert snake_case to camelCase."""
    components = text.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def camel_to_snake(text: str) -> str:
    """Convert camelCase to snake_case."""
    import re
    return re.sub(r"(?<!^)(?=[A-Z])", "_", text).lower()


# =============================================================================
# Dict Utilities
# =============================================================================
def get_nested(data: dict, *keys: str, default: Any = None) -> Any:
    """Safely get nested dictionary value."""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
            if current is None:
                return default
        else:
            return default
    return current


def set_nested(data: dict, *keys: str, value: Any) -> None:
    """Safely set nested dictionary value."""
    current = data
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def merge_dicts(*dicts: dict) -> dict:
    """Merge multiple dictionaries (later ones override)."""
    result = {}
    for d in dicts:
        result.update(d)
    return result


# =============================================================================
# Validation Utilities
# =============================================================================
def is_valid_url(url: str) -> bool:
    """Check if a string is a valid URL."""
    import re
    url_pattern = re.compile(
        r"^https?://"  # http:// or https://
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain
        r"localhost|"  # localhost
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # IP
        r"(?::\d+)?$", re.IGNORECASE)
    return url_pattern.match(url) is not None


def is_valid_email(email: str) -> bool:
    """Check if a string is a valid email address."""
    import re
    email_pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    return email_pattern.match(email) is not None


# =============================================================================
# Collection Utilities
# =============================================================================
def chunk_list(items: list[T], chunk_size: int) -> list[list[T]]:
    """Split a list into chunks of specified size."""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def deduplicate(items: list[T], key: Callable[[T], Any] = None) -> list[T]:
    """Remove duplicates while preserving order."""
    if key is None:
        seen = set()
        result = []
        for item in items:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result
    else:
        seen = set()
        result = []
        for item in items:
            k = key(item)
            if k not in seen:
                seen.add(k)
                result.append(item)
        return result
