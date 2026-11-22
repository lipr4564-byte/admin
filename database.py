"""
Слой хранения данных (SQLite WAL + транзакции + миграции).
Содержит fallback на встроенный sqlite3, если aiosqlite не установлен на хостинге.
"""

import asyncio
import json
import sqlite3
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, date
from typing import Optional, Dict, List, Tuple

try:
    import aiosqlite
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", "aiosqlite"])
        import aiosqlite
    except Exception:
        class _AsyncCursor:
            def __init__(self, cur: sqlite3.Cursor):
                self._cur = cur
                self.lastrowid = cur.lastrowid
                self.rowcount = cur.rowcount

            async def fetchone(self):
                return self._cur.fetchone()

            async def fetchall(self):
                return self._cur.fetchall()

        class _ExecuteContext:
            def __init__(self, conn: sqlite3.Connection, sql: str, params: tuple):
                self._conn = conn
                self._sql = sql
                self._params = params
                self._cur = None

            def _run(self):
                if self._cur is None:
                    self._cur = _AsyncCursor(self._conn.execute(self._sql, self._params))
                return self._cur

            def __await__(self):
                async def _coro():
                    return self._run()
                return _coro().__await__()

            async def __aenter__(self):
                return self._run()

            async def __aexit__(self, exc_type, exc, tb):
                if self._cur is not None:
                    self._cur._cur.close()

        class _AsyncConnection:
            def __init__(self, db_path: str, timeout: float = 10.0):
                self._conn = sqlite3.connect(db_path, timeout=timeout, check_same_thread=False)

            @property
            def row_factory(self):
                return self._conn.row_factory

            @row_factory.setter
            def row_factory(self, val):
                self._conn.row_factory = val

            def execute(self, sql: str, parameters: tuple = ()):
                return _ExecuteContext(self._conn, sql, parameters)

            async def commit(self):
                self._conn.commit()

            async def rollback(self):
                self._conn.rollback()

            async def close(self):
                self._conn.close()

        class _FallbackAioSqlite:
            Row = sqlite3.Row
            Connection = _AsyncConnection

            @staticmethod
            async def connect(db_path: str, timeout: float = 10.0):
                return _AsyncConnection(db_path, timeout=timeout)

        aiosqlite = _FallbackAioSqlite()

from config import CFG


ROLE_KEYS = [
    "curator_mil",
    "curator_econ",
    "tracker",
    "eventolog",
    "mapper_regular",
    "mapper_war",
    "technologist",
    "un",
    "moderator",
    "pr",
]

