import json
from typing import Any, Dict


def encode(message: Dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=True)


def decode(raw: str) -> Dict[str, Any]:
    return json.loads(raw)
