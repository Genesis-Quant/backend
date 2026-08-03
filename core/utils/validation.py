"""Backend 自有请求字段的公共校验。"""

from runtime.utils import normalize_str_list


def normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("不能为空")
    return normalized


def validate_outputs(value: list[str]) -> list[str]:
    return normalize_str_list(value, "output", reject_duplicates=True)