MIGRATIONS: Dict[int, List[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            blacklisted INTEGER NOT NULL DEFAULT 0
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(LOWER(username));",
        """
        CREATE TABLE IF NOT EXISTS owners (
            user_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            full_name TEXT NOT NULL DEFAULT '',
            role_title TEXT NOT NULL DEFAULT 'Совладелец',
            is_primary INTEGER NOT NULL DEFAULT 0,
            added_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS pending_owners (
            username TEXT PRIMARY KEY,
            role_title TEXT NOT NULL DEFAULT 'Совладелец',
            added_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS role_limits (
            role_key TEXT PRIMARY KEY,
            max_slots INTEGER NOT NULL DEFAULT -1
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS staff_roles (
            user_id INTEGER NOT NULL,
            role_key TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            PRIMARY KEY (user_id, role_key)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS pending_staff (
            username TEXT NOT NULL,
            role_key TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            PRIMARY KEY (username, role_key)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS vacations (
            user_id INTEGER PRIMARY KEY,
            until_date TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            approved_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS vacation_requests (
            req_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            until_date TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role_key TEXT NOT NULL,
            answers_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by INTEGER DEFAULT NULL,
            created_at TEXT NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);",
        """
        CREATE TABLE IF NOT EXISTS cooldowns (
            user_id INTEGER NOT NULL,
            role_key TEXT NOT NULL,
            until_ts INTEGER NOT NULL,
            PRIMARY KEY (user_id, role_key)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS kpi_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_ts INTEGER NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_kpi_user_ts ON kpi_actions(user_id, created_ts);",
        """
        CREATE TABLE IF NOT EXISTS group_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            msg_ts INTEGER NOT NULL
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_grp_user_ts ON group_activity(user_id, msg_ts);",
    ]
}

DEFAULT_SETTINGS = {
    "applications_open": "1",
    "cooldown_days": "3",
    "roster_msg_id": "0",
    "staff_topic_id": str(CFG.staff_topic_id),
}


class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    async def connect(self):
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path, timeout=10.0)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode = WAL;")
            await self._conn.execute("PRAGMA synchronous = NORMAL;")
            await self._conn.execute("PRAGMA foreign_keys = ON;")
            await self._conn.execute("PRAGMA busy_timeout = 5000;")
            await self._run_migrations()
            await self._init_defaults()

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    @asynccontextmanager
    async def transaction(self):
        await self.connect()
        async with self._write_lock:
            await self._conn.execute("BEGIN IMMEDIATE;")
            try:
                yield self._conn
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

    @property
    def conn(self) -> aiosqlite.Connection:
        return self._conn

    async def _run_migrations(self):
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);"
        )
        async with self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations;") as cur:
            row = await cur.fetchone()
            current_ver = row[0] if row else 0

        for ver in sorted(MIGRATIONS.keys()):
            if ver > current_ver:
                for sql in MIGRATIONS[ver]:
                    try:
                        await self._conn.execute(sql)
                    except Exception:
                        pass
                await self._conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                    (ver, datetime.now().isoformat()),
                )
        await self._conn.commit()

    async def _init_defaults(self):
        now = datetime.now().isoformat()
        async with self.transaction() as conn:
            for k, v in DEFAULT_SETTINGS.items():
                await conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?);",
                    (k, v),
                )
            for rk in ROLE_KEYS:
                await conn.execute(
                    "INSERT OR IGNORE INTO role_limits (role_key, max_slots) VALUES (?, -1);",
                    (rk,),
                )
            if CFG.primary_owner_id > 0:
                await conn.execute(
                    """
                    INSERT INTO owners (user_id, username, full_name, role_title, is_primary, added_at)
                    VALUES (?, ?, ?, 'Владелец', 1, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        is_primary = 1,
                        username = CASE WHEN excluded.username != '' THEN excluded.username ELSE owners.username END;
                    """,
                    (CFG.primary_owner_id, CFG.primary_owner_username, CFG.primary_owner_username, now),
                )


db_manager = DatabaseManager(CFG.sqlite_path)


async def init_db():
    await db_manager.connect()


async def close_db():
    await db_manager.close()


# ===================== НАСТРОЙКИ =====================

async def get_setting(key: str) -> str:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT value FROM settings WHERE key = ?;", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


async def get_int_setting(key: str) -> int:
    val = await get_setting(key)
    return int(val) if val.lstrip("-").isdigit() else int(DEFAULT_SETTINGS.get(key, "0"))


async def set_setting(key: str, value) -> None:
    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
            (key, str(value)),
        )


async def get_all_settings() -> dict:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT key, value FROM settings;") as cur:
        rows = await cur.fetchall()
    res = dict(DEFAULT_SETTINGS)
    for r in rows:
        res[r["key"]] = r["value"]
    return {
        "applications_open": res["applications_open"] == "1",
        "cooldown_days": int(res["cooldown_days"]),
        "roster_msg_id": int(res["roster_msg_id"]),
        "staff_topic_id": int(res["staff_topic_id"]),
    }


# ===================== ПОЛЬЗОВАТЕЛИ, ВЛАДЕЛЬЦЫ И ЧС =====================

async def register_user_activity(user_id: int, username: str, full_name: str, is_group_msg: bool = False) -> Tuple[dict, bool]:
    now = datetime.now().isoformat()
    now_ts = int(datetime.now().timestamp())
    uname = (username or "").strip().lstrip("@")
    fname = (full_name or str(user_id)).strip()
    need_refresh = False

    async with db_manager.transaction() as conn:
        async with conn.execute("SELECT username, full_name FROM users WHERE user_id = ?;", (user_id,)) as cur:
            old_u = await cur.fetchone()

        await conn.execute(
            """
            INSERT INTO users (user_id, username, full_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = CASE WHEN excluded.username != '' THEN excluded.username ELSE users.username END,
                full_name = CASE WHEN excluded.full_name != '' THEN excluded.full_name ELSE users.full_name END,
                last_seen = excluded.last_seen;
            """,
            (user_id, uname, fname, now, now),
        )

        if old_u and (old_u["username"] != uname or old_u["full_name"] != fname):
            async with conn.execute("SELECT 1 FROM staff_roles WHERE user_id = ? LIMIT 1;", (user_id,)) as scur:
                is_s = await scur.fetchone()
            async with conn.execute("SELECT 1 FROM owners WHERE user_id = ? LIMIT 1;", (user_id,)) as ocur:
                is_o = await ocur.fetchone()
            if is_s or is_o:
                need_refresh = True

        await conn.execute(
            "UPDATE owners SET username = ?, full_name = ? WHERE user_id = ?;",
            (uname, fname, user_id),
        )

        if uname:
            async with conn.execute(
                "SELECT * FROM pending_owners WHERE LOWER(username) = LOWER(?);",
                (uname,),
            ) as pocur:
                po = await pocur.fetchone()
            if po:
                await conn.execute(
                    """
                    INSERT INTO owners (user_id, username, full_name, role_title, is_primary, added_at)
                    VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(user_id) DO UPDATE SET role_title = excluded.role_title, username = excluded.username, full_name = excluded.full_name;
                    """,
                    (user_id, uname, fname, po["role_title"], po["added_at"]),
                )
                await conn.execute("DELETE FROM pending_owners WHERE LOWER(username) = LOWER(?);", (uname,))
                need_refresh = True

            async with conn.execute(
                "SELECT * FROM pending_staff WHERE LOWER(username) = LOWER(?);",
                (uname,),
            ) as pscur:
                ps_rows = await pscur.fetchall()
            if ps_rows:
                for r in ps_rows:
                    await conn.execute(
                        "INSERT OR IGNORE INTO staff_roles (user_id, role_key, assigned_at) VALUES (?, ?, ?);",
                        (user_id, r["role_key"], r["assigned_at"]),
                    )
                await conn.execute("DELETE FROM pending_staff WHERE LOWER(username) = LOWER(?);", (uname,))
                need_refresh = True

        if is_group_msg:
            async with conn.execute("SELECT 1 FROM staff_roles WHERE user_id = ? LIMIT 1;", (user_id,)) as scur:
                if await scur.fetchone():
                    await conn.execute(
                        "INSERT INTO group_activity (user_id, msg_ts) VALUES (?, ?);",
                        (user_id, now_ts),
                    )

    u = await get_user_data(user_id)
    return u, need_refresh


async def get_user_data(user_id: int) -> Optional[dict]:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT * FROM users WHERE user_id = ?;", (user_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def find_user_by_query(query: str) -> Optional[dict]:
    await db_manager.connect()
    q = query.strip().lstrip("@")
    if q.isdigit():
        u = await get_user_data(int(q))
        if u:
            return u
    async with db_manager.conn.execute(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(full_name) = LOWER(?) LIMIT 1;",
        (q, q),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def is_owner(user) -> bool:
    if not user:
        return False
    if user.id == CFG.primary_owner_id and CFG.primary_owner_id > 0:
        return True
    uname = (user.username or "").strip().lstrip("@").lower()
    if uname and uname == CFG.primary_owner_username.lower():
        return True
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT 1 FROM owners WHERE user_id = ?;", (user.id,)) as cur:
        if await cur.fetchone():
            return True
    if uname:
        async with db_manager.conn.execute(
            "SELECT 1 FROM pending_owners WHERE LOWER(username) = LOWER(?);",
            (uname,),
        ) as cur2:
            if await cur2.fetchone():
                return True
    return False


async def is_blacklisted(user_id: int) -> bool:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT blacklisted FROM users WHERE user_id = ?;", (user_id,)) as cur:
        row = await cur.fetchone()
    return bool(row and row["blacklisted"] == 1)


async def set_blacklist(user_id: int, value: bool) -> None:
    now = datetime.now().isoformat()
    async with db_manager.transaction() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, full_name, first_seen, last_seen, blacklisted)
            VALUES (?, '', ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET blacklisted = excluded.blacklisted;
            """,
            (user_id, str(user_id), now, now, 1 if value else 0),
        )


async def get_blacklisted_users() -> List[dict]:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT * FROM users WHERE blacklisted = 1 ORDER BY last_seen DESC;") as cur:
        return [dict(r) for r in await cur.fetchall()]


async def get_all_users() -> List[dict]:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT * FROM users ORDER BY last_seen DESC;") as cur:
        return [dict(r) for r in await cur.fetchall()]


async def get_owners() -> Tuple[List[dict], List[dict]]:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT * FROM owners ORDER BY is_primary DESC, added_at ASC;") as cur:
        active = [dict(r) for r in await cur.fetchall()]
    async with db_manager.conn.execute("SELECT * FROM pending_owners ORDER BY added_at ASC;") as cur2:
        pending = [dict(r) for r in await cur2.fetchall()]
    return active, pending


async def get_owner_ids() -> List[int]:
    active, _ = await get_owners()
    ids = {o["user_id"] for o in active if o["user_id"] > 0}
    if CFG.primary_owner_id > 0:
        ids.add(CFG.primary_owner_id)
    return list(ids)


async def add_owner_entry(query: str, role_title: str = "Совладелец") -> Tuple[bool, str]:
    q = query.strip().lstrip("@")
    if not q:
        return False, "Пустой ввод"
    now = datetime.now().isoformat()

    u = await find_user_by_query(q)
    if u:
        async with db_manager.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO owners (user_id, username, full_name, role_title, is_primary, added_at)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(user_id) DO UPDATE SET role_title = excluded.role_title;
                """,
                (u["user_id"], u["username"], u["full_name"], role_title, now),
            )
        return True, f"Добавлен в руководство: {u['full_name']} (@{u['username'] or 'нет'})"

    if q.isdigit():
        uid = int(q)
        async with db_manager.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO users (user_id, username, full_name, first_seen, last_seen)
                VALUES (?, '', ?, ?, ?) ON CONFLICT(user_id) DO NOTHING;
                """,
                (uid, str(uid), now, now),
            )
            await conn.execute(
                """
                INSERT INTO owners (user_id, username, full_name, role_title, is_primary, added_at)
                VALUES (?, '', ?, ?, 0, ?)
                ON CONFLICT(user_id) DO UPDATE SET role_title = excluded.role_title;
                """,
                (uid, str(uid), role_title, now),
            )
        return True, f"Добавлен по ID {uid} ({role_title})"

    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO pending_owners (username, role_title, added_at) VALUES (?, ?, ?);",
            (q, role_title, now),
        )
    return True, f"@{q} добавлен как {role_title} (ID привяжется при первом контакте с ботом)"


async def remove_owner_by_id(user_id: int) -> bool:
    if user_id == CFG.primary_owner_id:
        return False
    async with db_manager.transaction() as conn:
        await conn.execute("DELETE FROM owners WHERE user_id = ? AND is_primary = 0;", (user_id,))
    return True


async def remove_pending_owner(username: str) -> None:
    async with db_manager.transaction() as conn:
        await conn.execute("DELETE FROM pending_owners WHERE LOWER(username) = LOWER(?);", (username,))


# ===================== РОЛИ, ЛИМИТЫ И СОСТАВ =====================

async def get_role_limits() -> Dict[str, int]:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT role_key, max_slots FROM role_limits;") as cur:
        rows = await cur.fetchall()
    res = {rk: -1 for rk in ROLE_KEYS}
    for r in rows:
        res[r["role_key"]] = r["max_slots"]
    return res


async def set_role_limit(role_key: str, max_slots: int) -> None:
    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT INTO role_limits (role_key, max_slots) VALUES (?, ?) ON CONFLICT(role_key) DO UPDATE SET max_slots = excluded.max_slots;",
            (role_key, max_slots),
        )


async def get_role_occupancy(role_key: str) -> int:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT COUNT(*) FROM staff_roles WHERE role_key = ?;", (role_key,)) as cur:
        c1 = (await cur.fetchone())[0]
    async with db_manager.conn.execute("SELECT COUNT(*) FROM pending_staff WHERE role_key = ?;", (role_key,)) as cur2:
        c2 = (await cur2.fetchone())[0]
    return c1 + c2


async def get_role_free_slots(role_key: str) -> Optional[int]:
    limits = await get_role_limits()
    lim = limits.get(role_key, -1)
    if lim < 0:
        return None
    occ = await get_role_occupancy(role_key)
    return max(0, lim - occ)


async def get_user_roles(user_id: int) -> List[str]:
    await db_manager.connect()
    async with db_manager.conn.execute(
        "SELECT role_key FROM staff_roles WHERE user_id = ? ORDER BY assigned_at ASC;",
        (user_id,),
    ) as cur:
        return [r["role_key"] for r in await cur.fetchall()]


async def assign_staff_role(user_id: int, role_key: str) -> None:
    now = datetime.now().isoformat()
    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO staff_roles (user_id, role_key, assigned_at) VALUES (?, ?, ?);",
            (user_id, role_key, now),
        )


