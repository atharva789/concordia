import os
import secrets
import socket
from dataclasses import dataclass
from typing import Tuple

import requests

INVITE_PREFIX = "concordia://"


@dataclass
class Invite:
    host: str
    port: int
    token: str


def generate_token(length: int = 16) -> str:
    return secrets.token_hex(length // 2)


def default_username() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "user"


def guess_public_host() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def fetch_public_ip(timeout: float = 3.0) -> str:
    try:
        resp = requests.get("https://api.ipify.org", params={"format": "text"}, timeout=timeout)
        if resp.status_code == 200:
            return resp.text.strip()
    except Exception:
        pass
    return ""


def format_invite(host: str, port: int, token: str) -> str:
    return f"{INVITE_PREFIX}{host}:{port}/{token}"


def parse_invite(code: str) -> Invite:
    if code.startswith(INVITE_PREFIX):
        code = code[len(INVITE_PREFIX) :]
    if "/" not in code:
        raise ValueError("Invite code must include host:port/token")
    host_port, token = code.split("/", 1)
    if ":" not in host_port:
        raise ValueError("Invite code must include host:port/token")
    host, port_str = host_port.rsplit(":", 1)
    return Invite(host=host, port=int(port_str), token=token)
