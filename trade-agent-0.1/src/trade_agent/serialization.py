"""Serialización canónica y determinista (mismos datos -> mismos bytes)."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return format(obj.normalize(), "f")
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def pretty_json(obj: Any) -> str:
    return json.dumps(to_jsonable(obj), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
