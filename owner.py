"""
Контроллер панели владельца и совладельцев:
решения по заявкам/отпускам, ручное управление составом и руководством,
лимиты мест по ролям, KPI отчёт за 7/30 дней, тихий ЧС и кулдауны.
"""

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatType

from config import PRIMARY_OWNER_ID
from database import (
    get_all_settings, get_int_setting, set_setting, is_owner,
    get_user_data, find_user_by_query, get_all_users,
    get_owners, add_owner_entry, remove_owner_by_id, remove_pending_owner,
    get_role_limits, set_role_limit, get_role_occupancy,
    get_user_roles, assign_staff_role, assign_staff_by_query,
    remove_staff_role, remove_all_staff_roles, get_all_active_staff_members,
    get_application, resolve_application, set_role_cooldown, clear_user_cooldowns,
    set_blacklist, get_blacklisted_users,
    resolve_vacation_request, end_user_vacation,
    get_kpi_report,
)
from keyboards import (
    ROLES, esc, _blockquote, format_eternal_link, format_vac_date,
    owner_panel_kb, back_owner_kb,
)
from staff import sync_staff_roster
from logger import send_log
from premium_emoji import pe, btn

router = Router()


class OwnerStates(StatesGroup):
    waiting_add_staff_query = State()
    waiting_add_owner_query = State()
    waiting_add_owner_title = State()
    waiting_blacklist_query = State()
    waiting_clear_cd_query = State()
    waiting_broadcast = State()
    waiting_topic_id = State()


# ===================== РЕШЕНИЯ ПО ЗАЯВКАМ И ОТПУСКАМ =====================

