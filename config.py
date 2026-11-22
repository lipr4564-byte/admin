"""
Конфигурация бота администрации Rise of Europe.
"""

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _get_int_env(key: str, default: int) -> int:
    val = os.getenv(key, str(default)).strip()
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"Переменная {key} в .env должна быть числом")


@dataclass(frozen=True)
class Config:
    bot_token: str
    primary_owner_id: int
    primary_owner_username: str
    group_id: int
    staff_topic_id: int
    log_chat_id: int
    data_dir: str
    sqlite_path: str
    log_path: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or ":" not in token:
        raise ValueError("BOT_TOKEN не установлен в .env или переменных окружения")

    data_dir = os.getenv("DATA_DIR", "data").strip()
    os.makedirs(data_dir, exist_ok=True)

    return Config(
        bot_token=token,
        primary_owner_id=_get_int_env("PRIMARY_OWNER_ID", 7930482209),
        primary_owner_username=os.getenv("PRIMARY_OWNER_USERNAME", "asdewrx").strip().lstrip("@"),
        group_id=_get_int_env("GROUP_ID", -1003410277945),
        staff_topic_id=_get_int_env("STAFF_TOPIC_ID", 1376384),
        log_chat_id=_get_int_env("LOG_CHAT_ID", -5535178614),
        data_dir=data_dir,
        sqlite_path=os.path.join(data_dir, "staff_bot.db"),
        log_path=os.path.join(data_dir, "staff_bot.log"),
    )


CFG = load_config()

BOT_TOKEN = CFG.bot_token
PRIMARY_OWNER_ID = CFG.primary_owner_id
PRIMARY_OWNER_USERNAME = CFG.primary_owner_username
GROUP_ID = CFG.group_id
STAFF_TOPIC_ID = CFG.staff_topic_id
LOG_CHAT_ID = CFG.log_chat_id
DATA_DIR = CFG.data_dir
SQLITE_PATH = CFG.sqlite_path
LOG_PATH = CFG.log_path