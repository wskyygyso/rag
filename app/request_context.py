"""请求 ID 生成和校验。"""

import re
import uuid
from typing import Optional

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def resolve_request_id(raw_request_id: Optional[str]) -> str:
    """复用合法的上游请求 ID，否则生成新的十六进制 ID。"""
    if raw_request_id and REQUEST_ID_PATTERN.fullmatch(raw_request_id):
        return raw_request_id
    return uuid.uuid4().hex