@router.callback_query(F.data.startswith("app_"))
async def cb_app_decision(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    parts = callback.data.split("_")
    action, app_id = parts[1], int(parts[2])
    app = await get_application(app_id)
    if not app or app["status"] != "pending":
        await callback.answer("Заявка уже рассмотрена.", show_alert=True)
        return

    cand_uid = app["user_id"]
    role_key = app["role_key"]
    meta = ROLES.get(role_key, {"title": role_key})

    if action == "bl":
        await resolve_application(app_id, "blacklisted", callback.from_user.id)
        await set_blacklist(cand_uid, True)
        await callback.message.edit_text(
            f"{pe('⛔')} <b>Заявка #{app_id} закрыта. Кандидат <code>{cand_uid}</code> внесён в тихий ЧС.</b>",
            parse_mode="HTML",
        )
        await send_log(bot, f"⛔ Кандидат <code>{cand_uid}</code> добавлен в тихий ЧС по заявке #{app_id}")
        await callback.answer("Внесён в тихий ЧС")
        return

    if action == "yes":
        res = await resolve_application(app_id, "approved", callback.from_user.id)
        if not res:
            await callback.answer("Уже рассмотрено.", show_alert=True)
            return
        await sync_staff_roster(bot)
        try:
            await bot.send_message(
                chat_id=cand_uid,
                text=(
                    f"{pe('✅')} <b>Твоя заявка #{app_id} одобрена!</b>\n\n"
                    f"Ты назначен на должность: <b>{esc(meta['title'])}</b>.\n"
                    f"Список состава в теме уже обновлён."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass
        await send_log(
            bot,
            f"✅ <b>Одобрена заявка #{app_id}</b>\nКандидат: <code>{cand_uid}</code>\nРоль: <b>{esc(meta['title'])}</b>",
        )
        await callback.message.edit_text(
            f"{pe('✅')} <b>Заявка #{app_id} одобрена ({esc(meta['title'])}). Закреп обновлён.</b>",
            parse_mode="HTML",
        )
        await callback.answer("✅ Одобрено")
        return

    if action == "no":
        res = await resolve_application(app_id, "rejected", callback.from_user.id)
        if not res:
            await callback.answer("Уже рассмотрено.", show_alert=True)
            return
        cd_days = await get_int_setting("cooldown_days")
        if cd_days > 0:
            await set_role_cooldown(cand_uid, role_key, cd_days)
        try:
            cd_note = f"\nПовторная заявка на эту должность будет доступна через {cd_days} дн." if cd_days > 0 else ""
            await bot.send_message(
                chat_id=cand_uid,
                text=f"{pe('❌')} <b>Твоя заявка #{app_id} на должность «{esc(meta['title'])}» отклонена.</b>{cd_note}",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await send_log(
            bot,
            f"❌ <b>Отклонена заявка #{app_id}</b>\nКандидат: <code>{cand_uid}</code>\nРоль: <b>{esc(meta['title'])}</b>",
        )
        await callback.message.edit_text(
            f"{pe('❌')} <b>Заявка #{app_id} отклонена (кулдаун {cd_days} дн.).</b>",
            parse_mode="HTML",
        )
        await callback.answer("❌ Отклонено")


@router.callback_query(F.data.startswith("vac_ok_") | F.data.startswith("vac_no_"))
async def cb_vac_decision(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    _, act, req_id = callback.data.split("_")
    approve = act == "ok"
    res = await resolve_vacation_request(req_id, approve=approve, reviewer_id=callback.from_user.id)
    if not res:
        await callback.answer("Запрос уже рассмотрен.", show_alert=True)
        return

    uid = res["user_id"]
    until_short = format_vac_date(res["until_date"])
    if approve:
        await sync_staff_roster(bot)
        try:
            await bot.send_message(
                chat_id=uid,
                text=f"{pe('✅')} <b>Твой отпуск до {until_short} одобрен!</b> Пометка добавлена в закреп.",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await send_log(bot, f"📅 Одобрен отпуск админа <code>{uid}</code> до {until_short}")
        await callback.message.edit_text(f"{pe('✅')} <b>Отпуск #{req_id} до {until_short} одобрен.</b>", parse_mode="HTML")
    else:
        try:
            await bot.send_message(
                chat_id=uid,
                text=f"{pe('❌')} <b>Запрос на отпуск до {until_short} отклонён.</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await callback.message.edit_text(f"{pe('❌')} <b>Отпуск #{req_id} отклонён.</b>", parse_mode="HTML")
    await callback.answer()


# ===================== ГЛАВНЫЙ ЭКРАН ПАНЕЛИ ВЛАДЕЛЬЦА =====================

async def _render_owner_panel(target, edit: bool = True):
    st = await get_all_settings()
    staff = await get_all_active_staff_members()
    active_own, pending_own = await get_owners()
    bl = await get_blacklisted_users()

    status_apps = "🟢 открыт" if st["applications_open"] else "🔴 закрыт"
    text = (
        f"{pe('🔧')} <b>Панель владельца</b>\n\n"
        f"{pe('👥')} Действующих админов: <b>{len(staff)}</b>\n"
        f"{pe('👑')} В руководстве: <b>{len(active_own) + len(pending_own)}</b>\n"
        f"{pe('⛔')} В тихом ЧС: <b>{len(bl)}</b>\n"
        f"{pe('📅')} Кулдаун после отказа: <b>{st['cooldown_days']} дн.</b>\n"
        f"{pe('📢')} Набор заявок: <b>{status_apps}</b>\n"
        f"ID темы закрепа: <code>{st['staff_topic_id']}</code>"
    )
    kb = owner_panel_kb(apps_open=st["applications_open"])
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("admin"), F.chat.type == ChatType.PRIVATE)
async def cmd_admin(message: Message, state: FSMContext):
    if not await is_owner(message.from_user):
        return
    await state.clear()
    await _render_owner_panel(message, edit=False)


@router.callback_query(F.data == "owner_panel")
async def cb_owner_panel(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.clear()
    await _render_owner_panel(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data == "own_toggle_apps")
async def cb_own_toggle_apps(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    st = await get_all_settings()
    await set_setting("applications_open", "0" if st["applications_open"] else "1")
    await _render_owner_panel(callback.message, edit=True)
    await callback.answer("Статус набора изменён")


@router.callback_query(F.data == "own_sync_roster")
async def cb_own_sync_roster(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    ok = await sync_staff_roster(bot)
    if ok:
        await callback.answer("✅ Закреп в теме обновлён!", show_alert=True)
    else:
        await callback.answer("❌ Ошибка обновления. Проверь права бота в группе.", show_alert=True)


# ===================== УПРАВЛЕНИЕ СОСТАВОМ =====================

async def _render_staff_list(callback: CallbackQuery):
    staff = await get_all_active_staff_members()
    builder = InlineKeyboardBuilder()
    builder.row(btn("Добавить админа вручную", "own_staff_add", "✅"))
    for m in staff:
        roles_txt = ", ".join(ROLES[r]["short"] for r in m["roles"] if r in ROLES)
        name = m.get("full_name") or str(m["user_id"])
        builder.row(btn(f"{name[:20]} ({roles_txt[:22]})", f"own_mng_{m['user_id']}"))
    builder.row(btn("В панель владельца", "owner_panel", "🔙"))

    await callback.message.edit_text(
        f"{pe('👥')} <b>Управление составом администрации</b>\n\n"
        f"Выбери сотрудника для смены ролей или снятия, либо добавь нового вручную:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


async def _render_manage_staff_card(callback: CallbackQuery, uid: int):
    u = await get_user_data(uid) or {}
    roles = await get_user_roles(uid)
    link = format_eternal_link(uid, u.get("full_name", str(uid)), u.get("username", ""))

    builder = InlineKeyboardBuilder()
    for rk, meta in ROLES.items():
        has_r = rk in roles
        prefix = "✅ " if has_r else "➕ "
        builder.row(btn(f"{prefix}{meta['title']}", f"own_tgr_{uid}_{rk}"))
    builder.row(btn("Снять отпуск", f"own_clrvac_{uid}", "📅"))
    builder.row(btn("Снять со всех должностей", f"own_kickstaff_{uid}", "🗑️"))
    builder.row(btn("Назад к списку", "own_staff", "🔙"))

    await callback.message.edit_text(
        f"{pe('🔧')} <b>Карточка сотрудника</b>\n\n"
        f"Админ: {link}\n"
        f"ID: <code>{uid}</code>\n\n"
        f"Нажимай на роли ниже, чтобы добавить или убрать должность:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "own_staff")
async def cb_own_staff(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.clear()
    await _render_staff_list(callback)
    await callback.answer()


@router.callback_query(F.data == "own_staff_add")
async def cb_own_staff_add(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    builder = InlineKeyboardBuilder()
    for rk, meta in ROLES.items():
        builder.row(btn(meta["title"], f"own_addrole_{rk}", meta["emoji"]))
    builder.row(btn("Назад", "own_staff", "🔙"))

    await callback.message.edit_text(
        f"{pe('✅')} <b>Назначение админа вручную</b>\n\nВыбери должность, на которую хочешь назначить игрока:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("own_addrole_"))
async def cb_own_addrole_select(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    rk = callback.data[len("own_addrole_"):]
    meta = ROLES.get(rk)
    if not meta:
        return

    await state.update_data(add_staff_role=rk)
    await state.set_state(OwnerStates.waiting_add_staff_query)
    await callback.message.edit_text(
        f"{pe('✏️')} Должность: <b>{esc(meta['title'])}</b>\n\n"
        f"Отправь <code>@username</code> или <code>ID</code> игрока:",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )
    await callback.answer()


@router.message(OwnerStates.waiting_add_staff_query, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_add_staff_query(message: Message, state: FSMContext, bot: Bot):
    if not await is_owner(message.from_user):
        return
    data = await state.get_data()
    rk = data.get("add_staff_role")
    ok, msg_txt = await assign_staff_by_query(message.text, rk)
    await state.clear()
    if ok:
        await sync_staff_roster(bot)
        await send_log(bot, f"👑 Владелец назначил на роль <b>{ROLES[rk]['title']}</b>: {esc(msg_txt)}")
    await message.answer(
        f"{pe('✅' if ok else '❌')} {esc(msg_txt)}",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )


@router.callback_query(F.data.startswith("own_mng_"))
async def cb_own_manage_staff(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    uid = int(callback.data[len("own_mng_"):])
    await _render_manage_staff_card(callback, uid)
    await callback.answer()


@router.callback_query(F.data.startswith("own_tgr_"))
async def cb_own_toggle_role(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    parts = callback.data.split("_", 3)
    uid, rk = int(parts[2]), parts[3]
    roles = await get_user_roles(uid)
    if rk in roles:
        await remove_staff_role(uid, rk)
    else:
        await assign_staff_role(uid, rk)
    await sync_staff_roster(bot)
    await _render_manage_staff_card(callback, uid)
    await callback.answer()


@router.callback_query(F.data.startswith("own_clrvac_"))
async def cb_own_clear_vac(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    uid = int(callback.data[len("own_clrvac_"):])
    await end_user_vacation(uid)
    await sync_staff_roster(bot)
    await callback.answer("✅ Отпуск снят", show_alert=True)


@router.callback_query(F.data.startswith("own_kickstaff_"))
async def cb_own_kick_staff(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    uid = int(callback.data[len("own_kickstaff_"):])
    await remove_all_staff_roles(uid)
    await sync_staff_roster(bot)
    await send_log(bot, f"🗑️ Владелец снял админа <code>{uid}</code> со всех должностей")
    await callback.answer("🗑️ Сотрудник полностью снят с админки", show_alert=True)
    await _render_staff_list(callback)


# ===================== РУКОВОДСТВО (ВЛАДЕЛЬЦЫ / СОВЛАДЕЛЬЦЫ) =====================

async def _render_owners_list(callback: CallbackQuery):
    active, pending = await get_owners()
    lines = []
    builder = InlineKeyboardBuilder()
    builder.row(btn("Добавить владельца / совладельца", "own_add_owner", "✅"))

    for o in active:
        link = format_eternal_link(o["user_id"], o.get("full_name", str(o["user_id"])), o.get("username", ""))
        lines.append(f"• {link} · <i>{esc(o.get('role_title', 'Совладелец'))}</i>")
        if o["user_id"] != PRIMARY_OWNER_ID and not o.get("is_primary"):
            builder.row(btn(f"Убрать: {o.get('full_name') or o['user_id']}", f"own_del_owner_{o['user_id']}", "🗑️"))

    for po in pending:
        lines.append(f"• @{esc(po['username'])} · <i>{esc(po.get('role_title', 'Совладелец'))}</i>")
        builder.row(btn(f"Убрать: @{po['username']}", f"own_del_pown_{po['username']}", "🗑️"))

    builder.row(btn("В панель владельца", "owner_panel", "🔙"))
    await callback.message.edit_text(
        f"{pe('👑')} <b>Руководство (Владельцы / Совладельцы)</b>\n\n"
        f"{_blockquote(chr(10).join(lines))}",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "own_owners")
async def cb_own_owners(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.clear()
    await _render_owners_list(callback)
    await callback.answer()


@router.callback_query(F.data == "own_add_owner")
async def cb_own_add_owner(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.set_state(OwnerStates.waiting_add_owner_query)
    await callback.message.edit_text(
        f"{pe('👑')} Введи <code>@username</code> или <code>ID</code> человека для добавления в руководство:",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )
    await callback.answer()


@router.message(OwnerStates.waiting_add_owner_query, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_add_owner_query(message: Message, state: FSMContext):
    if not await is_owner(message.from_user):
        return
    await state.update_data(new_owner_query=message.text.strip())
    await state.set_state(OwnerStates.waiting_add_owner_title)

    builder = InlineKeyboardBuilder()
    builder.row(btn("Совладелец", "own_title_Совладелец"), btn("Владелец", "own_title_Владелец"))
    builder.row(btn("Главный админ", "own_title_Главный админ"))
    await message.answer(
        "Выбери или напиши подпись должности в списке руководства:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(OwnerStates.waiting_add_owner_title, F.data.startswith("own_title_"))
async def cb_own_title_pick(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    title = callback.data[len("own_title_"):]
    data = await state.get_data()
    ok, txt = await add_owner_entry(data["new_owner_query"], role_title=title)
    await state.clear()
    if ok:
        await sync_staff_roster(bot)
    await callback.message.edit_text(f"{pe('✅' if ok else '❌')} {esc(txt)}", parse_mode="HTML", reply_markup=back_owner_kb())
    await callback.answer()


@router.message(OwnerStates.waiting_add_owner_title, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_own_title_text(message: Message, state: FSMContext, bot: Bot):
    if not await is_owner(message.from_user):
        return
    data = await state.get_data()
    ok, txt = await add_owner_entry(data["new_owner_query"], role_title=message.text.strip()[:30])
    await state.clear()
    if ok:
        await sync_staff_roster(bot)
    await message.answer(f"{pe('✅' if ok else '❌')} {esc(txt)}", parse_mode="HTML", reply_markup=back_owner_kb())


@router.callback_query(F.data.startswith("own_del_owner_"))
async def cb_own_del_owner(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    uid = int(callback.data[len("own_del_owner_"):])
    if await remove_owner_by_id(uid):
        await sync_staff_roster(bot)
        await callback.answer("Удалён из руководства")
        await _render_owners_list(callback)
    else:
        await callback.answer("Нельзя удалить главного владельца.", show_alert=True)


@router.callback_query(F.data.startswith("own_del_pown_"))
async def cb_own_del_pending_owner(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    uname = callback.data[len("own_del_pown_"):]
    await remove_pending_owner(uname)
    await sync_staff_roster(bot)
    await callback.answer("Удалён из руководства")
    await _render_owners_list(callback)


# ===================== ЛИМИТЫ МЕСТ ПО РОЛЯМ =====================

@router.callback_query(F.data == "own_limits")
async def cb_own_limits(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    limits = await get_role_limits()
    builder = InlineKeyboardBuilder()
    for rk, meta in ROLES.items():
        lim = limits.get(rk, -1)
        occ = await get_role_occupancy(rk)
        lim_txt = "без лимита" if lim < 0 else f"{occ}/{lim}"
        builder.row(
            btn("-1", f"own_limdec_{rk}"),
            btn(f"{meta['short']}: {lim_txt}", f"own_liminf_{rk}"),
            btn("+1", f"own_liminc_{rk}"),
        )
    builder.row(btn("В панель владельца", "owner_panel", "🔙"))

    await callback.message.edit_text(
        f"{pe('🔧')} <b>Лимиты мест по должностям</b>\n\n"
        f"• Нажми <b>-1 / +1</b> для изменения лимита (<code>0</code> закрывает набор на роль).\n"
        f"• Нажми на название роли, чтобы переключить в режим «без лимита».",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("own_lim"))
async def cb_own_lim_change(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    limits = await get_role_limits()
    data = callback.data
    if data.startswith("own_limdec_"):
        rk = data[len("own_limdec_"):]
        cur = limits.get(rk, -1)
        new_val = 0 if cur < 0 else max(0, cur - 1)
    elif data.startswith("own_liminc_"):
        rk = data[len("own_liminc_"):]
        cur = limits.get(rk, -1)
        new_val = 1 if cur < 0 else min(50, cur + 1)
    else:
        rk = data[len("own_liminf_"):]
        new_val = -1

    await set_role_limit(rk, new_val)
    await sync_staff_roster(bot)
    await cb_own_limits(callback)


# ===================== KPI ОТЧЁТ (7Д / 30Д) =====================

@router.callback_query(F.data == "own_kpi")
async def cb_own_kpi(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    report = await get_kpi_report()
    if not report:
        await callback.answer("В составе пока нет админов.", show_alert=True)
        return

    lines = []
    for s in report:
        roles_str = ", ".join(ROLES[r]["short"] for r in s["roles"] if r in ROLES)
        link = format_eternal_link(s["user_id"], s.get("full_name", str(s["user_id"])), s.get("username", ""))
        vac = f" [отпуск до {format_vac_date(s['vacation_until'])}]" if s.get("vacation_until") else ""
        flag_low = " ❗" if (s["act_7"] == 0 and s["msg_7"] == 0 and not vac) else ""
        lines.append(
            f"• {link} ({esc(roles_str)}){vac}{flag_low}\n"
            f"  KPI в боте: <b>{s['act_7']}</b> за 7д / <b>{s['act_30']}</b> за 30д | "
            f"Чат: <b>{s['msg_7']}</b> за 7д / <b>{s['msg_30']}</b> за 30д"
        )

    await callback.message.edit_text(
        f"{pe('📊')} <b>KPI отчёт по составу (7д / 30д)</b>\n"
        f"<i>❗ помечены админы с 0 активностью за неделю без отпуска</i>\n\n"
        f"{_blockquote(chr(10).join(lines))}",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )
    await callback.answer()


# ===================== ТИХИЙ ЧС И КУЛДАУНЫ =====================

async def _render_moderation_screen(callback: CallbackQuery):
    bl = await get_blacklisted_users()
    builder = InlineKeyboardBuilder()
    builder.row(btn("Добавить в тихий ЧС", "own_bl_add", "⛔"))
    for u in bl[:15]:
        name = u.get("full_name") or u.get("username") or str(u["user_id"])
        builder.row(btn(f"Убрать из ЧС: {name[:24]}", f"own_bl_del_{u['user_id']}", "✅"))
    builder.row(btn("Сбросить кулдаун игроку", "own_cd_clear", "🔄"))
    builder.row(btn("В панель владельца", "owner_panel", "🔙"))

    await callback.message.edit_text(
        f"{pe('⛔')} <b>Тихий ЧС и кулдауны</b>\n\n"
        f"Пользователей в тихом ЧС: <b>{len(bl)}</b>\n"
        f"<i>Пользователи в тихом ЧС полностью игнорируются ботом без каких либо уведомлений.</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "own_moderation")
async def cb_own_moderation(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.clear()
    await _render_moderation_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "own_bl_add")
async def cb_own_bl_add(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.set_state(OwnerStates.waiting_blacklist_query)
    await callback.message.edit_text(
        f"{pe('⛔')} Введи <code>@username</code> или <code>ID</code> для внесения в тихий ЧС:",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )
    await callback.answer()


@router.message(OwnerStates.waiting_blacklist_query, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_bl_add(message: Message, state: FSMContext):
    if not await is_owner(message.from_user):
        return
    q = message.text.strip()
    u = await find_user_by_query(q)
    uid = u["user_id"] if u else (int(q) if q.isdigit() else 0)
    if not uid:
        await message.answer("❌ Пользователь не найден (введи числовой ID или @username того, кто уже писал боту/в группу).", reply_markup=back_owner_kb())
        return
    await set_blacklist(uid, True)
    await state.clear()
    await message.answer(f"{pe('✅')} Пользователь <code>{uid}</code> внесён в тихий ЧС.", parse_mode="HTML", reply_markup=back_owner_kb())


@router.callback_query(F.data.startswith("own_bl_del_"))
async def cb_own_bl_del(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    uid = int(callback.data[len("own_bl_del_"):])
    await set_blacklist(uid, False)
    await callback.answer("✅ Удалён из ЧС")
    await _render_moderation_screen(callback)


@router.callback_query(F.data == "own_cd_clear")
async def cb_own_cd_clear(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.set_state(OwnerStates.waiting_clear_cd_query)
    await callback.message.edit_text(
        f"{pe('🔄')} Введи <code>@username</code> или <code>ID</code>, чтобы снять все кулдауны на подачу заявок:",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )
    await callback.answer()


@router.message(OwnerStates.waiting_clear_cd_query, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_cd_clear(message: Message, state: FSMContext):
    if not await is_owner(message.from_user):
        return
    q = message.text.strip()
    u = await find_user_by_query(q)
    uid = u["user_id"] if u else (int(q) if q.isdigit() else 0)
    if not uid:
        await message.answer("❌ Пользователь не найден.", reply_markup=back_owner_kb())
        return
    await clear_user_cooldowns(uid)
    await state.clear()
    await message.answer(f"{pe('✅')} Все кулдауны для <code>{uid}</code> сброшены.", parse_mode="HTML", reply_markup=back_owner_kb())


# ===================== НАСТРОЙКИ И РАССЫЛКА =====================

@router.callback_query(F.data == "own_settings")
async def cb_own_settings(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    st = await get_all_settings()
    builder = InlineKeyboardBuilder()
    builder.row(
        btn("-1 дн.", "own_cd_dec"),
        btn(f"Кулдаун отказа: {st['cooldown_days']} дн.", "own_noop"),
        btn("+1 дн.", "own_cd_inc"),
    )
    builder.row(btn("Изменить ID темы закрепа", "own_set_topic", "🔧"))
    builder.row(btn("Пересоздать закреп в теме", "own_recreate_roster", "🔄"))
    builder.row(btn("Рассылка по пользователям бота", "own_broadcast", "📢"))
    builder.row(btn("В панель владельца", "owner_panel", "🔙"))

    await callback.message.edit_text(
        f"{pe('🔧')} <b>Настройки бота и рассылка</b>\n\n"
        f"Текущая тема закрепа: <code>{st['staff_topic_id']}</code>\n"
        f"ID сообщения закрепа: <code>{st['roster_msg_id']}</code>",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "own_noop")
async def cb_own_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.in_(("own_cd_dec", "own_cd_inc")))
async def cb_own_cd_step(callback: CallbackQuery):
    if not await is_owner(callback.from_user):
        return
    cur = await get_int_setting("cooldown_days")
    nxt = max(0, cur - 1) if callback.data == "own_cd_dec" else min(30, cur + 1)
    await set_setting("cooldown_days", nxt)
    await cb_own_settings(callback)


@router.callback_query(F.data == "own_recreate_roster")
async def cb_own_recreate_roster(callback: CallbackQuery, bot: Bot):
    if not await is_owner(callback.from_user):
        return
    ok = await sync_staff_roster(bot, force_recreate=True)
    await callback.answer("✅ Новое сообщение отправлено и закреплено!" if ok else "❌ Ошибка отправки", show_alert=True)


@router.callback_query(F.data == "own_set_topic")
async def cb_own_set_topic(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.set_state(OwnerStates.waiting_topic_id)
    await callback.message.edit_text(
        f"{pe('🔧')} Введи новый числовой ID темы (thread_id) для закрепа состава (<code>0</code> для общего чата):",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )
    await callback.answer()


@router.message(OwnerStates.waiting_topic_id, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_set_topic(message: Message, state: FSMContext):
    if not await is_owner(message.from_user):
        return
    if not message.text.strip().isdigit():
        await message.answer("❌ Введи число.")
        return
    await set_setting("staff_topic_id", int(message.text.strip()))
    await state.clear()
    await message.answer(f"{pe('✅')} ID темы обновлён.", parse_mode="HTML", reply_markup=back_owner_kb())


@router.callback_query(F.data == "own_broadcast")
async def cb_own_broadcast(callback: CallbackQuery, state: FSMContext):
    if not await is_owner(callback.from_user):
        return
    await state.set_state(OwnerStates.waiting_broadcast)
    await callback.message.edit_text(
        f"{pe('📢')} Напиши текст рассылки:",
        parse_mode="HTML",
        reply_markup=back_owner_kb(),
    )
    await callback.answer()


@router.message(OwnerStates.waiting_broadcast, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_broadcast(message: Message, state: FSMContext, bot: Bot):
    if not await is_owner(message.from_user):
        return
    users = await get_all_users()
    sent = 0
    for u in users:
        if u.get("blacklisted"):
            continue
        try:
            await bot.send_message(
                chat_id=u["user_id"],
                text=f"{pe('📢')} <b>Объявление администрации Rise of Europe:</b>\n\n{esc(message.text)}",
                parse_mode="HTML",
            )
            sent += 1
        except Exception:
            pass
    await state.clear()
    await message.answer(f"{pe('✅')} Рассылка завершена. Доставлено: <b>{sent}</b>", parse_mode="HTML", reply_markup=back_owner_kb())