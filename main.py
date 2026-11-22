"""
Точка входа бота администрации Rise of Europe.
"""

import asyncio
import os
import time

from aiogram import Bot, Dispatcher, Router, BaseMiddleware
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter
from aiogram.filters.chat_member_updated import MEMBER, ADMINISTRATOR, RESTRICTED, LEFT, KICKED
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType

from config import BOT_TOKEN, GROUP_ID, DATA_DIR
from database import (
    init_db, close_db, is_owner, is_blacklisted,
    remove_all_staff_roles, get_user_data,
)
from keyboards import ROLES, format_eternal_link
from staff import router as staff_router, sync_staff_roster, staff_maintenance_scheduler
from owner import router as owner_router
from logger import log, send_log
from premium_emoji import pe


_member_cache: dict = {}


class AccessMiddleware(BaseMiddleware):
    """
    1) Тихий ЧС: полное игнорирование без ответа.
    2) Проверка участия в группе РП (-1003410277945) для ЛС.
    """
    async def __call__(self, handler, event, data):
        user = None
        chat = None
        bot: Bot = data.get("bot")

        if isinstance(event, Message):
            user = event.from_user
            chat = event.chat
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            chat = event.message.chat if event.message else None
        else:
            return await handler(event, data)

        if not user or user.is_bot:
            return

        if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            if chat.id != GROUP_ID:
                return
            return await handler(event, data)

        if await is_owner(user):
            return await handler(event, data)

        if await is_blacklisted(user.id):
            return

        now = time.monotonic()
        cached = _member_cache.get(user.id)
        if cached and (now - cached[1] < 60):
            in_group = cached[0]
        else:
            in_group = False
            try:
                cm = await bot.get_chat_member(chat_id=GROUP_ID, user_id=user.id)
                in_group = cm.status in ("creator", "administrator", "member", "restricted")
            except Exception:
                in_group = False
            _member_cache[user.id] = (in_group, now)

        if not in_group:
            if isinstance(event, Message):
                await event.answer(
                    f"{pe('⛔')} Доступ к боту открыт только участникам РП группы <b>Rise of Europe</b>.",
                    parse_mode="HTML",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Вы не состоите в группе Rise of Europe.", show_alert=True)
            return

        return await handler(event, data)


leave_router = Router()


@leave_router.chat_member(
    ChatMemberUpdatedFilter(member_status_changed=(MEMBER | ADMINISTRATOR | RESTRICTED) >> (LEFT | KICKED))
)
async def on_member_left_group(event: ChatMemberUpdated, bot: Bot):
    if event.chat.id != GROUP_ID:
        return

    user = event.new_chat_member.user
    _member_cache.pop(user.id, None)

    removed_roles = await remove_all_staff_roles(user.id)
    if removed_roles:
        await sync_staff_roster(bot)
        u = await get_user_data(user.id) or {}
        link = format_eternal_link(user.id, u.get("full_name") or user.full_name, u.get("username") or user.username or "")
        r_names = ", ".join(ROLES[r]["title"] for r in removed_roles if r in ROLES)
        await send_log(
            bot,
            f"🚪 <b>Выход админа из группы РП</b>\n\n"
            f"Пользователь: {link}\n"
            f"Сняты роли: <b>{r_names}</b>\n"
            f"Список в теме автоматически обновлён.",
        )


async def on_startup(bot: Bot):
    os.makedirs(DATA_DIR, exist_ok=True)
    await init_db()
    await sync_staff_roster(bot)
    asyncio.create_task(staff_maintenance_scheduler(bot))
    me = await bot.get_me()
    log.info(f"Бот администрации @{me.username} запущен!")


async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())

    dp.include_router(leave_router)
    dp.include_router(owner_router)
    dp.include_router(staff_router)

    dp.startup.register(on_startup)

    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "chat_member"],
            drop_pending_updates=True,
        )
    finally:
        await close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())