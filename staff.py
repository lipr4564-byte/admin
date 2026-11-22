"""
Контроллер пользователей и действующего состава:
пояснения ролей, анкетирование, заявки, отпуск, KPI и синхронизация закрепа в теме.
"""

import asyncio
from datetime import datetime, date, timedelta
from typing import Optional
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatType

from config import GROUP_ID, STAFF_TOPIC_ID
from database import (
    get_all_settings, get_int_setting, set_setting,
    register_user_activity, is_owner,
    get_owners, get_owner_ids,
    get_role_limits, get_role_occupancy, get_role_free_slots,
    get_user_roles, get_all_staff_grouped, get_all_active_staff_members,
    remove_all_staff_roles,
    get_role_cooldown, has_pending_application, create_application,
    get_user_vacation, create_vacation_request, end_user_vacation, clear_expired_vacations,
    record_kpi_action,
)
from keyboards import (
    ROLES, KPI_ACTION_TYPES, Views, esc, _blockquote, format_eternal_link, format_vac_date,
    main_menu_kb, back_main_kb, apply_role_kb, subrole_curator_kb, subrole_mapper_kb,
    apply_confirm_kb, app_decision_kb, vacation_duration_kb, vacation_decision_kb, kpi_action_kb,
)
from logger import send_log, log
from premium_emoji import pe

router = Router()
_roster_lock = asyncio.Lock()


class StaffStates(StatesGroup):
    answering_question = State()
    waiting_vac_custom_date = State()
    waiting_vac_reason = State()
    waiting_kpi_note = State()


async def sync_staff_roster(bot: Bot, force_recreate: bool = False) -> bool:
    """
    Редактирует закреплённое сообщение со списком админов в теме.
    Новое сообщение отправляется и закрепляется только если старое удалено или его ещё не было.
    """
    async with _roster_lock:
        active_owners, pending_owners = await get_owners()
        staff_by_role, pending_by_role = await get_all_staff_grouped()
        limits = await get_role_limits()

        text = Views.build_roster_text(
            active_owners=active_owners,
            pending_owners=pending_owners,
            staff_by_role=staff_by_role,
            pending_by_role=pending_by_role,
            limits=limits,
        )

        msg_id = await get_int_setting("roster_msg_id")
        topic_id = await get_int_setting("staff_topic_id") or STAFF_TOPIC_ID

        if msg_id > 0 and not force_recreate:
            try:
                await bot.edit_message_text(
                    chat_id=GROUP_ID,
                    message_id=msg_id,
                    text=text,
                    parse_mode="HTML",
                )
                return True
            except Exception as e:
                err = str(e).lower()
                if "message is not modified" in err:
                    return True
                if not any(x in err for x in ("not found", "message_id_invalid", "can't be edited", "deleted")):
                    log.warning(f"Ошибка редактирования закрепа {msg_id}: {e}")

        try:
            kwargs = {
                "chat_id": GROUP_ID,
                "text": text,
                "parse_mode": "HTML",
            }
            if topic_id > 0:
                kwargs["message_thread_id"] = topic_id
            sent = await bot.send_message(**kwargs)
            await set_setting("roster_msg_id", sent.message_id)
            try:
                await bot.pin_chat_message(
                    chat_id=GROUP_ID,
                    message_id=sent.message_id,
                    disable_notification=True,
                )
            except Exception as pin_err:
                log.warning(f"Не удалось закрепить сообщение состава: {pin_err}")
            return True
        except Exception as e:
            log.error(f"Ошибка отправки списка состава в группу: {e}")
            return False


async def staff_maintenance_scheduler(bot: Bot):
    """
    Раз в 60 секунд:
    1) Снимает истёкшие отпуска;
    2) Проверяет, не вышел ли кто-то из админов из группы (резервная сверка).
    """
    while True:
        try:
            changed = False
            expired_uids = await clear_expired_vacations()
            if expired_uids:
                changed = True
                for uid in expired_uids:
                    await send_log(bot, f"📅 Истёк срок отпуска у админа <code>{uid}</code>")

            members = await get_all_active_staff_members()
            for m in members:
                uid = m["user_id"]
                try:
                    cm = await bot.get_chat_member(chat_id=GROUP_ID, user_id=uid)
                    if cm.status in ("left", "kicked"):
                        removed = await remove_all_staff_roles(uid)
                        if removed:
                            changed = True
                            await send_log(
                                bot,
                                f"🚪 <b>Админ покинул группу</b>\n"
                                f"Пользователь: {format_eternal_link(uid, m.get('full_name', str(uid)), m.get('username', ''))}\n"
                                f"Сняты роли: {', '.join(ROLES[r]['title'] for r in removed if r in ROLES)}",
                            )
                except Exception:
                    pass

            if changed:
                await sync_staff_roster(bot)
        except Exception as e:
            log.error(f"Ошибка фонового планировщика состава: {e}")
        await asyncio.sleep(60)


