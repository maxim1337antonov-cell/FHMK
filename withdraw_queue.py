"""
Очередь выплат: SQLite — источник правды, JSON — снимок для forwarder.
Формат задач тот же: wallet, amount, deal_id, user_id, timestamp, type.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from database import DB_PATH, db

logger = logging.getLogger(__name__)

QUEUE_FILE = Path(DB_PATH).with_name("withdraw_queue.json")

PAYOUTS_SQL = """
CREATE TABLE IF NOT EXISTS payouts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id INTEGER NOT NULL,
  user_id INTEGER,
  wallet TEXT NOT NULL,
  amount TEXT NOT NULL,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  admin_refund INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(deal_id, type)
);
CREATE INDEX IF NOT EXISTS idx_payouts_status ON payouts(status);
"""


async def ensure_payouts_table():
    async with db() as conn:
        await conn.executescript(PAYOUTS_SQL)
        await conn.commit()


def _now() -> str:
    return datetime.utcnow().isoformat()


def _row_to_task(row: dict) -> dict:
    task = {
        "wallet": row["wallet"],
        "amount": row["amount"],
        "deal_id": row["deal_id"],
        "user_id": row["user_id"],
        "timestamp": row.get("created_at"),
        "type": row["type"],
    }
    if row.get("admin_refund"):
        task["admin_refund"] = True
    return task


async def _pending_tasks() -> list[dict]:
    await ensure_payouts_table()
    async with db() as conn:
        cur = await conn.execute(
            "SELECT deal_id, user_id, wallet, amount, type, admin_refund, created_at "
            "FROM payouts WHERE status='pending' ORDER BY id ASC"
        )
        rows = await cur.fetchall()
    return [_row_to_task(dict(r)) for r in rows]


async def _write_json_snapshot(tasks: list[dict] | None = None):
    if tasks is None:
        tasks = await _pending_tasks()
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"⚠️ [PAYOUTS] Не удалось записать снимок JSON: {e}")


_migrated = False


async def _migrate_json_into_db():
    global _migrated
    if _migrated:
        return
    _migrated = True
    if not QUEUE_FILE.exists():
        return
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return
    if not isinstance(raw, list):
        return
    for task in raw:
        if not task.get("wallet") or not task.get("amount") or not task.get("deal_id"):
            continue
        await enqueue_payout(
            deal_id=int(task["deal_id"]),
            user_id=task.get("user_id"),
            wallet=str(task["wallet"]),
            amount=str(task["amount"]),
            payout_type=str(task.get("type") or "refund"),
            admin_refund=bool(task.get("admin_refund")),
            sync_json=False,
        )


async def enqueue_payout(
    *,
    deal_id: int,
    user_id: int | None,
    wallet: str,
    amount: str,
    payout_type: str,
    admin_refund: bool = False,
    sync_json: bool = True,
) -> dict:
    """
    Добавить выплату. Повтор deal_id+type игнорируется.
    Returns: {created: bool, success: bool}
    """
    await ensure_payouts_table()
    now = _now()
    created = False
    async with db() as conn:
        cur = await conn.execute(
            "INSERT OR IGNORE INTO payouts "
            "(deal_id, user_id, wallet, amount, type, status, admin_refund, created_at, updated_at) "
            "VALUES (?,?,?,?,?,'pending',?,?,?)",
            (
                int(deal_id),
                user_id,
                wallet.strip(),
                str(amount).strip(),
                str(payout_type),
                1 if admin_refund else 0,
                now,
                now,
            ),
        )
        await conn.commit()
        created = cur.rowcount == 1
    if created:
        logger.info(
            f"✅ [PAYOUTS] Задача {payout_type} для сделки #{deal_id} добавлена "
            f"({amount} BTC → {wallet})"
        )
    else:
        logger.warning(
            f"⚠️ [PAYOUTS] Задача {payout_type} для сделки #{deal_id} уже есть, дубликат пропущен"
        )
    if sync_json:
        await _write_json_snapshot()
    return {"success": True, "created": created}


async def load_withdraw_queue() -> list[dict]:
    """Очередь для forwarder: pending из БД (JSON мигрируется один раз)."""
    await ensure_payouts_table()
    await _migrate_json_into_db()
    tasks = await _pending_tasks()
    await _write_json_snapshot(tasks)
    return tasks


async def mark_payout_done(deal_id: int, payout_type: str | None = None) -> int:
    """Снять одну выплату (или все типы по deal_id). Не трогает чужие pending."""
    await ensure_payouts_table()
    now = _now()
    async with db() as conn:
        if payout_type:
            cur = await conn.execute(
                "UPDATE payouts SET status='done', updated_at=? "
                "WHERE deal_id=? AND type=? AND status='pending'",
                (now, int(deal_id), str(payout_type)),
            )
        else:
            cur = await conn.execute(
                "UPDATE payouts SET status='done', updated_at=? "
                "WHERE deal_id=? AND status='pending'",
                (now, int(deal_id)),
            )
        await conn.commit()
        n = cur.rowcount
    await _write_json_snapshot()
    return n


async def save_withdraw_queue(queue: list | None):
    """
    Совместимость с forwarder: JSON-снимок.
    Снятие задачи из очереди — только через mark_payout_done,
    чтобы параллельный enqueue не пометить done.
    """
    await ensure_payouts_table()
    await _write_json_snapshot()
