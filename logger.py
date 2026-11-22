"""
Логгирование в файл, консоль и группу логов.
Поддерживает автоподбор формата ID группы логов (-ID / -100ID / ID).
"""

import logging
import os
from datetime import datetime
from aiogram import Bot
from config import LOG_CHAT_ID, DATA_DIR, LOG_PATH

os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ROE_Staff")

_RESOLVED_LOG_CHAT_ID = None


def _ts() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


async def send_log(bot: Bot, text: str):
    global _RESOLVED_LOG_CHAT_ID
    payload = f"📋 <b>[Лог]</b> <code>{_ts()}</code>\n\n{text}"

    if _RESOLVED_LOG_CHAT_ID is not None:
        try:
            await bot.send_message(chat_id=_RESOLVED_LOG_CHAT_ID, text=payload, parse_mode="HTML")
            return
        except Exception:
            _RESOLVED_LOG_CHAT_ID = None

    raw_abs = abs(LOG_CHAT_ID)
    candidates = [LOG_CHAT_ID, -raw_abs, int(f"-100{raw_abs}")]
    seen = set()
    for cid in candidates:
        if cid in seen:
            continue
        seen.add(cid)
        try:
            await bot.send_message(chat_id=cid, text=payload, parse_mode="HTML")
            _RESOLVED_LOG_CHAT_ID = cid
            return
        except Exception:
            continue

    log.error(f"Не удалось отправить лог в чат {LOG_CHAT_ID}")