async def assign_staff_by_query(query: str, role_key: str) -> Tuple[bool, str]:
    q = query.strip().lstrip("@")
    if not q:
        return False, "Пустой ввод"
    now = datetime.now().isoformat()

    u = await find_user_by_query(q)
    if u:
        await assign_staff_role(u["user_id"], role_key)
        return True, f"Назначен: {u['full_name']} (@{u['username'] or 'нет'})"

    if q.isdigit():
        uid = int(q)
        async with db_manager.transaction() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO users (user_id, username, full_name, first_seen, last_seen) VALUES (?, '', ?, ?, ?);",
                (uid, str(uid), now, now),
            )
            await conn.execute(
                "INSERT OR IGNORE INTO staff_roles (user_id, role_key, assigned_at) VALUES (?, ?, ?);",
                (uid, role_key, now),
            )
        return True, f"Назначен по ID {uid}"

    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO pending_staff (username, role_key, assigned_at) VALUES (?, ?, ?);",
            (q, role_key, now),
        )
    return True, f"@{q} назначен на должность (ID подвяжется автоматически при активности)"


async def remove_staff_role(user_id: int, role_key: str) -> None:
    async with db_manager.transaction() as conn:
        await conn.execute(
            "DELETE FROM staff_roles WHERE user_id = ? AND role_key = ?;",
            (user_id, role_key),
        )


