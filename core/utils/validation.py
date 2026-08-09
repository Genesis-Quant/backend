"""应用请求字段的公共校验。"""


def normalize_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("不能为空")
    return normalized


def strip_text(value: str) -> str:
    return value.strip()
