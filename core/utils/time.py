"""共享时间函数。"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)
