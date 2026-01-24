import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "concordia"
    return Path.home() / ".config" / "concordia"


def env_path() -> Path:
    return config_dir() / ".env"


def load_env() -> None:
    path = env_path()
    if path.exists():
        load_dotenv(path)


def ensure_gemini_key_interactive() -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = input("Enter GEMINI_API_KEY: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not value:
        return None
    path.write_text(f"GEMINI_API_KEY={value}\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY"] = value
    return value