async def remove_all_staff_roles(user_id: int) -> List[str]:
    roles = await get_user_roles(user_id)
    async with db_manager.transaction() as conn:
        await conn.execute("DELETE FROM staff_roles WHERE user_id = ?;", (user_id,))
        await conn.execute("DELETE FROM vacations WHERE user_id = ?;", (user_id,))
    return roles


async def remove_pending_staff_role(username: str, role_key: str) -> None:
    async with db_manager.transaction() as conn:
        await conn.execute(
            "DELETE FROM pending_staff WHERE LOWER(username) = LOWER(?) AND role_key = ?;",
            (username, role_key),
        )


async def get_all_staff_grouped() -> Tuple[Dict[str, List[dict]], Dict[str, List[str]]]:
    await db_manager.connect()
    staff_by_role: Dict[str, List[dict]] = {rk: [] for rk in ROLE_KEYS}
    pending_by_role: Dict[str, List[str]] = {rk: [] for rk in ROLE_KEYS}

    async with db_manager.conn.execute(
        """
        SELECT sr.role_key, u.user_id, u.username, u.full_name, u.last_seen, v.until_date AS vacation_until
        FROM staff_roles sr
        JOIN users u ON u.user_id = sr.user_id
        LEFT JOIN vacations v ON v.user_id = sr.user_id
        ORDER BY sr.assigned_at ASC;
        """
    ) as cur:
        for r in await cur.fetchall():
            if r["role_key"] in staff_by_role:
                staff_by_role[r["role_key"]].append(dict(r))

    async with db_manager.conn.execute("SELECT username, role_key FROM pending_staff ORDER BY assigned_at ASC;") as cur2:
        for r in await cur2.fetchall():
            if r["role_key"] in pending_by_role:
                pending_by_role[r["role_key"]].append(r["username"])

    return staff_by_role, pending_by_role