@router.message(F.chat.id == GROUP_ID)
async def group_activity_monitor(message: Message, bot: Bot):
    user = message.from_user
    if not user or user.is_bot:
        return
    _, need_refresh = await register_user_activity(
        user.id, user.username or "", user.full_name or "", is_group_msg=True
    )
    if need_refresh:
        await sync_staff_roster(bot)


async def _send_main_menu(target, user, edit: bool = False):
    roles = await get_user_roles(user.id)
    vac = await get_user_vacation(user.id)
    owner_flag = await is_owner(user)
    is_staff = len(roles) > 0

    role_line = ""
    if is_staff:
        titles = [ROLES[r]["title"] for r in roles if r in ROLES]
        vac_txt = f" (в отпуске до {format_vac_date(vac['until_date'])})" if vac else ""
        role_line = f"\n{pe('🛡️')} Твоя должность: <b>{esc(', '.join(titles))}</b>{vac_txt}"

    text = (
        f"{pe('👑')} <b>Набор и состав администрации Rise of Europe</b>\n\n"
        f"Здесь можно изучить описание должностей, подать заявку в состав или посмотреть актуальный список команды."
        f"{role_line}"
    )
    kb = main_menu_kb(is_staff=is_staff, is_owner_flag=owner_flag, on_vacation=bool(vac))
    if edit:
        await target.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("start"), F.chat.type == ChatType.PRIVATE)
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    _, need_refresh = await register_user_activity(
        message.from_user.id, message.from_user.username or "", message.from_user.full_name or ""
    )
    if need_refresh:
        await sync_staff_roster(bot)
    await _send_main_menu(message, message.from_user, edit=False)


@router.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _send_main_menu(callback.message, callback.from_user, edit=True)
    await callback.answer()


@router.callback_query(F.data == "roles_info")
async def cb_roles_info(callback: CallbackQuery):
    await callback.message.edit_text(
        Views.roles_explanation(),
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "view_roster")
async def cb_view_roster(callback: CallbackQuery):
    active_owners, pending_owners = await get_owners()
    staff_by_role, pending_by_role = await get_all_staff_grouped()
    limits = await get_role_limits()
    text = Views.build_roster_text(active_owners, pending_owners, staff_by_role, pending_by_role, limits)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_main_kb())
    await callback.answer()


# ===================== ПОДАЧА ЗАЯВКИ И АНКЕТИРОВАНИЕ =====================

async def _collect_occupancy() -> dict:
    occ = {}
    for rk in ROLES:
        occ[rk] = await get_role_occupancy(rk)
    return occ


@router.callback_query(F.data == "apply_start")
async def cb_apply_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    st = await get_all_settings()
    if not st["applications_open"]:
        await callback.answer("❗ Набор в администрацию сейчас временно закрыт.", show_alert=True)
        return

    limits = await get_role_limits()
    occupancy = await _collect_occupancy()
    await callback.message.edit_text(
        f"{pe('✏️')} <b>Подача заявки в администрацию</b>\n\n"
        f"Перед выбором советуем открыть <b>Пояснение должностей</b>, чтобы выбрать подходящее направление:",
        parse_mode="HTML",
        reply_markup=apply_role_kb(limits, occupancy),
    )
    await callback.answer()


@router.callback_query(F.data == "role_grp_curator")
async def cb_role_grp_curator(callback: CallbackQuery):
    limits = await get_role_limits()
    occupancy = await _collect_occupancy()
    await callback.message.edit_text(
        f"{pe('⚔️')} <b>Выбери направление кураторства:</b>\n\n"
        f"• <b>Военный куратор:</b> {ROLES['curator_mil']['desc']}\n"
        f"• <b>Экономический куратор:</b> {ROLES['curator_econ']['desc']}",
        parse_mode="HTML",
        reply_markup=subrole_curator_kb(limits, occupancy),
    )
    await callback.answer()


