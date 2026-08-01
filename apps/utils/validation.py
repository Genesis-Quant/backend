"""Backend 自有请求字段的公共校验。"""

from core.utils import normalize_str_list


def validate_outputs(value: list[str]) -> list[str]:
    return normalize_str_list(value, "output", reject_duplicates=True)