async def get_all_active_staff_members() -> List[dict]:
    await db_manager.connect()
    async with db_manager.conn.execute(
        """
        SELECT u.user_id, u.username, u.full_name, u.last_seen, v.until_date AS vacation_until,
               GROUP_CONCAT(sr.role_key) AS roles_csv
        FROM staff_roles sr
        JOIN users u ON u.user_id = sr.user_id
        LEFT JOIN vacations v ON v.user_id = sr.user_id
        GROUP BY u.user_id
        ORDER BY MIN(sr.assigned_at) ASC;
        """
    ) as cur:
        rows = []
        for r in await cur.fetchall():
            d = dict(r)
            d["roles"] = d["roles_csv"].split(",") if d.get("roles_csv") else []
            rows.append(d)
        return rows


# ===================== ЗАЯВКИ И КУЛДАУНЫ =====================

async def get_role_cooldown(user_id: int, role_key: str) -> int:
    now_ts = int(datetime.now().timestamp())
    await db_manager.connect()
    async with db_manager.conn.execute(
        "SELECT until_ts FROM cooldowns WHERE user_id = ? AND role_key = ?;",
        (user_id, role_key),
    ) as cur:
        row = await cur.fetchone()
    if row and row["until_ts"] > now_ts:
        return row["until_ts"] - now_ts
    return 0