@router.callback_query(F.data == "role_grp_mapper")
async def cb_role_grp_mapper(callback: CallbackQuery):
    limits = await get_role_limits()
    occupancy = await _collect_occupancy()
    await callback.message.edit_text(
        f"{pe('🌍')} <b>Выбери направление картографии:</b>\n\n"
        f"• <b>Обычный маппер:</b> {ROLES['mapper_regular']['desc']}\n"
        f"• <b>Вар маппер:</b> {ROLES['mapper_war']['desc']}",
        parse_mode="HTML",
        reply_markup=subrole_mapper_kb(limits, occupancy),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("apply_role_"))
async def cb_apply_role_select(callback: CallbackQuery, state: FSMContext):
    role_key = callback.data[len("apply_role_"):]
    meta = ROLES.get(role_key)
    if not meta:
        return

    uid = callback.from_user.id
    user_roles = await get_user_roles(uid)
    if role_key in user_roles:
        await callback.answer("Вы уже занимаете эту должность!", show_alert=True)
        return

    free = await get_role_free_slots(role_key)
    if free is not None and free <= 0:
        await callback.answer("⛔ На эту должность сейчас нет свободных мест.", show_alert=True)
        return

    if await has_pending_application(uid, role_key):
        await callback.answer("⏳ Ваша заявка на эту должность уже на рассмотрении.", show_alert=True)
        return

    cd_sec = await get_role_cooldown(uid, role_key)
    if cd_sec > 0:
        days = cd_sec // 86400
        hours = (cd_sec % 86400) // 3600
        await callback.answer(
            f"⏳ Повторная заявка на эту роль доступна через {days} дн. {hours} ч.",
            show_alert=True,
        )
        return

    questions = meta["questions"]
    await state.update_data(role_key=role_key, q_index=0, answers=[])
    await state.set_state(StaffStates.answering_question)

    await callback.message.edit_text(
        f"{pe('📋')} <b>Анкета: {esc(meta['title'])}</b>\n"
        f"Вопрос <b>1</b> из <b>{len(questions)}</b>:\n\n"
        f"{_blockquote(esc(questions[0]))}\n\n"
        f"Напиши ответ одним сообщением:",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )
    await callback.answer()


