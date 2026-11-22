"""
Слой представления: метаданные должностей, анкеты, HTML-шаблоны и клавиатуры.
Совместим с Python 3.10 / 3.11 / 3.12+. Без длинных тире.
"""

from datetime import datetime
from html import escape
from typing import Dict, List
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from premium_emoji import btn, pe


def esc(text: str) -> str:
    return escape(str(text))


def _blockquote(content: str) -> str:
    return f"<blockquote>{content}</blockquote>"


ROLES: Dict[str, dict] = {
    "curator_mil": {
        "title": "Военный куратор",
        "short": "Военный",
        "emoji": "⚔️",
        "desc": "разбирает военные конфликты, логистику, потери и спорные боевые ситуации.",
        "questions": [
            "Какой у тебя опыт кураторства или игры в ВПИ/РП проектах?",
            "Насколько хорошо разбираешься в современной военной технике, ПВО и тактике?",
            "Каким ИИ пользуешься при разборе ситуаций или расчётах и как перепроверяешь ответы?",
            "Сколько времени в день готов уделять проверкам?",
        ],
    },
    "curator_econ": {
        "title": "Экономический куратор",
        "short": "Экономический",
        "emoji": "📊",
        "desc": "курирует экономику стран, бюджеты, торговлю, санкции и производственные мощности.",
        "questions": [
            "Какой у тебя опыт в экономическом кураторстве или ВПИ проектах?",
            "Как понимаешь баланс бюджета, торговли и ресурсов в РП?",
            "Каким ИИ пользуешься для расчётов и таблиц (если пользуешься)?",
            "Сколько времени в день готов уделять экономическим проверкам?",
        ],
    },
    "tracker": {
        "title": "Трекер",
        "short": "Трекер",
        "emoji": "🛡️",
        "desc": "следит за РП процессом, откатывает нон РП движения, контролирует ивенты и войны. Нужен большой опыт, без него не примут.",
        "questions": [
            "Опиши свой опыт трекерства в ВПИ (нужен реальный опыт, без него заявка не пройдёт).",
            "Как действуешь, если игрок совершил нон РП действие во время активной войны?",
            "Какими инструментами или ИИ пользуешься при проверке длинных РП постов?",
            "Сколько времени в день готов активно следить за новостником и войнами?",
        ],
    },
    "eventolog": {
        "title": "Ивентолог",
        "short": "Ивентолог",
        "emoji": "📢",
        "desc": "создатель ивентов, кризисов и различных событий. Нужен умеренный опыт.",
        "questions": [
            "Есть ли опыт создания ивентов и кризисов в ВПИ? Приведи 1 или 2 примера своих идей.",
            "Как вовлечь в событие сразу несколько стран, чтобы отыгрыш не заглох?",
            "Каким ИИ пользуешься для подготовки текстов и вводных (если пользуешься)?",
            "Сколько времени в неделю готов уделять проведению ивентов?",
        ],
    },
    "mapper_regular": {
        "title": "Обычный маппер",
        "short": "Обычная карта",
        "emoji": "🌍",
        "desc": "картограф основной политической карты проекта. Научиться несложно, важна аккуратность.",
        "questions": [
            "В каких программах работаешь с картами (Photoshop, Paint.NET, IbisPaint и т.д.)?",
            "Есть ли опыт ведения политических карт? Кратко опиши.",
            "Какими инструментами или ИИ пользуешься для обработки/ускорения работы?",
            "Как часто и в какое время готов обновлять карту?",
        ],
    },
    "mapper_war": {
        "title": "Вар маппер",
        "short": "Маппер по войнам",
        "emoji": "🏳️",
        "desc": "картограф по войнам, линиям фронта и операциям. Научиться несложно, нужна оперативность.",
        "questions": [
            "В каких программах рисуешь военные карты, фронты и направления ударов?",
            "Понимаешь ли базовые обозначения фронта, окружений и передвижения войск?",
            "Какими инструментами или ИИ пользуешься в работе (если пользуешься)?",
            "Насколько быстро можешь обновлять карту в разгар войны?",
        ],
    },
    "technologist": {
        "title": "Технолог",
        "short": "Технолог",
        "emoji": "🔧",
        "desc": "проверяет выдуманные игроками технологии, даёт согласие или отказ на разработку и создание с учётом ТТХ, БЧ, уязвимостей и сроков.",
        "questions": [
            "Есть ли опыт проверки технологий или ТТХ техники? На что смотришь в первую очередь (БЧ, дальность, ПВО, логистика, импорт)?",
            "Что ответишь игроку, который пытается без базы за короткий срок создать нереалистичную разработку?",
            "Каким ИИ пользуешься для проверки ТТХ, физики и фактчекинга?",
            "Сколько времени в день готов тратить на разбор тех заявок?",
        ],
    },
    "un": {
        "title": "ООН",
        "short": "ООН",
        "emoji": "🌐",
        "desc": "отыгрывает за ООН, ведёт международные заседания и резолюции. Это не мешает играть за свою страну.",
        "questions": [
            "Есть ли опыт отыгрыша ООН или международных организаций в ВПИ?",
            "Понимаешь ли, что игра за ООН требует нейтралитета от лица организации, даже если у тебя есть своя страна?",
            "Каким ИИ пользуешься для составления резолюций и повесток?",
            "Сколько времени готов уделять площадке ООН?",
        ],
    },
    "moderator": {
        "title": "Модератор",
        "short": "Модератор",
        "emoji": "👮",
        "desc": "следит за порядком в чате и темах, пресекает нарушения правил.",
        "questions": [
            "Есть ли опыт модерации Telegram чатов и супергрупп с темами?",
            "Как поступишь при провокациях, оффтопе или переходе на личности в чате?",
            "Каким ИИ или инструментами пользуешься (если пользуешься)?",
            "В какое время суток ты обычно онлайн?",
        ],
    },
    "pr": {
        "title": "Пиар менеджер",
        "short": "Пиар менеджер",
        "emoji": "📩",
        "desc": "реклама проекта в TikTok и на любых других площадках для привлечения новых игроков.",
        "questions": [
            "На каких площадках планируешь продвигать проект (TikTok, Shorts, Reels, Telegram и т.д.)?",
            "Есть ли опыт монтажа видео или ведения пабликов? Опиши формат роликов.",
            "Каким ИИ пользуешься для сценариев, озвучки или визуала?",
            "Сколько публикаций в неделю готов делать?",
        ],
    },
}

