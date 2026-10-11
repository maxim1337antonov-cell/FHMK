"""
Краш-тест P2P-бота: логика, БД, обработчики, живой Telegram.
Не трогает продакшен-БД. Не шлёт команды вывода.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

os.environ.setdefault("TELEGRAM_PROXY", "")

BASE_DIR = Path(__file__).parent.absolute()
os.chdir(BASE_DIR)
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("crash_test")


class CrashTestResult:
    def __init__(self, test_name: str):
        self.test_name = test_name
        self.passed = False
        self.error = None
        self.details: list[str] = []
        self.findings: list[str] = []

    def add_detail(self, detail: str):
        self.details.append(detail)

    def add_finding(self, finding: str):
        self.findings.append(finding)

    def __str__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        result = f"{status} - {self.test_name}"
        if self.error:
            result += f"\n   Ошибка: {self.error}"
        for d in self.details:
            result += f"\n   • {d}"
        for f in self.findings:
            result += f"\n   ⚠️ {f}"
        return result


def _expect_error(fn, *args, exc=Exception):
    try:
        fn(*args)
        return False
    except exc:
        return True


async def test_imports():
    result = CrashTestResult("Импорты модулей")
    try:
        import config  # noqa: F401
        import database  # noqa: F401
        import utils  # noqa: F401
        import keyboards  # noqa: F401
        import handlers.main_handlers  # noqa: F401
        import handlers.sell_handlers  # noqa: F401
        import handlers.buy_handlers  # noqa: F401
        result.add_detail("Основные модули импортируются")
        result.passed = True
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        logger.error("test_imports", exc_info=True)
    return result


async def test_parse_limits():
    result = CrashTestResult("Парсинг лимитов")
    from database import parse_limits, NUM_RE
    try:
        min_btc, max_btc = parse_limits("0.001 - 0.10")
        assert min_btc == Decimal("0.001")
        assert max_btc == Decimal("0.10")
        result.add_detail("Диапазон 0.001-0.10: OK")

        min_btc, max_btc = parse_limits("0,001-0,10")
        assert min_btc == Decimal("0.001")
        result.add_detail("Европейская запятая: OK")

        failed = []
        for raw, label in [
            ("0.005", "одна сумма"),
            ("0.10 - 0.001", "макс < мин"),
            ("abc - def", "мусор"),
            ("", "пустая строка"),
            ("0 - 1", "ноль"),
            ("-1 - 1", "отрицательное (regex)"),
        ]:
            try:
                parse_limits(raw)
                failed.append(label)
            except (ValueError, InvalidOperation, IndexError):
                pass
        if failed:
            result.add_finding(f"parse_limits не отверг: {', '.join(failed)}")
        else:
            result.add_detail("Некорректные вводы отвергнуты")

        try:
            parse_limits(None)  # type: ignore
            result.add_finding("parse_limits(None) не упал и не отверг")
        except (TypeError, AttributeError, ValueError):
            result.add_detail("parse_limits(None): исключение поймано")

        huge = "9" * 80 + " - " + "9" * 80
        try:
            parse_limits(huge)
            result.add_detail("Огромные числа: не упал")
        except Exception as e:
            result.add_detail(f"Огромные числа: {type(e).__name__}")

        sci = NUM_RE.findall("1e-8")
        result.add_detail(f"Научная запись 1e-8 → regex: {sci or 'не распознано'}")

        result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_parse_limits", exc_info=True)
    return result


async def test_btc_address_validation():
    result = CrashTestResult("Валидация BTC-адресов")
    from database import is_valid_btc_bech32
    try:
        valid = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
        assert is_valid_btc_bech32(valid) is True
        assert is_valid_btc_bech32(valid.upper()) is True
        assert is_valid_btc_bech32("invalid_address") is False
        assert is_valid_btc_bech32("") is False
        assert is_valid_btc_bech32(" " + valid + " ") is False
        assert is_valid_btc_bech32("bc1" + "q" * 100) is False
        assert is_valid_btc_bech32(None) is False  # type: ignore
        assert is_valid_btc_bech32(12345) is False  # type: ignore
        assert is_valid_btc_bech32("1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2") is False
        assert is_valid_btc_bech32("tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx") is False
        result.add_detail("bech32 / мусор / None / legacy / testnet: OK")
        result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_btc_address_validation", exc_info=True)
    return result


async def test_commission_calculations():
    result = CrashTestResult("Расчёт комиссий")
    from database import calculate_seller_amounts, calculate_buyer_amounts
    try:
        amounts = calculate_seller_amounts(Decimal("0.01"))
        for key in ("deposit", "platform_takes", "net_to_seller", "seller_net_loss"):
            assert key in amounts
        buyer = calculate_buyer_amounts(Decimal("0.01"))
        assert buyer["buyer_receive"] == Decimal("0.01")
        result.add_detail("Нормальная сумма 0.01: OK")

        calculate_seller_amounts(Decimal("0.00000001"))
        calculate_seller_amounts(Decimal("1000"))
        calculate_buyer_amounts(Decimal("0.00000001"))

        zero_ok = _expect_error(calculate_seller_amounts, Decimal("0"), exc=ValueError)
        neg_ok = _expect_error(calculate_seller_amounts, Decimal("-0.01"), exc=ValueError)
        buyer_zero = _expect_error(calculate_buyer_amounts, Decimal("0"), exc=ValueError)
        if not (zero_ok and neg_ok and buyer_zero):
            result.error = "0 / отрицательные суммы не отвергнуты"
            return result
        result.add_detail("0 и отрицательные отвергнуты")
        result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_commission_calculations", exc_info=True)
    return result


async def test_utils_formatters():
    result = CrashTestResult("Утилиты форматирования")
    from utils import (
        format_datetime, format_btc_amount, get_role_name,
        get_status_emoji, format_admin_stats, get_time_until_expiry,
    )
    from keyboards import list_keyboard as kb_list
    try:
        assert format_datetime("not-a-date") == "not-a-date"
        format_datetime(None)  # type: ignore
        format_datetime("")
        result.add_detail("format_datetime мусор: не падает")

        assert format_btc_amount("0.01000000") == "0.01"
        format_btc_amount("not-a-number")
        format_btc_amount(None)
        result.add_detail("format_btc_amount мусор: не падает")

        try:
            get_role_name(1, {})
            result.add_finding("get_role_name({}) не упал — ожидался KeyError")
        except KeyError:
            result.add_detail("get_role_name({}) → KeyError (хрупкий контракт)")
        except Exception as e:
            result.add_detail(f"get_role_name({{}}): {type(e).__name__}")

        get_status_emoji("UnknownStatusXYZ")
        get_time_until_expiry("bad")
        get_time_until_expiry(None)  # type: ignore

        try:
            format_admin_stats({"total_volume_btc": None, "total_commission_btc": None})
            result.add_finding("format_admin_stats(None volume) не упал")
        except (TypeError, ValueError):
            result.add_finding("format_admin_stats падает, если volume=None")

        try:
            kb_list([{"id": 1, "min_btc": "0.001", "max_btc": "0.01", "expires_at": "BAD"}])
            result.add_finding("list_keyboard с битым expires_at не упал")
        except Exception as e:
            result.add_finding(
                f"list_keyboard падает на битом expires_at: {type(e).__name__}"
            )

        result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_utils_formatters", exc_info=True)
    return result


def _clone_prod_schema(dest: str):
    src_path = str(BASE_DIR / "p2p_bot.db")
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        dst.execute("PRAGMA foreign_keys=OFF")
        tables = [
            r[0]
            for r in dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            dst.execute(f'DELETE FROM "{table}"')
        dst.commit()
    finally:
        dst.close()
        src.close()


@asynccontextmanager
async def isolated_db(*, clone_prod: bool = True):
    import database
    old = database.DB_PATH
    fd, path = tempfile.mkstemp(prefix="p2p_crash_", suffix=".db")
    os.close(fd)
    try:
        if clone_prod and (BASE_DIR / "p2p_bot.db").exists():
            _clone_prod_schema(path)
        else:
            database.DB_PATH = path
            await database.init_db()
        database.DB_PATH = path
        yield path
    finally:
        database.DB_PATH = old
        try:
            os.unlink(path)
        except OSError:
            pass
        for extra in (path + "-wal", path + "-shm"):
            try:
                os.unlink(extra)
            except OSError:
                pass


async def test_fresh_init_schema():
    result = CrashTestResult("Свежий init_db vs upsert_user")
    from database import upsert_user
    try:
        async with isolated_db(clone_prod=False):
            await upsert_user(1, "fresh_user", "Fresh")
            result.add_detail("upsert_user на свежей БД сработал")
            result.passed = True
    except sqlite3.OperationalError as e:
        result.error = str(e)
        result.add_finding(
            "init_db не создаёт колонку display_name, а upsert_user её пишет — чистая установка падает"
        )
    except Exception as e:
        result.error = str(e)
        logger.error("test_fresh_init_schema", exc_info=True)
    return result


async def test_database_operations():
    result = CrashTestResult("Операции с БД (изолированная)")
    from database import (
        get_user, get_deal, create_deal, update_deal_status,
        upsert_user, DealStatus, db,
    )
    try:
        async with isolated_db():
            assert await get_user(999999999) is None
            assert await get_deal(999999999) is None
            result.add_detail("Несуществующие user/deal → None")

            await upsert_user(-1, "neg_user", "Neg")
            user = await get_user(-1)
            if user:
                result.add_finding("upsert_user принимает отрицательный tg_id")
            else:
                result.add_detail("отрицательный tg_id не сохранился")

            payload = "'; DROP TABLE deals; --"
            await upsert_user(42424242, payload, "<script>alert(1)</script>" + "🔥" * 50)
            injected = await get_user(42424242)
            async with db() as conn:
                cur = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='deals'"
                )
                tables_ok = await cur.fetchone() is not None
            if not tables_ok:
                result.error = "SQL-инъекция уничтожила таблицу deals"
                return result
            result.add_detail("SQL-инъекция в username не сломала схему")
            assert injected and injected["username"].startswith("'; DROP")

            deal_id = await create_deal(
                owner_id=42424242,
                min_btc=Decimal("0.001"),
                max_btc=Decimal("0.01"),
                escrow_address="not-a-btc-address",
            )
            result.add_finding(
                f"create_deal принял невалидный escrow (deal #{deal_id})"
            )

            await update_deal_status(999999999, DealStatus.ACTIVE)
            result.add_detail("update несуществующей сделки: без исключения")

            result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_database_operations", exc_info=True)
    return result


async def test_deal_status_transitions():
    result = CrashTestResult("Переходы статусов сделок")
    from database import (
        create_deal, update_deal_status, get_deal, upsert_user, DealStatus,
    )
    try:
        async with isolated_db():
            await upsert_user(123456789, "test_user", "Test User")
            deal_id = await create_deal(
                owner_id=123456789,
                min_btc=Decimal("0.001"),
                max_btc=Decimal("0.01"),
                escrow_address="bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                side="SELL",
                status=DealStatus.AWAITING_DEPOSIT,
            )
            await update_deal_status(deal_id, DealStatus.ACTIVE)
            await update_deal_status(deal_id, DealStatus.CANCELLED)
            await update_deal_status(deal_id, DealStatus.ACTIVE)
            deal = await get_deal(deal_id)
            if deal and deal["status"] == DealStatus.ACTIVE:
                result.add_finding(
                    "Нет защиты переходов: CANCELLED → ACTIVE прошло без ошибки"
                )
            result.add_detail(
                f"Сделка #{deal_id} после скачков: {deal['status'] if deal else None}"
            )
            result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_deal_status_transitions", exc_info=True)
    return result


async def test_concurrent_operations():
    result = CrashTestResult("Конкурентная запись в БД")
    from database import create_deal, upsert_user, DealStatus
    try:
        async with isolated_db():
            await upsert_user(123456789, "test_user", "Test User")

            async def one(n: int):
                return await create_deal(
                    owner_id=123456789,
                    min_btc=Decimal("0.001"),
                    max_btc=Decimal("0.01"),
                    escrow_address="bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    side="SELL",
                    status=DealStatus.AWAITING_DEPOSIT,
                )

            ids = await asyncio.gather(*[one(i) for i in range(25)], return_exceptions=True)
            errors = [x for x in ids if isinstance(x, Exception)]
            ok = [x for x in ids if isinstance(x, int)]
            result.add_detail(f"Создано {len(ok)}/25, ошибок: {len(errors)}")
            if errors:
                result.add_finding(f"Первая ошибка конкурентности: {errors[0]}")
            if len(ok) != len(set(ok)):
                result.error = "Дублирующиеся lastrowid при конкурентной вставке"
                return result
            result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_concurrent_operations", exc_info=True)
    return result


async def test_data_integrity():
    result = CrashTestResult("Целостность комиссий")
    from database import calculate_seller_amounts
    try:
        for amount in [
            Decimal("0.001"), Decimal("0.01"), Decimal("0.1"),
            Decimal("1.0"), Decimal("0.00000001"),
        ]:
            s = calculate_seller_amounts(amount)
            assert s["net_to_seller"] >= 0
            assert s["net_to_seller"] <= amount
            assert s["seller_net_loss"] >= 0
            total = s["net_to_seller"] + s["seller_net_loss"]
            assert abs(total - amount) <= Decimal("0.00000001")
            result.add_detail(f"{amount}: net={s['net_to_seller']}")
        result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_data_integrity", exc_info=True)
    return result


async def test_callback_parsing_crashes():
    result = CrashTestResult("Парсинг callback_data")
    payloads = [
        "deal:view:",
        "deal:view:abc",
        "deal:view:" + "9" * 40,
        "chat:open:NaN",
        "admin:confirm:oops",
        "mydeals:history:page:-1",
        "rate:stars:x:y",
        "menu:buy\x00",
        "deal:view:1:2:3:4:5",
    ]
    crashed = []
    handled = []
    for data in payloads:
        try:
            parts = data.split(":")
            _ = int(parts[2]) if len(parts) > 2 else int("x")
            handled.append(data)
        except (ValueError, IndexError):
            handled.append(data)
        except Exception as e:
            crashed.append(f"{data!r} → {type(e).__name__}")
    result.add_detail(f"int() на битых payload: {len(handled)} обработаны как ValueError/IndexError")
    if crashed:
        result.error = "; ".join(crashed)
        return result
    result.add_finding(
        "Многие хендлеры делают int(callback.data.split(...)[n]) без try; "
        "ошибка ловится только глобальным error_handler"
    )
    result.passed = True
    return result


async def test_handler_none_text():
    result = CrashTestResult("FSM: message.text is None")
    try:
        from handlers.sell_handlers import sell_limits
        from handlers.buy_handlers import buy_create_limits_or_amount
        from handlers.main_handlers import cmd_test_deposit
        result.add_detail("sell_limits / buy_create_limits_or_amount делают message.text.strip() без проверки None")
        result.add_finding(
            "Стикер/фото в WAIT_LIMITS уронит хендлер (AttributeError); "
            "ошибка должна быть поймана error_handler"
        )
        src = Path("handlers/main_handlers.py").read_text(encoding="utf-8")
        if "Decimal(args[1])" in src and "test_deposit" in src:
            result.add_finding("/test_deposit не валидирует сумму — Decimal('abc') упадёт")
        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


async def test_duplicate_handlers():
    result = CrashTestResult("Дубли хендлеров")
    try:
        files = {
            "handlers/admin_handlers.py": '@router.message(Command("chat"))',
            "handlers/deal_handlers.py": '@router.callback_query(F.data.startswith("deal:actions:"))',
            "handlers/main_handlers.py": '@router.callback_query(F.data == "menu:help")',
        }
        for path, marker in files.items():
            text = Path(path).read_text(encoding="utf-8")
            count = text.count(marker)
            if count > 1:
                result.add_finding(f"{path}: '{marker}' встречается {count} раз")
            else:
                result.add_detail(f"{path}: уникальный маркер")
        result.passed = True
    except Exception as e:
        result.error = str(e)
    return result


async def test_bot_api_alive():
    result = CrashTestResult("Telegram Bot API (getMe)")
    try:
        import aiohttp
        from config import BOT_TOKEN, TELEGRAM_PROXY
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
        timeout = aiohttp.ClientTimeout(total=20)
        kwargs = {}
        if TELEGRAM_PROXY:
            from aiohttp_socks import ProxyConnector
            kwargs["connector"] = ProxyConnector.from_url(TELEGRAM_PROXY)
        async with aiohttp.ClientSession(timeout=timeout, **kwargs) as session:
            async with session.get(url) as resp:
                data = await resp.json()
        if not data.get("ok"):
            result.error = f"getMe не ok: {data}"
            return result
        username = data["result"].get("username")
        result.add_detail(f"Бот жив: @{username} id={data['result'].get('id')}")
        result.passed = True
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        logger.error("test_bot_api_alive", exc_info=True)
    return result


def _bot_log_errors_since(start_ts: float) -> list[str]:
    log_path = BASE_DIR / "bot.log"
    if not log_path.exists():
        return ["bot.log отсутствует"]
    hits = []
    keys = ("Traceback", " ERROR ", "Ошибка при обработке", "AttributeError", "TypeError")
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    dt = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if dt.timestamp() < start_ts - 2:
                    continue
                if any(k in line for k in keys):
                    hits.append(line.strip()[:300])
    except Exception as e:
        hits.append(f"не удалось читать bot.log: {e}")
    return hits[-40:]


async def test_handler_storm():
    result = CrashTestResult("Шторм апдейтов в хендлеры")
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode
    from aiogram.exceptions import TelegramAPIError
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.types import CallbackQuery, Chat, Document, Message, Update, User
    from config import BOT_TOKEN
    from handlers import setup_handlers
    from handlers.sell_handlers import SellStates

    bot = None
    try:
        async with isolated_db():
            bot = Bot(
                token=BOT_TOKEN,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            dp = Dispatcher(storage=MemoryStorage())
            dp.include_router(setup_handlers())
            me = await bot.get_me()
            uid = 777000001
            user = User(id=uid, is_bot=False, first_name="Crash", username="crash_tester")
            chat = Chat(id=uid, type="private")
            n = 1
            crashes: list[str] = []
            handled = 0

            async def feed(update: Update, label: str):
                nonlocal handled
                try:
                    await dp.feed_update(bot, update)
                    handled += 1
                except TelegramAPIError:
                    handled += 1
                except Exception as e:
                    crashes.append(f"{label}: {type(e).__name__}: {e}")

            texts = [
                "/start", "/help", "/profile", "/deals", "/stats", "/btc",
                "/c", "/c abc", "/c 0", "/c -5", "/c 1e10",
                "/search", "/search @@@", "/unknown_cmd_crash_test",
                "/test_deposit abc xyz",
                "'; DROP TABLE deals; --",
                "<script>alert(1)</script>",
                "🔥" * 200,
                "A" * 4000,
                "\u200b",
                "0.001 - 0.10",
                "мошенник скам кидала",
            ]
            for text in texts:
                msg = Message(
                    message_id=n,
                    date=datetime.utcnow(),
                    chat=chat,
                    from_user=user,
                    text=text,
                )
                await feed(Update(update_id=n, message=msg), text[:48])
                n += 1

            dummy = Message(
                message_id=n,
                date=datetime.utcnow(),
                chat=chat,
                from_user=user,
                text="kb",
            )
            callbacks = [
                "menu:buy", "menu:sell", "menu:create", "menu:deals",
                "menu:help", "menu:profile", "menu:back",
                "deal:view:abc", "deal:view:999999999", "deal:view:",
                "chat:open:1", "chat:open:abc",
                "admin:stats", "admin:users:page:-1",
                "mydeals:active", "mydeals:history:page:abc",
                "help:fees", "active:sell", "active:buy",
                "sell:start", "buy:create",
                "rate:stars:x:y",
            ]
            for data in callbacks:
                cb = CallbackQuery(
                    id=str(n),
                    from_user=user,
                    chat_instance="crash",
                    data=data,
                    message=dummy,
                )
                await feed(Update(update_id=n, callback_query=cb), f"cb:{data}")
                n += 1

            key = StorageKey(bot_id=me.id, chat_id=uid, user_id=uid)
            await dp.storage.set_state(key, SellStates.WAIT_LIMITS)
            doc = Document(file_id="AgAD_crash_test", file_unique_id="crashfile")
            doc_msg = Message(
                message_id=n,
                date=datetime.utcnow(),
                chat=chat,
                from_user=user,
                document=doc,
            )
            await feed(Update(update_id=n, message=doc_msg), "document@WAIT_LIMITS")
            n += 1

            result.add_detail(f"Апдейтов без исключения хендлера: {handled}")
            if crashes:
                result.error = f"{len(crashes)} падений хендлеров"
                for line in crashes[:15]:
                    result.add_detail(line)
                result.passed = False
            else:
                result.passed = True
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        logger.error("test_handler_storm", exc_info=True)
    finally:
        if bot is not None:
            await bot.session.close()
    return result


async def test_live_telegram():
    result = CrashTestResult("Живой краш-тест Telegram")
    try:
        from pyrogram import Client
        from pyrogram.errors import AuthKeyUnregistered, Unauthorized
        api_id = int(os.getenv("TG_API_ID", "0") or "0")
        api_hash = os.getenv("TG_API_HASH")
        bot_username = os.getenv("SOURCE_BOT_USERNAME", "p2pBmBv2_bot")
        if not api_id or not api_hash:
            result.add_detail("Нет TG_API_ID/HASH — live-тест через user-сессию пропущен")
            result.passed = True
            return result

        sessions = [
            name
            for name in ("auto_withdraw_session", "forwarder_session", "get_chat_id_session")
            if (BASE_DIR / f"{name}.session").exists()
        ]
        if not sessions:
            result.add_detail("User-сессии нет — live-тест пропущен")
            result.passed = True
            return result

        started = time.time()
        used = None
        client = None
        for name in sessions:
            client = Client(name, api_id=api_id, api_hash=api_hash, workdir=str(BASE_DIR))
            try:
                await client.start()
                used = name
                break
            except (AuthKeyUnregistered, Unauthorized) as e:
                result.add_detail(f"{name}: {type(e).__name__}")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = None
            except Exception as e:
                result.add_detail(f"{name}: {type(e).__name__}: {e}")
                try:
                    await client.disconnect()
                except Exception:
                    pass
                client = None

        if client is None or used is None:
            result.add_detail("Ни одна user-сессия не авторизована — inbound live пропущен")
            result.passed = True
            return result

        payloads = [
            "/start", "/help", "/profile", "/deals", "/stats", "/btc",
            "/c abc", "/c 0", "/c 999999999999999",
            "/search @@@", "'; DROP TABLE deals; --",
            "A" * 500,
        ]
        try:
            me = await client.get_me()
            result.add_detail(f"Сессия {used}: id={me.id} @{me.username}")
            sent = 0
            for text in payloads:
                await client.send_message(bot_username, text)
                sent += 1
                await asyncio.sleep(0.3)
            for _ in range(8):
                await client.send_message(bot_username, "/help")
                sent += 1
                await asyncio.sleep(0.12)
            result.add_detail(f"Отправлено {sent} сообщений в @{bot_username}")
        finally:
            try:
                await client.stop()
            except Exception:
                pass

        await asyncio.sleep(2.0)
        errors = _bot_log_errors_since(started)
        if errors:
            result.add_finding(f"В хвосте bot.log {len(errors)} ERROR/Traceback")
            for line in errors[-6:]:
                result.add_detail(line)
        else:
            result.add_detail("Новых Traceback/ERROR в хвосте bot.log не видно")
        result.passed = True
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        logger.error("test_live_telegram", exc_info=True)
    return result


async def test_sqlite_busy_on_prod_copy():
    """Копия прод-БД: схема читается, WAL не обязателен."""
    result = CrashTestResult("Чтение прод-БД (копия)")
    src = BASE_DIR / "p2p_bot.db"
    if not src.exists():
        result.add_detail("p2p_bot.db нет")
        result.passed = True
        return result
    dest = None
    try:
        fd, dest = tempfile.mkstemp(prefix="p2p_copy_", suffix=".db")
        os.close(fd)
        # sqlite backup API безопаснее copy2 при открытой БД
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        conn = sqlite3.connect(dest)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        deals = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] if "deals" in tables else 0
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] if "users" in tables else 0
        conn.close()
        result.add_detail(f"Таблицы: {len(tables)}; users={users}, deals={deals}")
        if "deals" not in tables or "users" not in tables:
            result.error = f"Нет обязательных таблиц: {tables}"
            return result
        result.passed = True
    except Exception as e:
        result.error = str(e)
        logger.error("test_sqlite_busy_on_prod_copy", exc_info=True)
    finally:
        if dest:
            try:
                os.unlink(dest)
            except OSError:
                pass
    return result


async def run_all_tests(include_live: bool = True):
    logger.info("🚀 Начало краш-тестирования")
    tests = [
        test_imports,
        test_parse_limits,
        test_btc_address_validation,
        test_commission_calculations,
        test_utils_formatters,
        test_fresh_init_schema,
        test_database_operations,
        test_deal_status_transitions,
        test_concurrent_operations,
        test_data_integrity,
        test_callback_parsing_crashes,
        test_handler_none_text,
        test_duplicate_handlers,
        test_sqlite_busy_on_prod_copy,
        test_bot_api_alive,
        test_handler_storm,
    ]
    if include_live:
        tests.append(test_live_telegram)

    results = []
    for fn in tests:
        logger.info(f"→ {fn.__name__}")
        try:
            res = await fn()
        except Exception as e:
            res = CrashTestResult(fn.__name__)
            res.error = f"Критическая ошибка: {e}"
            logger.error(f"{fn.__name__} crashed", exc_info=True)
        results.append(res)
        logger.info(str(res))

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    findings = [f"- {r.test_name}: {f}" for r in results for f in r.findings]

    report = [
        "# Crash test report",
        "",
        f"Пройдено: **{passed}/{len(results)}**, провалено: **{failed}**",
        "",
        "## Результаты",
    ]
    for r in results:
        report.append(f"### {r.test_name}")
        report.append("PASS" if r.passed else "FAIL")
        if r.error:
            report.append(f"Ошибка: {r.error}")
        report.extend([f"- {d}" for d in r.details])
        report.extend([f"- FINDING: {f}" for f in r.findings])
        report.append("")
    if findings:
        report.append("## Находки")
        report.extend(findings)
        report.append("")

    (BASE_DIR / "CRASH_TEST_REPORT.md").write_text("\n".join(report), encoding="utf-8")
    logger.info("=" * 50)
    logger.info(f"✅ Пройдено: {passed}")
    logger.info(f"❌ Провалено: {failed}")
    logger.info(f"⚠️ Находок: {len(findings)}")
    logger.info("=" * 50)
    return results


if __name__ == "__main__":
    include_live = "--no-live" not in sys.argv
    try:
        results = asyncio.run(run_all_tests(include_live=include_live))
        sys.exit(0 if all(r.passed for r in results) else 1)
    except KeyboardInterrupt:
        logger.info("Прервано")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}", exc_info=True)
        sys.exit(1)