@router.message(StaffStates.answering_question, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_question_answer(message: Message, state: FSMContext):
    ans = message.text.strip()
    if len(ans) < 2 or len(ans) > 1000:
        await message.answer(f"{pe('❌')} Ответ должен быть от 2 до 1000 символов.", parse_mode="HTML")
        return

    data = await state.get_data()
    role_key = data["role_key"]
    q_index = data["q_index"]
    answers = data["answers"]
    meta = ROLES[role_key]
    questions = meta["questions"]

    answers.append([questions[q_index], ans])
    next_idx = q_index + 1

    if next_idx < len(questions):
        await state.update_data(q_index=next_idx, answers=answers)
        await message.answer(
            f"{pe('📋')} <b>Анкета: {esc(meta['title'])}</b>\n"
            f"Вопрос <b>{next_idx + 1}</b> из <b>{len(questions)}</b>:\n\n"
            f"{_blockquote(esc(questions[next_idx]))}\n\n"
            f"Напиши ответ одним сообщением:",
            parse_mode="HTML",
            reply_markup=back_main_kb(),
        )
        return

    await state.update_data(answers=answers)
    preview_lines = []
    for idx, (q, a) in enumerate(answers, 1):
        preview_lines.append(f"<b>{idx}. {esc(q)}</b>\n{esc(a)}")

    await message.answer(
        f"{pe('❓')} <b>Проверь анкету перед отправкой</b>\n"
        f"Должность: <b>{esc(meta['title'])}</b>\n\n"
        f"{_blockquote(chr(10).join(preview_lines))}\n\n"
        f"Отправляем на рассмотрение?",
        parse_mode="HTML",
        reply_markup=apply_confirm_kb(),
    )


@router.callback_query(F.data == "apply_submit")
async def cb_apply_submit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    role_key = data.get("role_key")
    answers = data.get("answers")
    if not role_key or not answers:
        await callback.answer("❌ Данные анкеты не найдены, начни заново.", show_alert=True)
        await state.clear()
        return

    free = await get_role_free_slots(role_key)
    if free is not None and free <= 0:
        await state.clear()
        await callback.answer("⛔ Пока анкета заполнялась, свободные места закончились.", show_alert=True)
        await _send_main_menu(callback.message, callback.from_user, edit=True)
        return

    user = callback.from_user
    app_id = await create_application(user.id, role_key, answers)
    await state.clear()

    meta = ROLES[role_key]
    ans_blocks = [f"<b>{idx}. {esc(q)}</b>\n{esc(a)}" for idx, (q, a) in enumerate(answers, 1)]
    cand_link = format_eternal_link(user.id, user.full_name or str(user.id), user.username or "")

    admin_text = (
        f"{pe('📩')} <b>Новая заявка в администрацию #{app_id}</b>\n"
        f"─────────────────────\n"
        f"Кандидат: {cand_link}\n"
        f"ID: <code>{user.id}</code>\n"
        f"Должность: <b>{esc(meta['title'])}</b>\n\n"
        f"{_blockquote(chr(10).join(ans_blocks))}"
    )

    for own_id in await get_owner_ids():
        try:
            await bot.send_message(
                chat_id=own_id,
                text=admin_text,
                parse_mode="HTML",
                reply_markup=app_decision_kb(app_id),
            )
        except Exception as e:
            log.warning(f"Не удалось отправить заявку #{app_id} владельцу {own_id}: {e}")

    await send_log(
        bot,
        f"📩 <b>Заявка #{app_id}</b>\nКандидат: {cand_link}\nРоль: <b>{esc(meta['title'])}</b>",
    )

    await callback.message.edit_text(
        f"{pe('✅')} <b>Заявка #{app_id} на должность «{esc(meta['title'])}» отправлена!</b>\n\n"
        f"Ожидай решения руководства, ответ придёт сюда в бот.",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )
    await callback.answer("✅ Отправлено")


# ===================== ОТПУСК / НЕАКТИВ =====================

@router.callback_query(F.data == "vac_start")
async def cb_vac_start(callback: CallbackQuery, state: FSMContext):
    roles = await get_user_roles(callback.from_user.id)
    if not roles:
        await callback.answer("⛔ Доступно только действующим админам.", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        f"{pe('📅')} <b>Оформление отпуска / неактива</b>\n\n"
        f"Выбери дату возврата или укажи её вручную:",
        parse_mode="HTML",
        reply_markup=vacation_duration_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("vac_days_"))
async def cb_vac_days(callback: CallbackQuery, state: FSMContext):
    days = int(callback.data[len("vac_days_"):])
    until_dt = date.today() + timedelta(days=days)
    await state.update_data(vac_until=until_dt.isoformat())
    await state.set_state(StaffStates.waiting_vac_reason)
    await callback.message.edit_text(
        f"{pe('📅')} Возврат: <b>{until_dt.strftime('%d.%m.%Y')}</b>\n\n"
        f"Напиши короткую причину неактива (можно <code>-</code>):",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "vac_custom_date")
async def cb_vac_custom_date(callback: CallbackQuery, state: FSMContext):
    await state.set_state(StaffStates.waiting_vac_custom_date)
    await callback.message.edit_text(
        f"{pe('✏️')} Введи дату возврата в формате <code>ДД.ММ</code> (например: <code>29.11</code>):",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )
    await callback.answer()


def _parse_ddmm(raw: str) -> Optional[date]:
    s = raw.strip()
    today = date.today()
    for fmt in ("%d.%m.%Y", "%d.%m"):
        try:
            dt = datetime.strptime(s, fmt).date()
            if fmt == "%d.%m":
                dt = dt.replace(year=today.year)
                if dt < today:
                    dt = dt.replace(year=today.year + 1)
            return dt
        except ValueError:
            continue
    return None


@router.message(StaffStates.waiting_vac_custom_date, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_vac_custom_date(message: Message, state: FSMContext):
    dt = _parse_ddmm(message.text)
    if not dt or dt <= date.today() or (dt - date.today()).days > 90:
        await message.answer(
            f"{pe('❌')} Неверная дата. Укажи будущую дату в формате <code>ДД.ММ</code> (не дальше 90 дней).",
            parse_mode="HTML",
        )
        return

    await state.update_data(vac_until=dt.isoformat())
    await state.set_state(StaffStates.waiting_vac_reason)
    await message.answer(
        f"{pe('📅')} Дата возврата: <b>{dt.strftime('%d.%m.%Y')}</b>\n\n"
        f"Напиши короткую причину неактива (можно <code>-</code>):",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )


@router.message(StaffStates.waiting_vac_reason, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_vac_reason(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    until_iso = data.get("vac_until")
    if not until_iso:
        await state.clear()
        return

    reason = message.text.strip()[:200]
    user = message.from_user
    req_id = await create_vacation_request(user.id, until_iso, reason)
    await state.clear()

    roles = await get_user_roles(user.id)
    role_titles = ", ".join(ROLES[r]["title"] for r in roles if r in ROLES)
    link = format_eternal_link(user.id, user.full_name or str(user.id), user.username or "")
    until_short = format_vac_date(until_iso)

    for own_id in await get_owner_ids():
        try:
            await bot.send_message(
                chat_id=own_id,
                text=(
                    f"{pe('📅')} <b>Запрос на отпуск #{req_id}</b>\n\n"
                    f"Админ: {link}\n"
                    f"Должность: <b>{esc(role_titles)}</b>\n"
                    f"До даты: <b>{until_short}</b>\n"
                    f"Причина: <i>{esc(reason)}</i>"
                ),
                parse_mode="HTML",
                reply_markup=vacation_decision_kb(req_id),
            )
        except Exception:
            pass

    await message.answer(
        f"{pe('✅')} Запрос на отпуск до <b>{until_short}</b> отправлен руководству!",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )


@router.callback_query(F.data == "vac_return")
async def cb_vac_return(callback: CallbackQuery, bot: Bot):
    uid = callback.from_user.id
    await end_user_vacation(uid)
    await sync_staff_roster(bot)
    await send_log(bot, f"📅 Админ <code>{uid}</code> досрочно вышел из отпуска")
    await callback.answer("✅ Отпуск завершён, метка в закрепе снята!", show_alert=True)
    await _send_main_menu(callback.message, callback.from_user, edit=True)


# ===================== УЧЁТ РАБОТЫ (KPI) =====================

@router.callback_query(F.data == "kpi_log_menu")
async def cb_kpi_log_menu(callback: CallbackQuery):
    roles = await get_user_roles(callback.from_user.id)
    if not roles:
        await callback.answer("⛔ Доступно только действующим админам.", show_alert=True)
        return

    await callback.message.edit_text(
        f"{pe('📊')} <b>Учёт работы (KPI)</b>\n\n"
        f"Выбери тип выполненной проверки или действия:",
        parse_mode="HTML",
        reply_markup=kpi_action_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("kpi_act_"))
async def cb_kpi_act_select(callback: CallbackQuery, state: FSMContext):
    act_key = callback.data[len("kpi_act_"):]
    act_name = KPI_ACTION_TYPES.get(act_key)
    if not act_name:
        return

    await state.update_data(kpi_act_key=act_key, kpi_act_name=act_name)
    await state.set_state(StaffStates.waiting_kpi_note)
    await callback.message.edit_text(
        f"{pe('✏️')} <b>{esc(act_name)}</b>\n\n"
        f"Отправь короткое примечание или ссылку на сообщение в теме (можно <code>-</code>):",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )
    await callback.answer()


@router.message(StaffStates.waiting_kpi_note, F.chat.type == ChatType.PRIVATE, F.text)
async def handle_kpi_note(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    act_key = data.get("kpi_act_key")
    act_name = data.get("kpi_act_name")
    if not act_key:
        await state.clear()
        return

    note = message.text.strip()[:250]
    user = message.from_user
    await record_kpi_action(user.id, act_key, note)
    await state.clear()

    link = format_eternal_link(user.id, user.full_name or str(user.id), user.username or "")
    await send_log(
        bot,
        f"📊 <b>KPI действие: {esc(act_name)}</b>\n"
        f"Админ: {link}\n"
        f"Примечание: <i>{esc(note)}</i>",
    )

    await message.answer(
        f"{pe('✅')} Действие «<b>{esc(act_name)}</b>» учтено в KPI!",
        parse_mode="HTML",
        reply_markup=back_main_kb(),
    )