async def set_role_cooldown(user_id: int, role_key: str, days: int) -> None:
    until_ts = int(datetime.now().timestamp()) + max(0, days) * 86400
    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO cooldowns (user_id, role_key, until_ts) VALUES (?, ?, ?);",
            (user_id, role_key, until_ts),
        )


async def clear_user_cooldowns(user_id: int) -> None:
    async with db_manager.transaction() as conn:
        await conn.execute("DELETE FROM cooldowns WHERE user_id = ?;", (user_id,))


async def has_pending_application(user_id: int, role_key: str) -> bool:
    await db_manager.connect()
    async with db_manager.conn.execute(
        "SELECT 1 FROM applications WHERE user_id = ? AND role_key = ? AND status = 'pending' LIMIT 1;",
        (user_id, role_key),
    ) as cur:
        return await cur.fetchone() is not None


async def create_application(user_id: int, role_key: str, answers: List[Tuple[str, str]]) -> int:
    now = datetime.now().isoformat()
    async with db_manager.transaction() as conn:
        cur = await conn.execute(
            "INSERT INTO applications (user_id, role_key, answers_json, status, created_at) VALUES (?, ?, ?, 'pending', ?);",
            (user_id, role_key, json.dumps(answers, ensure_ascii=False), now),
        )
        return cur.lastrowid


async def get_application(app_id: int) -> Optional[dict]:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT * FROM applications WHERE id = ?;", (app_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["answers"] = json.loads(d["answers_json"])
    return d