KPI_ACTION_TYPES = {
    "tech": "Проверка технологии",
    "rp": "Откат / разбор РП",
    "event": "Проведение ивента",
    "map": "Обновление карты",
    "mod": "Модерация чата",
    "un": "Деятельность ООН",
    "pr": "Пиар публикация",
    "cur": "Кураторский разбор",
}


def format_eternal_link(user_id: int, full_name: str, username: str = "") -> str:
    label = esc(full_name or str(user_id))
    uname = (username or "").strip().lstrip("@")
    link = f'<a href="tg://user?id={user_id}">{label}</a>'
    if uname:
        return f"{link} (@{esc(uname)})"
    return link


def format_vac_date(iso_date: str) -> str:
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return dt.strftime("%d.%m")
    except Exception:
        return iso_date


# ===================== ТЕКСТЫ (VIEWS) =====================

class Views:
    @staticmethod
    def roles_explanation() -> str:
        lines = [
            "<b>Куратор (военный / экономический):</b> отвечает за военную или экономическую сферу, разбирает спорные ситуации и помогает игрокам.",
            f"<b>Трекер:</b> {ROLES['tracker']['desc']}",
            f"<b>Ивентолог:</b> {ROLES['eventolog']['desc']}",
            "<b>Маппер (обычный / вар маппер):</b> картограф основной карты или карты военных действий. Освоить несложно.",
            f"<b>Технолог:</b> {ROLES['technologist']['desc']}",
            f"<b>ООН:</b> {ROLES['un']['desc']}",
            f"<b>Модератор:</b> {ROLES['moderator']['desc']}",
            f"<b>Пиар менеджер:</b> {ROLES['pr']['desc']}",
        ]
        body = "\n\n".join(lines)
        return f"{pe('ℹ️')} <b>Пояснение должностей Rise of Europe</b>\n\n{_blockquote(body)}"

    @staticmethod
    def build_roster_text(
        active_owners: List[dict],
        pending_owners: List[dict],
        staff_by_role: Dict[str, List[dict]],
        pending_by_role: Dict[str, List[str]],
        limits: Dict[str, int],
    ) -> str:
        parts = [
            f"{pe('👑')} <b>СОСТАВ АДМИНИСТРАЦИИ RISE OF EUROPE</b>",
            "─────────────────────",
            f"{pe('ℹ️')} <i>Актуальный список. Пишите нужному специалисту по его профилю, ссылки кликабельны по ID.</i>",
        ]

        own_lines = []
        for o in active_owners:
            link = format_eternal_link(o["user_id"], o.get("full_name", str(o["user_id"])), o.get("username", ""))
            own_lines.append(f"• {link} · <i>{esc(o.get('role_title', 'Совладелец'))}</i>")
        for po in pending_owners:
            own_lines.append(f"• @{esc(po['username'])} · <i>{esc(po.get('role_title', 'Совладелец'))}</i>")

        if own_lines:
            parts.append(f"\n<b>Руководство проекта</b>\n{_blockquote(chr(10).join(own_lines))}")

        for rk, meta in ROLES.items():
            members = staff_by_role.get(rk, [])
            pendings = pending_by_role.get(rk, [])
            lim = limits.get(rk, -1)
            occ = len(members) + len(pendings)

            if lim >= 0:
                header_cnt = f"({occ}/{lim})"
            else:
                header_cnt = f"({occ})" if occ > 0 else ""

            role_lines = []
            for m in members:
                link = format_eternal_link(m["user_id"], m.get("full_name", str(m["user_id"])), m.get("username", ""))
                vac = f" <i>(в отпуске до {format_vac_date(m['vacation_until'])})</i>" if m.get("vacation_until") else ""
                role_lines.append(f"• {link}{vac}")
            for puname in pendings:
                role_lines.append(f"• @{esc(puname)}")

            if not role_lines:
                if lim == 0:
                    role_lines.append("<i>Набор закрыт</i>")
                elif lim > 0:
                    role_lines.append(f"<i>Вакантно (мест: {lim})</i>")
                else:
                    role_lines.append("<i>Вакантно</i>")

            block_title = f"{meta['title']} {header_cnt}".strip()
            parts.append(f"\n<b>{esc(block_title)}</b>\n{_blockquote(chr(10).join(role_lines))}")

        return "\n".join(parts)


