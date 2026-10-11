"""
Главный файл запуска P2P бота
"""
import asyncio
import logging
import sys
import io
from datetime import datetime, timedelta

# Исправление кодировки для Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonCommands

from config import BOT_TOKEN, DEPOSIT_BOT_TOKEN, TELEGRAM_PROXY
from utils import create_bot
from database import init_db, expire_old_deals, update_daily_stats, cleanup_old_cooldowns, db, ADMIN_IDS, ADMIN_CHAT_ID, DealStatus, get_user, find_user_by_username, confirm_latest_awaiting_deposit_for_user
from handlers import setup_handlers
from decimal import Decimal
import json
from pathlib import Path
BASE_DIR = Path(__file__).parent.absolute()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(BASE_DIR / 'bot.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


async def expirer_loop():
    """
    Фоновая задача проверки истечения заявок + уведомление за 30 минут + возврат депозита при истечении
    """
    bot = create_bot(BOT_TOKEN)
    
    while True:
        try:
            now = datetime.utcnow()
            now_iso = now.isoformat()
            warning_time = (now + timedelta(minutes=30)).isoformat()
            
            # Получаем список истекших сделок, требующих возврата депозита
            expired_deals = await expire_old_deals()
            
            # Обрабатываем возврат депозита для истекших сделок на продажу
            if expired_deals:
                logger.info(f"⏰ [ИСТЕЧЕНИЕ] Найдено {len(expired_deals)} истекших сделок, требующих возврата депозита")
                
                for expired_deal in expired_deals:
                    deal_id = expired_deal.get("id")
                    owner_id = expired_deal.get("owner_id")
                    max_btc = Decimal(str(expired_deal.get("max_btc") or "0"))
                    
                    logger.info(f"⏰ [ИСТЕЧЕНИЕ] Обработка истекшей сделки #{deal_id}, продавец: {owner_id}, депозит: {max_btc} BTC")
                    
                    # Получаем кошелек продавца
                    user = await get_user(owner_id)
                    user_wallet = user.get("btc_wallet") if user else None
                    
                    if user_wallet and max_btc > 0:
                        try:
                            # Форматируем сумму для команды (полный возврат без штрафа)
                            amount_str = f"{max_btc:.8f}".rstrip('0').rstrip('.')
                            
                            logger.info(f"💰 [ИСТЕЧЕНИЕ] Создаю задачу на возврат депозита для сделки #{deal_id}")
                            logger.info(f"💰 [ИСТЕЧЕНИЕ] Кошелек: {user_wallet}, сумма: {amount_str} BTC (полный возврат)")
                            
                            from withdraw_queue import enqueue_payout
                            queued = await enqueue_payout(
                                deal_id=deal_id,
                                user_id=owner_id,
                                wallet=user_wallet,
                                amount=amount_str,
                                payout_type="refund",
                            )
                            if queued.get("created"):
                                logger.info(f"✅ [ИСТЕЧЕНИЕ] Задача возврата депозита для сделки #{deal_id} добавлена в очередь")
                                from utils import get_btc_rub_rate, btc_to_rub
                                btc_rub_rate = await get_btc_rub_rate()
                                deposit_rub = btc_to_rub(max_btc, btc_rub_rate)
                                deposit_formatted = f"{max_btc:.8f}".rstrip('0').rstrip('.')
                                deposit_rub_formatted = f"{deposit_rub:,.0f}".replace(",", " ")
                                
                                notification = (
                                    "⏰ <b>Время сделки истекло</b>\n\n"
                                    f"Сделка #{deal_id} автоматически отменена по истечении времени (5 часов).\n\n"
                                    f"💰 <b>Возврат депозита:</b>\n"
                                    f"Депозит: {deposit_formatted} BTC (~{deposit_rub_formatted} руб)\n\n"
                                    f"💸 <b>К возврату:</b> {deposit_formatted} BTC (~{deposit_rub_formatted} руб)\n\n"
                                    f"✅ Депозит будет возвращен автоматически в полном объеме (без штрафа)."
                                )
                                
                                try:
                                    await bot.send_message(owner_id, notification, parse_mode="HTML")
                                    logger.info(f"✅ [ИСТЕЧЕНИЕ] Уведомление отправлено продавцу {owner_id} о возврате депозита")
                                except Exception as e:
                                    logger.error(f"❌ [ИСТЕЧЕНИЕ] Ошибка отправки уведомления продавцу {owner_id}: {e}")
                            else:
                                logger.info(f"💰 [ИСТЕЧЕНИЕ] Задача не добавлена (дубликат)")
                                
                        except Exception as e:
                            logger.error(f"❌ [ИСТЕЧЕНИЕ] Ошибка при обработке возврата депозита для сделки #{deal_id}: {e}", exc_info=True)
                    else:
                        if not user_wallet:
                            logger.warning(f"⚠️ [ИСТЕЧЕНИЕ] BTC кошелек не указан в профиле пользователя {owner_id} для сделки #{deal_id}")
                        if max_btc <= 0:
                            logger.warning(f"⚠️ [ИСТЕЧЕНИЕ] Депозит равен нулю для сделки #{deal_id}")
            
            # Проверяем сделки, которые скоро истекут (за 30 минут)
            async with db() as conn:
                cur = await conn.execute(
                    "SELECT * FROM deals WHERE status IN (?, ?, ?) "
                    "AND expires_at <= ? AND expires_at > ?",
                    (DealStatus.ACTIVE, DealStatus.MATCHED, DealStatus.PAYMENT_PENDING,
                     warning_time, now_iso)
                )
                expiring_deals = await cur.fetchall()
                
                for deal in expiring_deals:
                    d = dict(deal)
                    expires_at = datetime.fromisoformat(d["expires_at"])
                    minutes_left = int((expires_at - now).total_seconds() / 60)
                    
                    if 29 <= minutes_left <= 31:
                        notification = (
                            "⏰ <b>Внимание! Заявка скоро истечёт</b>\n\n"
                            f"Сделка #{d['id']}\n"
                            f"Осталось: <b>{minutes_left} минут</b>\n\n"
                            "Завершите сделку или она будет автоматически отменена."
                        )
                        
                        try:
                            await bot.send_message(d["owner_id"], notification, parse_mode="HTML")
                            
                            if d.get("partner_id"):
                                await bot.send_message(d["partner_id"], notification, parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления об истечении: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка в expirer_loop: {e}", exc_info=True)
        
        await asyncio.sleep(60)


async def stats_updater_loop():
    """
    Обновляет дневную статистику каждый час
    """
    while True:
        try:
            await update_daily_stats()
            logger.info(f"Статистика обновлена: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            logger.error(f"Ошибка обновления статистики: {e}")
        
        await asyncio.sleep(3600)


async def cleanup_loop():
    """
    Очистка старых данных (cooldowns и т.п.)
    """
    while True:
        try:
            cleanup_old_cooldowns()
            logger.info("Выполнена очистка старых данных")
        except Exception as e:
            logger.error(f"Ошибка очистки: {e}")
        
        await asyncio.sleep(3600)


async def start_deposit_listener():
    """Второй бот слушает входящие сообщения от @bmbsilaKR_bot и подтверждает депозиты автоматически."""
    if not DEPOSIT_BOT_TOKEN:
        logger.info("⚠️ DEPOSIT_BOT_TOKEN не указан, авто-подтверждение депозитов отключено")
        return
    
    from decimal import Decimal
    import re
    
    deposit_bot = create_bot(DEPOSIT_BOT_TOKEN)
    deposit_dp = Dispatcher()

    @deposit_dp.message()
    async def on_deposit_message(message):
        """Обработчик уведомлений о депозитах от бота @bmbsilaKR_bot
        
        Формат уведомления:
        "Сумма 0.00007839 BTC (650 RUB)
        
        Была зачислена на ваш счет.
        
        🤵🏼От: @K0_T_1K"
        """
        try:
            # Проверяем, что сообщение от бота @bmbsilaKR_bot (если указан username)
            # Или обрабатываем все сообщения, если бот получает уведомления в личку
            text = (message.text or "").strip()
            
            # Логируем информацию о сообщении
            sender_info = f"от {message.from_user.username or message.from_user.id}"
            if message.chat.type == "private":
                logger.info(f"📨 Получено личное сообщение {sender_info}: {text[:200]}")
            else:
                logger.info(f"📨 Получено сообщение в чате {message.chat.id} {sender_info}: {text[:200]}")
            
            # Ищем сумму в формате "Сумма X BTC" или "Сумма X.XXXXX BTC"
            amount_match = re.search(r'Сумма\s+([\d.]+)\s+BTC', text, re.IGNORECASE)
            if not amount_match:
                logger.debug(f"ℹ️ Сообщение не содержит 'Сумма X BTC': {text[:100]}")
                return
            
            amount_btc = Decimal(amount_match.group(1))
            logger.info(f"💰 Найдена сумма депозита: {amount_btc} BTC")
            
            # Ищем username в формате "🤵🏼От: @username" или "От: @username"
            username_match = re.search(r'[🤵🏼От:\s]*@([A-Za-z0-9_]+)', text)
            if not username_match:
                logger.debug(f"ℹ️ Сообщение не содержит '@username': {text[:100]}")
                return
            
            username = username_match.group(1)
            logger.info(f"🔍 Найден username в уведомлении о депозите: @{username}")
            
            # Находим пользователя по username
            user = await find_user_by_username(username)
            if not user:
                logger.warning(f"⚠️ Пользователь с username @{username} не найден в базе")
                return
            
            logger.info(f"👤 Найден пользователь: {user['tg_id']} (@{username})")
            
            # Находим активную сделку пользователя со статусом AWAITING_DEPOSIT или CONFIRMING
            from database import confirm_deposit, get_user_active_deals, DealStatus, get_deal
            deals = await get_user_active_deals(user["tg_id"])
            deal_id = None
            matching_deal = None
            
            for deal in deals:
                if deal["status"] in [DealStatus.CONFIRMING, DealStatus.AWAITING_DEPOSIT]:
                    # Проверяем, совпадает ли сумма
                    # Для заявки на продажу депозит = max_btc
                    deal_amount = Decimal(str(deal.get("max_btc") or "0"))
                    
                    # Сравниваем суммы с небольшой погрешностью (0.00000001 BTC)
                    # Это нужно, так как могут быть небольшие расхождения из-за округления
                    if abs(deal_amount - amount_btc) < Decimal("0.00000001"):
                        deal_id = deal["id"]
                        matching_deal = deal
                        logger.info(f"✅ Найдена сделка #{deal_id} с совпадающей суммой: {deal_amount} BTC (получено: {amount_btc} BTC)")
                        break
            
            if not deal_id:
                logger.warning(
                    f"⚠️ Не найдена сделка с совпадающей суммой {amount_btc} BTC "
                    f"для пользователя @{username}. "
                    f"Доступные сделки: {[(d['id'], d['status'], Decimal(str(d.get('max_btc', 0)))) for d in deals if d['status'] in [DealStatus.CONFIRMING, DealStatus.AWAITING_DEPOSIT]]}"
                )
                return

            from database import claim_deposit_message
            if not await claim_deposit_message(message.chat.id, message.message_id):
                logger.info(f"ℹ️ Сообщение {message.message_id} уже обработано, пропускаю")
                return
            
            # Подтверждаем депозит (без отправки уведомления - оно будет отправлено после нажатия "Депозит внесён")
            await confirm_deposit(deal_id)
            logger.info(f"✅ Депозит подтверждён автоматически для сделки #{deal_id} (уведомление будет отправлено после нажатия 'Депозит внесён')")
                
        except Exception as e:
            logger.error(f"❌ Ошибка авто-подтверждения депозита: {e}", exc_info=True)

    logger.info("🤖 Запуск бота для авто-подтверждения депозитов...")
    await deposit_dp.start_polling(
        deposit_bot,
        allowed_updates=["message"],
        drop_pending_updates=True
    )

async def main():
    """Главная функция запуска бота"""
    
    logger.info("Запуск бота...")
    if TELEGRAM_PROXY:
        logger.info(f"Используется прокси для Telegram API: {TELEGRAM_PROXY}")
    
    await init_db()
    logger.info("База данных инициализирована")
    
    bot = create_bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )
    
    # Устанавливаем команды бота для меню
    commands = [
        BotCommand(command="start", description="👋 Начать работу"),
        BotCommand(command="buy", description="📈 Купить BTC"),
        BotCommand(command="sell", description="📉 Продать BTC"),
        BotCommand(command="create", description="⚡️ Создать объявление"),
        BotCommand(command="deals", description="🗳 Мои сделки"),
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="profile", description="👤 Профиль"),
        BotCommand(command="btc", description="💰 Курс BTC"),
        BotCommand(command="c", description="💼 Калькулятор BTC"),
        BotCommand(command="stats", description="📊 Статистика"),
    ]
    await bot.set_my_commands(commands)
    
    # Устанавливаем тип меню - команды
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    
    # Добавляем обработчик ошибок
    @dp.errors()
    async def error_handler(event):
        """Обработчик ошибок для aiogram 3.x"""
        exception = getattr(event, "exception", None)
        logger.error(f"Ошибка при обработке обновления: {exception}", exc_info=True)
        
        # Получаем update из event
        update = event.update if hasattr(event, 'update') else event
        
        # Пытаемся отправить сообщение об ошибке пользователю
        try:
            # Для aiogram 3.x update может быть Update объектом
            if update:
                # Проверяем callback_query
                if hasattr(update, 'callback_query') and update.callback_query:
                    try:
                        await update.callback_query.answer("❌ Произошла ошибка. Попробуйте снова.", show_alert=True)
                    except Exception:
                        pass
                # Проверяем message
                elif hasattr(update, 'message') and update.message:
                    try:
                        await update.message.answer("❌ Произошла ошибка. Попробуйте снова.")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение об ошибке: {e}")
        
        return True
    
    main_router = setup_handlers()
    dp.include_router(main_router)
    
    asyncio.create_task(expirer_loop())
    logger.info("Запущена задача проверки истечения заявок")
    
    asyncio.create_task(stats_updater_loop())
    logger.info("Запущена задача обновления статистики")
    
    asyncio.create_task(cleanup_loop())
    logger.info("Запущена задача очистки данных")

    # Если указан токен второго бота, запускаем слушатель депозитов
    if DEPOSIT_BOT_TOKEN:
        asyncio.create_task(start_deposit_listener())
        logger.info("Запущен слушатель уведомлений о депозитах")
    
    logger.info("Бот успешно запущен!")
    
    try:
        logger.info("Начинаем polling...")
        # Явно указываем типы обновлений, чтобы гарантировать обработку callback_query
        allowed_updates = dp.resolve_used_update_types()
        if "callback_query" not in allowed_updates:
            allowed_updates.append("callback_query")
        logger.info(f"Разрешенные типы обновлений: {allowed_updates}")
        await dp.start_polling(
            bot,
            allowed_updates=allowed_updates,
            drop_pending_updates=True
        )
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Программа прервана пользователем")
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}")
        sys.exit(1)