async def resolve_application(app_id: int, status: str, reviewer_id: int) -> Optional[dict]:
    async with db_manager.transaction() as conn:
        async with conn.execute("SELECT * FROM applications WHERE id = ? AND status = 'pending';", (app_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await conn.execute(
            "UPDATE applications SET status = ?, reviewed_by = ? WHERE id = ?;",
            (status, reviewer_id, app_id),
        )
        if status == "approved":
            await conn.execute(
                "INSERT OR IGNORE INTO staff_roles (user_id, role_key, assigned_at) VALUES (?, ?, ?);",
                (row["user_id"], row["role_key"], datetime.now().isoformat()),
            )
    return await get_application(app_id)


# ===================== ОТПУСКА / НЕАКТИВ =====================

async def get_user_vacation(user_id: int) -> Optional[dict]:
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT * FROM vacations WHERE user_id = ?;", (user_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def create_vacation_request(user_id: int, until_date: str, reason: str) -> str:
    req_id = uuid.uuid4().hex[:8]
    now = datetime.now().isoformat()
    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT INTO vacation_requests (req_id, user_id, until_date, reason, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?);",
            (req_id, user_id, until_date, reason, now),
        )
    return req_id


async def resolve_vacation_request(req_id: str, approve: bool, reviewer_id: int) -> Optional[dict]:
    now = datetime.now().isoformat()
    async with db_manager.transaction() as conn:
        async with conn.execute("SELECT * FROM vacation_requests WHERE req_id = ? AND status = 'pending';", (req_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        new_status = "approved" if approve else "rejected"
        await conn.execute("UPDATE vacation_requests SET status = ? WHERE req_id = ?;", (new_status, req_id))
        if approve:
            await conn.execute(
                """
                INSERT INTO vacations (user_id, until_date, reason, approved_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    until_date = excluded.until_date,
                    reason = excluded.reason,
                    approved_by = excluded.approved_by,
                    created_at = excluded.created_at;
                """,
                (row["user_id"], row["until_date"], row["reason"], reviewer_id, now),
            )
    d = dict(row)
    d["status"] = new_status
    return d


async def end_user_vacation(user_id: int) -> bool:
    async with db_manager.transaction() as conn:
        cur = await conn.execute("DELETE FROM vacations WHERE user_id = ?;", (user_id,))
        return (cur.rowcount or 0) > 0


async def clear_expired_vacations() -> List[int]:
    today_iso = date.today().isoformat()
    await db_manager.connect()
    async with db_manager.conn.execute("SELECT user_id FROM vacations WHERE until_date < ?;", (today_iso,)) as cur:
        expired = [r["user_id"] for r in await cur.fetchall()]
    if expired:
        async with db_manager.transaction() as conn:
            await conn.execute("DELETE FROM vacations WHERE until_date < ?;", (today_iso,))
    return expired


# ===================== KPI И АКТИВНОСТЬ =====================

async def record_kpi_action(user_id: int, action_type: str, note: str = "") -> None:
    now_ts = int(datetime.now().timestamp())
    async with db_manager.transaction() as conn:
        await conn.execute(
            "INSERT INTO kpi_actions (user_id, action_type, note, created_ts) VALUES (?, ?, ?, ?);",
            (user_id, action_type, note, now_ts),
        )


async def get_kpi_report() -> List[dict]:
    now_ts = int(datetime.now().timestamp())
    ts_7d = now_ts - 7 * 86400
    ts_30d = now_ts - 30 * 86400

    async with db_manager.transaction() as conn:
        await conn.execute("DELETE FROM group_activity WHERE msg_ts < ?;", (now_ts - 35 * 86400,))

    staff = await get_all_active_staff_members()
    report = []
    for s in staff:
        uid = s["user_id"]
        async with db_manager.conn.execute(
            "SELECT COUNT(*) FROM kpi_actions WHERE user_id = ? AND created_ts >= ?;",
            (uid, ts_7d),
        ) as c1:
            act_7 = (await c1.fetchone())[0]
        async with db_manager.conn.execute(
            "SELECT COUNT(*) FROM kpi_actions WHERE user_id = ? AND created_ts >= ?;",
            (uid, ts_30d),
        ) as c2:
            act_30 = (await c2.fetchone())[0]
        async with db_manager.conn.execute(
            "SELECT COUNT(*) FROM group_activity WHERE user_id = ? AND msg_ts >= ?;",
            (uid, ts_7d),
        ) as c3:
            msg_7 = (await c3.fetchone())[0]
        async with db_manager.conn.execute(
            "SELECT COUNT(*) FROM group_activity WHERE user_id = ? AND msg_ts >= ?;",
            (uid, ts_30d),
        ) as c4:
            msg_30 = (await c4.fetchone())[0]

        s["act_7"] = act_7
        s["act_30"] = act_30
        s["msg_7"] = msg_7
        s["msg_30"] = msg_30
        report.append(s)

    report.sort(key=lambda x: (x["act_30"], x["msg_30"]), reverse=True)
    return report