# ===================== КЛАВИАТУРЫ =====================

def main_menu_kb(is_staff: bool = False, is_owner_flag: bool = False, on_vacation: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(btn("Пояснение должностей", "roles_info", "ℹ️"))
    builder.row(btn("Подать заявку в админку", "apply_start", "✏️"))
    builder.row(btn("Актуальный состав", "view_roster", "👥"))
    if is_staff:
        builder.row(btn("Отметить работу (KPI)", "kpi_log_menu", "📊"))
        if on_vacation:
            builder.row(btn("Вернуться из отпуска", "vac_return", "✅"))
        else:
            builder.row(btn("Уйти в отпуск", "vac_start", "📅"))
    if is_owner_flag:
        builder.row(btn("Панель владельца", "owner_panel", "🔧"))
    return builder.as_markup()


def back_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(btn("Назад", "back_main", "🔙"))
    return builder.as_markup()


def apply_role_kb(limits: Dict[str, int], occupancy: Dict[str, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(btn("Пояснение должностей", "roles_info", "ℹ️"))

    def _lbl(rk: str, base: str) -> str:
        lim = limits.get(rk, -1)
        if lim < 0:
            return base
        free = max(0, lim - occupancy.get(rk, 0))
        return f"{base} (мест: {free})"

    builder.row(btn("Куратор (военный / экономический)", "role_grp_curator", "⚔️"))
    builder.row(
        btn(_lbl("tracker", "Трекер"), "apply_role_tracker", "🛡️"),
        btn(_lbl("eventolog", "Ивентолог"), "apply_role_eventolog", "📢"),
    )
    builder.row(btn("Маппер (обычный / вар маппер)", "role_grp_mapper", "🌍"))
    builder.row(
        btn(_lbl("technologist", "Технолог"), "apply_role_technologist", "🔧"),
        btn(_lbl("un", "ООН"), "apply_role_un", "🌐"),
    )
    builder.row(
        btn(_lbl("moderator", "Модератор"), "apply_role_moderator", "👮"),
        btn(_lbl("pr", "Пиар менеджер"), "apply_role_pr", "📩"),
    )
    builder.row(btn("Назад", "back_main", "🔙"))
    return builder.as_markup()


def subrole_curator_kb(limits: Dict[str, int], occupancy: Dict[str, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for rk in ("curator_mil", "curator_econ"):
        lim = limits.get(rk, -1)
        suffix = f" (мест: {max(0, lim - occupancy.get(rk, 0))})" if lim >= 0 else ""
        builder.row(btn(f"{ROLES[rk]['title']}{suffix}", f"apply_role_{rk}", ROLES[rk]["emoji"]))
    builder.row(btn("Назад", "apply_start", "🔙"))
    return builder.as_markup()


def subrole_mapper_kb(limits: Dict[str, int], occupancy: Dict[str, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for rk in ("mapper_regular", "mapper_war"):
        lim = limits.get(rk, -1)
        suffix = f" (мест: {max(0, lim - occupancy.get(rk, 0))})" if lim >= 0 else ""
        builder.row(btn(f"{ROLES[rk]['title']}{suffix}", f"apply_role_{rk}", ROLES[rk]["emoji"]))
    builder.row(btn("Назад", "apply_start", "🔙"))
    return builder.as_markup()


def apply_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        btn("Отправить заявку", "apply_submit", "✅"),
        btn("Отменить", "back_main", "❌"),
    )
    return builder.as_markup()


def app_decision_kb(app_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        btn("Принять", f"app_yes_{app_id}", "✅"),
        btn("Отклонить", f"app_no_{app_id}", "❌"),
    )
    builder.row(btn("Тихий ЧС", f"app_bl_{app_id}", "⛔"))
    return builder.as_markup()


def vacation_duration_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        btn("3 дня", "vac_days_3", "📅"),
        btn("5 дней", "vac_days_5", "📅"),
    )
    builder.row(
        btn("7 дней", "vac_days_7", "📅"),
        btn("14 дней", "vac_days_14", "📅"),
    )
    builder.row(btn("Указать свою дату (ДД.ММ)", "vac_custom_date", "✏️"))
    builder.row(btn("Назад", "back_main", "🔙"))
    return builder.as_markup()


def vacation_decision_kb(req_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        btn("Одобрить отпуск", f"vac_ok_{req_id}", "✅"),
        btn("Отклонить", f"vac_no_{req_id}", "❌"),
    )
    return builder.as_markup()


def kpi_action_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    items = list(KPI_ACTION_TYPES.items())
    for i in range(0, len(items), 2):
        row_btns = [btn(title, f"kpi_act_{key}") for key, title in items[i:i + 2]]
        builder.row(*row_btns)
    builder.row(btn("Назад", "back_main", "🔙"))
    return builder.as_markup()


def owner_panel_kb(apps_open: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(btn("Управление составом", "own_staff", "👥"))
    builder.row(btn("Руководство (влд / совлд)", "own_owners", "👑"))
    builder.row(btn("Лимиты мест по ролям", "own_limits", "🔧"))
    builder.row(btn("KPI отчёт (7д / 30д)", "own_kpi", "📊"))
    builder.row(btn("ЧС и кулдауны", "own_moderation", "⛔"))
    builder.row(btn("Обновить закреп в теме", "own_sync_roster", "🔄"))
    builder.row(btn("Настройки и рассылка", "own_settings", "📢"))
    toggle_txt = "Закрыть набор заявок" if apps_open else "Открыть набор заявок"
    builder.row(btn(toggle_txt, "own_toggle_apps", "❗"))
    builder.row(btn("Главное меню", "back_main", "🔙"))
    return builder.as_markup()


def back_owner_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(btn("В панель владельца", "owner_panel", "🔙"))
    return builder.as_markup()