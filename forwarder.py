import os
import re
import time
import logging
import asyncio
import sys
import json
from pathlib import Path
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Получаем абсолютный путь к директории проекта
BASE_DIR = Path(__file__).parent.absolute()
# Добавляем путь к проекту для импорта database
sys.path.insert(0, str(BASE_DIR))

# Настраиваем логирование в файл и консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(BASE_DIR / 'forwarder.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Подавляем ошибки asyncio о необработанных исключениях для неизвестных чатов
logging.getLogger("asyncio").setLevel(logging.WARNING)

# Загружаем .env из директории проекта (перезаписываем существующие переменные)
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path, override=True)

# Импортируем BOT_TOKEN из config
try:
    from config import BOT_TOKEN
except ImportError:
    BOT_TOKEN = os.getenv("BOT_TOKEN")

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH")
SESSION_NAME = os.getenv("TG_SESSION", "forwarder_session")
SOURCE_BOT_USERNAME = os.getenv("SOURCE_BOT_USERNAME", "bmbsilaKR_bot")
SOURCE_BOT_USERNAMES_RAW = os.getenv("SOURCE_BOT_USERNAMES", SOURCE_BOT_USERNAME)
SOURCE_BOT_USERNAMES = [
    u.replace("@", "").lower().strip()
    for u in SOURCE_BOT_USERNAMES_RAW.split(",")
    if u.strip()
]
TARGET_CHAT_ID_STR = os.getenv("DEPOSIT_NOTIFY_CHAT_ID", "0")
WITHDRAW_BOT_USERNAME = "BmbP2p_bot"
WITHDRAW_BOT_USERNAME_NORMALIZED = WITHDRAW_BOT_USERNAME.replace("@", "").lower().strip()

if not API_ID or not API_HASH or not TARGET_CHAT_ID_STR or TARGET_CHAT_ID_STR == "0":
    raise SystemExit("Please set TG_API_ID, TG_API_HASH, DEPOSIT_NOTIFY_CHAT_ID in .env")

# Логируем ключевые настройки для диагностики запуска
logger.info(f"🔧 [STARTUP] SOURCE_BOT_USERNAMES='{SOURCE_BOT_USERNAMES_RAW}'")
logger.info(f"🔧 [STARTUP] DEPOSIT_NOTIFY_CHAT_ID='{TARGET_CHAT_ID_STR}'")

# ВАЖНО: Для каналов Pyrogram может требовать строковый формат ID
# Пробуем определить, это число или username
try:
    # Пробуем преобразовать в int
    temp_id = int(TARGET_CHAT_ID_STR)
    # Если это отрицательное число (канал), оставляем как строку для Pyrogram
    # Pyrogram лучше работает со строковыми ID для каналов
    if temp_id < 0:
        TARGET_CHAT_ID = TARGET_CHAT_ID_STR  # Оставляем строкой для каналов
        TARGET_CHAT_ID_INT = temp_id  # Сохраняем int версию для сравнения
    else:
        TARGET_CHAT_ID = temp_id  # Для обычных чатов используем int
        TARGET_CHAT_ID_INT = temp_id
    TARGET_CHAT_ID_STR_FORMAT = str(TARGET_CHAT_ID)
except ValueError:
    # Это username канала
    TARGET_CHAT_ID = TARGET_CHAT_ID_STR
    TARGET_CHAT_ID_INT = None
    TARGET_CHAT_ID_STR_FORMAT = TARGET_CHAT_ID_STR

# Используем абсолютный путь для сессии Pyrogram
session_path = str(BASE_DIR / f"{SESSION_NAME}.session")

# Создаем свой клиент для forwarder (не используем shared, чтобы избежать конфликтов)
from utils import pyrogram_proxy_dict, create_bot as create_aiogram_bot

_client_kwargs = dict(api_id=API_ID, api_hash=API_HASH, workdir=str(BASE_DIR))
_proxy = pyrogram_proxy_dict()
if _proxy:
    _client_kwargs["proxy"] = _proxy
    logger.info(f"🔧 [STARTUP] TELEGRAM_PROXY={os.getenv('TELEGRAM_PROXY', 'http://127.0.0.1:10801')}")

app = Client(SESSION_NAME, **_client_kwargs)

# ВАЖНО: Регистрируем обработчики ДО запуска клиента
# Это гарантирует, что обработчики будут зарегистрированы

# Файл очереди задач на возврат депозита
WITHDRAW_QUEUE_FILE = BASE_DIR / "withdraw_queue.json"


async def _wq_save(queue):
    from withdraw_queue import save_withdraw_queue
    await save_withdraw_queue(queue)


async def _wq_load():
    from withdraw_queue import load_withdraw_queue
    return await load_withdraw_queue()


async def _wq_remove(deal_id, payout_type=None):
    from withdraw_queue import mark_payout_done
    await mark_payout_done(deal_id, payout_type)




async def send_admin_notification(deal_id: int, user_id: int, wallet: str, amount: str, task_type: str, error_reason: str):
    """
    Отправляет уведомление администраторам через aiogram Bot о необходимости ручного вывода
    
    Args:
        deal_id: ID сделки
        user_id: ID пользователя
        wallet: BTC кошелек для вывода
        amount: Сумма в BTC
        task_type: Тип задачи (release/refund)
        error_reason: Причина ошибки
    """
    try:
        admin_chat_id = os.getenv("ADMIN_CHAT_ID")
        if not admin_chat_id:
            logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
            return
        
        admin_chat_id_clean = admin_chat_id.strip()
        admin_chat_id_int = int(admin_chat_id_clean)
        
        if not BOT_TOKEN:
            logger.error(f"❌ [АВТОВЫВОД] BOT_TOKEN не указан, не могу отправить уведомление")
            return
        
        # Создаем клавиатуру с кнопками
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Выполнен ручной вывод",
                    callback_data=f"admin:manual_withdraw_done:{deal_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Детали сделки",
                    callback_data=f"admin:deal_info:{deal_id}"
                ),
                InlineKeyboardButton(
                    text="👤 Профиль пользователя",
                    callback_data=f"admin:user_info:{user_id}"
                )
            ]
        ])
        
        error_notification = (
            f"⚠️ <b>ТРЕБУЕТСЯ РУЧНОЙ ВЫВОД</b>\n\n"
            f"❌ <b>Ошибка автовывода</b>\n\n"
            f"📋 <b>Детали:</b>\n"
            f"• Сделка: <code>#{deal_id}</code>\n"
            f"• Пользователь: <code>{user_id}</code>\n"
            f"• Тип: <code>{task_type}</code>\n\n"
            f"💰 <b>Данные для вывода:</b>\n"
            f"• Кошелек: <code>{wallet}</code>\n"
            f"• Сумма: <code>{amount} BTC</code>\n\n"
            f"🔧 <b>Причина:</b> {error_reason}\n\n"
            f"⚠️ <b>Требуется ручное вмешательство</b>"
        )
        
        bot = create_aiogram_bot(BOT_TOKEN)
        try:
            await bot.send_message(
                chat_id=admin_chat_id_int,
                text=error_notification,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            logger.info(f"✅ [АВТОВЫВОД] Уведомление о необходимости ручного вывода отправлено админам в чат {admin_chat_id_int}")
        finally:
            await bot.session.close()
            
    except ValueError as ve:
        logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}, ошибка: {ve}")
    except Exception as e:
        logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления администраторам: {e}", exc_info=True)


def extract_username(text: str) -> str | None:
    if not text:
        return None
    m = re.search(r"@([A-Za-z0-9_]+)", text)
    return m.group(1) if m else None


# Кэш для найденного чата (чтобы не искать каждый раз)
_cached_chat_id = None
_cached_chat_found = False

async def send_to_target_chat(msg, txt):
    """Отправляет сообщение в целевой чат, пробуя разные форматы ID"""
    global _cached_chat_id, _cached_chat_found
    
    chat_id_to_use = TARGET_CHAT_ID
    
    # Если чат уже найден ранее, используем кэш
    if _cached_chat_found and _cached_chat_id:
        chat_id_to_use = _cached_chat_id
        logger.info(f"🔄 Использую кэшированный chat_id: {chat_id_to_use}")
    else:
        # Пробуем найти чат через get_dialogs - это "знакомит" Pyrogram со всеми чатами
        found_chat = None
        
        try:
            # Получаем список всех диалогов - это "знакомит" Pyrogram со всеми чатами
            # Используем int версию для сравнения
            target_id_for_comparison = TARGET_CHAT_ID_INT if TARGET_CHAT_ID_INT is not None else None
            if target_id_for_comparison is None and isinstance(TARGET_CHAT_ID, str) and TARGET_CHAT_ID.lstrip('-').isdigit():
                try:
                    target_id_for_comparison = int(TARGET_CHAT_ID)
                except:
                    pass
            
            logger.info(f"🔍 Ищу чат через get_dialogs, сравниваю с ID: {target_id_for_comparison}")
            dialog_count = 0
            async for dialog in app.get_dialogs():
                dialog_count += 1
                # Сравниваем по int ID если есть
                if target_id_for_comparison is not None and isinstance(target_id_for_comparison, int):
                    if dialog.chat.id == target_id_for_comparison:
                        found_chat = dialog.chat
                        logger.info(f"✅ Чат найден через get_dialogs: {dialog.chat.title if hasattr(dialog.chat, 'title') else 'N/A'} (ID: {dialog.chat.id})")
                        chat_id_to_use = dialog.chat.id
                        _cached_chat_id = dialog.chat.id
                        _cached_chat_found = True
                        break
                # Или по username
                elif isinstance(TARGET_CHAT_ID, str) and not TARGET_CHAT_ID.startswith('-'):
                    if hasattr(dialog.chat, 'username') and dialog.chat.username:
                        if dialog.chat.username.lower() == str(TARGET_CHAT_ID).replace('@', '').lower():
                            found_chat = dialog.chat
                            logger.info(f"✅ Чат найден через get_dialogs по username: {dialog.chat.title if hasattr(dialog.chat, 'title') else 'N/A'} (ID: {dialog.chat.id})")
                            chat_id_to_use = dialog.chat.id
                            _cached_chat_id = dialog.chat.id
                            _cached_chat_found = True
                            break
            
            if not found_chat:
                logger.warning(f"⚠️ Чат не найден в {dialog_count} диалогах через get_dialogs")
        except Exception as dialogs_error:
            logger.warning(f"⚠️ get_dialogs не сработал: {dialogs_error}")
        
        # Если не нашли через get_dialogs, пробуем get_chat
        if not found_chat:
            try:
                chat_info = await app.get_chat(TARGET_CHAT_ID)
                logger.info(f"✅ Чат найден через get_chat: {chat_info.title if hasattr(chat_info, 'title') else 'N/A'} (ID: {chat_info.id})")
                chat_id_to_use = chat_info.id
                _cached_chat_id = chat_info.id
                _cached_chat_found = True
            except Exception as chat_error:
                error_msg = str(chat_error)
                logger.warning(f"⚠️ get_chat не сработал для {TARGET_CHAT_ID}: {error_msg}")
    
    logger.info(f"🔄 [SEND_TO_TARGET] Пробую отправить в чат: {chat_id_to_use} (тип: {type(chat_id_to_use)})")
    
    # Пробуем отправить - сначала пересылаем, потом как текст
    try:
        logger.info(f"🔄 [SEND_TO_TARGET] Пробую переслать сообщение (msg.id={msg.id}) в чат {chat_id_to_use}")
        await msg.forward(chat_id=chat_id_to_use)
        logger.info(f"✅ [SEND_TO_TARGET] Сообщение переслано в чат {chat_id_to_use}")
        return True
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"⚠️ [SEND_TO_TARGET] Не удалось переслать в {chat_id_to_use}: {error_msg}")
        logger.warning(f"⚠️ [SEND_TO_TARGET] Тип ошибки: {type(e).__name__}")
        
        # Если ошибка, сбрасываем кэш и пробуем снова
        if "Peer id invalid" in error_msg or "chat not found" in error_msg.lower():
            logger.warning(f"⚠️ [SEND_TO_TARGET] Сбрасываю кэш чата из-за ошибки")
            _cached_chat_id = None
            _cached_chat_found = False
        
        try:
            logger.info(f"🔄 [SEND_TO_TARGET] Пробую отправить как текст в чат {chat_id_to_use}")
            await app.send_message(chat_id_to_use, txt)
            logger.info(f"✅ [SEND_TO_TARGET] Сообщение отправлено как текст в чат {chat_id_to_use}")
            return True
        except Exception as e2:
            error_msg2 = str(e2)
            logger.error(f"❌ [SEND_TO_TARGET] Не удалось отправить текст в {chat_id_to_use}: {error_msg2}")
            logger.error(f"❌ [SEND_TO_TARGET] Тип ошибки: {type(e2).__name__}")
            return False


@app.on_message(filters.private)
async def on_any_private_message(_, msg):
    """Обрабатывает все личные сообщения и проверяет отправителя"""
    txt = msg.text or msg.caption or ""
    
    # Логируем все личные сообщения для отладки
    from_username = msg.from_user.username if msg.from_user else None
    from_id = msg.from_user.id if msg.from_user else None
    logger.info(f"🔍 [FORWARDER DEBUG] Личное сообщение от: {from_username or 'N/A'} (ID: {from_id or 'N/A'})")
    logger.info(f"🔍 [FORWARDER DEBUG] Текст: {txt[:100] if txt else 'N/A'}")
    logger.info(f"🔍 [FORWARDER] Дошли до проверки from_user")
    
    # Проверяем, что сообщение от нужного бота
    logger.info(f"🔍 [FORWARDER] Проверяю from_user: {msg.from_user is not None}")
    if not msg.from_user:
        logger.info("⚠️ [FORWARDER] Сообщение без from_user, пропускаем")
        return
    
    logger.info(f"🔍 [FORWARDER] Проверяю from_username: {from_username}")
    if not from_username:
        logger.info("⚠️ [FORWARDER] Сообщение без username, пропускаем")
        return
    
    # Нормализуем username для сравнения (убираем @ и приводим к нижнему регистру)
    logger.info(f"🔍 [FORWARDER] SOURCE_BOT_USERNAMES из .env: '{SOURCE_BOT_USERNAMES_RAW}'")
    from_username_normalized = from_username.lower().strip() if from_username else None
    
    logger.info(
        f"🔍 [FORWARDER] Сравниваю: ожидаем один из {SOURCE_BOT_USERNAMES} "
        f"(из SOURCE_BOT_USERNAMES='{SOURCE_BOT_USERNAMES_RAW}'), "
        f"получили '{from_username_normalized}' (из from_username='{from_username}')"
    )
    
    # Проверяем, это сообщение от бота для депозитов (SOURCE_BOT_USERNAME) или от бота для вывода (@BmbP2p_bot)
    is_deposit_bot = from_username_normalized in SOURCE_BOT_USERNAMES
    is_withdraw_bot = from_username_normalized == WITHDRAW_BOT_USERNAME_NORMALIZED
    
    if is_withdraw_bot:
        # Это сообщение от бота для вывода - проверяем, есть ли кнопка подтверждения
        logger.info(f"💰 [АВТОВЫВОД] Получено сообщение от бота вывода @{from_username}")
        
        # Проверяем, есть ли ошибка "Неверный кошелек!"
        if "неверный кошелек" in txt.lower() or "⚠️неверный кошелек" in txt.lower() or "⚠️ Неверный кошелек" in txt:
            logger.warning(f"⚠️ [АВТОВЫВОД] Обнаружена ошибка 'Неверный кошелек!' - отправляю на ручной вывод")
            # Отправляем уведомление админам
            try:
                from dotenv import load_dotenv
                admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                if admin_chat_id:
                    try:
                        # Извлекаем данные из текста ошибки
                        deal_id_match = re.search(r'#(\d+)', txt)
                        deal_id = int(deal_id_match.group(1)) if deal_id_match else 0
                        wallet_match = re.search(r'кошелек[:\s]+([a-zA-Z0-9]+)', txt, re.IGNORECASE)
                        wallet = wallet_match.group(1) if wallet_match else "не указан"
                        amount_match = re.search(r'(\d+\.?\d*)\s*BTC', txt, re.IGNORECASE)
                        amount = amount_match.group(1) if amount_match else "не указана"
                        user_id_match = re.search(r'ID[:\s]+(\d+)', txt)
                        user_id = int(user_id_match.group(1)) if user_id_match else 0
                        
                        await send_admin_notification(
                            deal_id=deal_id,
                            user_id=user_id,
                            wallet=wallet,
                            amount=amount,
                            task_type="release",
                            error_reason="Неверный кошелек!"
                        )
                    except ValueError:
                        logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}")
                else:
                    logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
            except Exception as e:
                logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления админам: {e}", exc_info=True)
            # Не пересылаем это сообщение в обычный чат, так как это ошибка
            return
        
        # Проверяем, есть ли подтверждение "✅ Ваша заявка на вывод одобрена" в личном чате
        if "ваша заявка на вывод одобрена" in txt.lower():
            logger.info(f"✅ [АВТОВЫВОД] Получено подтверждение вывода в личном чате от @{from_username}: {txt[:100]}")
            # Ищем активную задачу на вывод для этого сообщения
            try:
                queue = await _wq_load()
                if queue:
                    
                    # Ищем задачу типа "release" (вывод покупателю) или "refund" (возврат депозита) - берем самую последнюю
                    release_tasks = [task for task in queue if task.get("type") == "release"]
                    refund_tasks = [task for task in queue if task.get("type") == "refund"]
                    
                    # Обрабатываем задачи типа "release"
                    if release_tasks:
                        # Берем последнюю задачу (самую свежую)
                        task = release_tasks[-1]
                        deal_id = task.get("deal_id")
                        if deal_id:
                            logger.info(f"🎉 [АВТОВЫВОД] Найдена задача release для сделки #{deal_id}, завершаю сделку...")
                            try:
                                from database import complete_deal_after_withdrawal_confirmation
                                logger.info(f"🔄 [АВТОВЫВОД] Вызываю complete_deal_after_withdrawal_confirmation для сделки #{deal_id}")
                                success = await complete_deal_after_withdrawal_confirmation(deal_id, bot=None)
                                if success:
                                    logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена после подтверждения вывода")
                                    # Удаляем задачу из очереди ТОЛЬКО после успешного завершения сделки
                                    try:
                                        await _wq_remove(task.get('deal_id'), task.get('type'))
                                        logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди после успешного завершения")
                                    except Exception as e:
                                        logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                                else:
                                    logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось завершить сделку #{deal_id} (функция вернула False) - задача остается в очереди")
                            except ImportError as e:
                                logger.error(f"❌ [АВТОВЫВОД] Ошибка импорта complete_deal_after_withdrawal_confirmation: {e}", exc_info=True)
                            except Exception as e:
                                logger.error(f"❌ [АВТОВЫВОД] Ошибка при завершении сделки #{deal_id}: {e}", exc_info=True)
                    
                    # Обрабатываем задачи типа "refund"
                    if refund_tasks:
                        # Берем последнюю задачу (самую свежую)
                        task = refund_tasks[-1]
                        deal_id = task.get("deal_id")
                        if deal_id:
                            logger.info(f"✅ [АВТОВЫВОД] Найдена задача refund для сделки #{deal_id}, удаляю из очереди...")
                            try:
                                # Удаляем задачу из очереди
                                await _wq_remove(task.get('deal_id'), task.get('type'))
                                logger.info(f"✅ [АВТОВЫВОД] Задача возврата депозита для сделки #{deal_id} удалена из очереди после подтверждения в личном чате")
                            except Exception as e:
                                logger.error(f"❌ [АВТОВЫВОД] Ошибка при удалении задачи возврата из очереди: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"❌ [АВТОВЫВОД] Ошибка при обработке подтверждения в личном чате: {e}", exc_info=True)
        
        # Проверяем, есть ли кнопка подтверждения
        if msg.reply_markup and msg.reply_markup.inline_keyboard:
            # Ищем кнопку подтверждения
            for row in msg.reply_markup.inline_keyboard:
                for button in row:
                    button_text = button.text.lower()
                    # Ищем кнопку с текстом "подтверждаю", "подтвердить", "✅" и т.д.
                    if any(keyword in button_text for keyword in ["подтверждаю", "подтвердить", "confirm", "✅", "да", "yes", "ok"]):
                        logger.info(f"🔘 [АВТОВЫВОД] Найдена кнопка подтверждения: {button.text}, callback_data: {button.callback_data}")
                        
                        # Нажимаем кнопку автоматически
                        try:
                            if button.callback_data:
                                # Отправляем callback_query
                                from pyrogram.raw import functions, types
                                peer = await app.resolve_peer(msg.chat.id)
                                callback_data_bytes = button.callback_data.encode('utf-8') if isinstance(button.callback_data, str) else button.callback_data
                                
                                result = await app.invoke(
                                    functions.messages.GetBotCallbackAnswer(
                                        peer=peer,
                                        msg_id=msg.id,
                                        data=callback_data_bytes
                                    )
                                )
                                logger.info(f"✅ [АВТОВЫВОД] Кнопка подтверждения нажата автоматически, ответ: {result}")
                                return  # Не пересылаем это сообщение
                            else:
                                logger.warning(f"⚠️ [АВТОВЫВОД] У кнопки нет callback_data")
                        except Exception as e:
                            logger.error(f"❌ [АВТОВЫВОД] Ошибка при нажатии кнопки: {e}", exc_info=True)
        
        # Если это не кнопка подтверждения или ошибка - просто пересылаем сообщение
        logger.info(f"📨 [FORWARDER] Пересылаю сообщение от бота вывода в чат {TARGET_CHAT_ID}")
        try:
            success = await send_to_target_chat(msg, txt)
            if success:
                logger.info(f"✅ [FORWARDER] Сообщение успешно переслано в чат {TARGET_CHAT_ID}")
        except Exception as e:
            logger.error(f"❌ [FORWARDER] Ошибка при пересылке сообщения: {e}", exc_info=True)
        return
    
    # Проверяем по username (без учета регистра и @)
    # Используем ТОЛЬКО username из SOURCE_BOT_USERNAME из .env
    if is_deposit_bot:
        logger.info(f"✅ [FORWARDER] Найдено сообщение от бота (получено от: @{from_username}, ожидаем: @{SOURCE_BOT_USERNAME})")
    else:
        # Не от нужного бота
        logger.info(f"⚠️ [FORWARDER] Сообщение не от нужного бота (получено от: @{from_username or 'N/A'}, ожидаем: @{SOURCE_BOT_USERNAME}), пропускаем")
        return
    
    if not txt:
        logger.warning(f"⚠️ [FORWARDER] Сообщение без текста, пропускаем. msg.text={msg.text}, msg.caption={msg.caption}")
        return
    
    logger.info(f"📨 [FORWARDER] Получено сообщение от бота: {txt[:200]}")
    logger.info(f"📨 [FORWARDER] Пытаюсь переслать в чат {TARGET_CHAT_ID}")
    
    # Пересылаем оригинальное сообщение как есть (с оригинальным форматом)
    # Обработчик в main_handlers.py сам распарсит формат "Сумма X BTC ... 🤵🏼От: @username"
    logger.info(f"🔄 [FORWARDER] ДО try блока, вызываю send_to_target_chat для сообщения от @{from_username}")
    try:
        logger.info(f"🔄 [FORWARDER] ВНУТРИ try блока, вызываю send_to_target_chat")
        success = await send_to_target_chat(msg, txt)
        logger.info(f"🔄 [FORWARDER] send_to_target_chat вернул: {success}")
        
        if success:
            logger.info(f"✅ [FORWARDER] Сообщение успешно переслано в чат {TARGET_CHAT_ID}")
        else:
            logger.error(f"❌ [FORWARDER] Не удалось переслать сообщение в чат {TARGET_CHAT_ID}")
    except Exception as e:
        logger.error(f"❌ [FORWARDER] Ошибка при пересылке сообщения: {e}", exc_info=True)
        import traceback
        logger.error(f"❌ [FORWARDER] Traceback: {traceback.format_exc()}")


async def check_chat_access():
    """Проверка доступности целевого чата"""
    try:
        # Пробуем получить информацию о чате
        # Сначала пробуем как есть
        chat = None
        working_id = None
        chat_ids_to_try = [TARGET_CHAT_ID]
        
        # Если ID отрицательный и не начинается с -100, пробуем преобразовать
        if isinstance(TARGET_CHAT_ID, int) and TARGET_CHAT_ID < 0:
            if not str(TARGET_CHAT_ID).startswith("-100"):
                converted_id = int(f"-100{abs(TARGET_CHAT_ID)}")
                chat_ids_to_try.append(converted_id)
                logger.info(f"🔄 Будет попытка с преобразованным ID для канала: {converted_id}")
        
        # Пробуем каждый ID
        for chat_id in chat_ids_to_try:
            try:
                chat = await app.get_chat(chat_id)
                working_id = chat_id
                if chat_id != TARGET_CHAT_ID:
                    logger.warning(f"⚠️ Работает ID {chat_id} вместо {TARGET_CHAT_ID}")
                    logger.warning(f"💡 Рекомендуется обновить .env: DEPOSIT_NOTIFY_CHAT_ID={chat_id}")
                break
            except ValueError:
                continue
            except Exception as e:
                logger.debug(f"⚠️ Ошибка при проверке ID {chat_id}: {e}")
                continue
        
        if not chat:
            raise ValueError("Chat not found")
        
        logger.info(f"✅ Чат найден: {chat.title or chat.id} (тип: {chat.type})")
        
        # Пробуем отправить тестовое сообщение (оно будет удалено сразу)
        # Пробуем разные форматы ID
        test_sent = False
        chat_ids_to_try = [TARGET_CHAT_ID]
        if isinstance(TARGET_CHAT_ID, int) and TARGET_CHAT_ID < 0 and not str(TARGET_CHAT_ID).startswith("-100"):
            converted_id = int(f"-100{abs(TARGET_CHAT_ID)}")
            chat_ids_to_try.append(converted_id)
        
        for chat_id in chat_ids_to_try:
            try:
                test_msg = await app.send_message(
                    chat_id, 
                    "🔧 Тестовое сообщение от мостика (будет удалено)"
                )
                # Удаляем тестовое сообщение
                try:
                    await app.delete_messages(chat_id, test_msg.id)
                except:
                    pass
                logger.info(f"✅ Права на отправку сообщений подтверждены (ID: {chat_id})")
                test_sent = True
                break
            except Exception as send_err:
                logger.debug(f"⚠️ Не удалось отправить тестовое сообщение в {chat_id}: {send_err}")
                continue
        
        if not test_sent:
            logger.warning("⚠️ Не удалось отправить тестовое сообщение ни в один из вариантов ID")
            logger.warning("Мостик может пересылать сообщения, но не сможет отправлять новые")
        
        return True
    except ValueError as e:
        error_str = str(e)
        if "Peer id invalid" in error_str or "chat not found" in error_str.lower():
            logger.error(f"❌ Чат с ID {TARGET_CHAT_ID} не найден или недоступен")
            logger.error("")
            logger.error("🔍 ВОЗМОЖНЫЕ ПРИЧИНЫ:")
            logger.error("1. Аккаунт, от которого запущен мостик, НЕ добавлен в этот чат")
            logger.error("2. ID чата указан неправильно")
            logger.error("3. Для каналов используйте формат: -1001234567890")
            logger.error("")
            logger.error("💡 РЕШЕНИЕ:")
            logger.error("1. Запустите скрипт: py get_chat_id.py")
            logger.error("2. Найдите нужный чат в списке")
            logger.error("3. Скопируйте правильный ID и обновите .env файл")
            logger.error("4. Убедитесь, что ВАШ ЛИЧНЫЙ АККАУНТ добавлен в чат (не бот!)")
        else:
            logger.error(f"❌ Ошибка доступа к чату: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Не удалось получить доступ к чату {TARGET_CHAT_ID}: {e}")
        logger.error("")
        logger.error("🔍 ПРОВЕРЬТЕ:")
        logger.error("1. Вы добавлены в этот канал/группу?")
        logger.error("2. ID чата правильный?")
        logger.error("3. Для каналов используйте формат: -1001234567890")
        logger.error("")
        logger.error("💡 Запустите: py get_chat_id.py для получения правильного ID")
        return False


@app.on_raw_update()
async def on_raw_update(client, update, users, chats):
    # Игнорируем обновления о неизвестных чатах
    # Проверяем, есть ли обновление, связанное с нашим ботом-источником
    try:
        # Пытаемся обработать только если это обновление связано с личными сообщениями
        # Все остальные обновления игнорируем
        pass
    except Exception:
        # Игнорируем все ошибки при обработке raw_update
        pass


def asyncio_exception_handler(loop, context):
    """Глобальный обработчик исключений asyncio"""
    exception = context.get('exception')
    if exception:
        # Игнорируем ошибки о неизвестных peer ID (каналах)
        if isinstance(exception, (ValueError, KeyError)):
            error_msg = str(exception)
            if "Peer id invalid" in error_msg or "ID not found" in error_msg:
                return  # Игнорируем эту ошибку
    # Логируем другие ошибки
    logger.debug(f"Игнорируемая ошибка asyncio: {context.get('message', 'Unknown error')}")


async def init_chat_cache():
    """Инициализация кэша чата при старте"""
    global _cached_chat_id, _cached_chat_found
    
    logger.info("🔍 Инициализация кэша чата...")
    try:
        # Пробуем найти чат через get_dialogs
        target_id_for_comparison = TARGET_CHAT_ID_INT if TARGET_CHAT_ID_INT is not None else None
        if target_id_for_comparison is None and isinstance(TARGET_CHAT_ID, str) and TARGET_CHAT_ID.lstrip('-').isdigit():
            try:
                target_id_for_comparison = int(TARGET_CHAT_ID)
            except:
                pass
        
        if target_id_for_comparison:
            logger.info(f"🔍 Ищу чат через get_dialogs, ID: {target_id_for_comparison}")
            async for dialog in app.get_dialogs():
                if dialog.chat.id == target_id_for_comparison:
                    _cached_chat_id = dialog.chat.id
                    _cached_chat_found = True
                    logger.info(f"✅ Чат найден и закэширован: {dialog.chat.title if hasattr(dialog.chat, 'title') else 'N/A'} (ID: {dialog.chat.id})")
                    return True
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при инициализации кэша: {e}")
    
    # Если не нашли через get_dialogs, пробуем get_chat
    try:
        chat_info = await app.get_chat(TARGET_CHAT_ID)
        _cached_chat_id = chat_info.id
        _cached_chat_found = True
        logger.info(f"✅ Чат найден через get_chat и закэширован: {chat_info.title if hasattr(chat_info, 'title') else 'N/A'} (ID: {chat_info.id})")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Не удалось инициализировать кэш через get_chat: {e}")
        return False


async def process_withdraw_queue():
    """Обработка очереди задач на возврат депозита из файла"""
    import json
    WITHDRAW_BOT_USERNAME = "BmbP2p_bot"
    
    logger.info(f"💰 [АВТОВЫВОД] === ЗАПУСК ОБРАБОТЧИКА ОЧЕРЕДИ ===")
    logger.info(f"💰 [АВТОВЫВОД] Файл очереди: {WITHDRAW_QUEUE_FILE}")
    logger.info(f"💰 [АВТОВЫВОД] Файл существует: {WITHDRAW_QUEUE_FILE.exists()}")
    
    processed_count = 0
    last_file_check = 0
    
    while True:
        try:
            queue = await _wq_load()
            if queue:
                logger.info(f"💰 [АВТОВЫВОД] ✅ Найдено задач в очереди: {len(queue)}")
                for i, task in enumerate(queue):
                    logger.info(f"💰 [АВТОВЫВОД] Задача #{i+1}: сделка {task.get('deal_id')}, кошелек {task.get('wallet')}, сумма {task.get('amount')} BTC")
            
            # Обрабатываем задачи из очереди
            if queue:
                # ВАЖНО: НЕ удаляем задачу из очереди сразу! Берем копию для обработки
                task = queue[0]  # Берем первую задачу БЕЗ удаления
                wallet = task.get("wallet")
                amount = task.get("amount")
                deal_id = task.get("deal_id")
                user_id = task.get("user_id")
                task_type = task.get("type", "refund")  # Тип задачи: release или refund
                
                logger.info(f"💰 [АВТОВЫВОД] Тип задачи: {task_type}")
                
                # Проверяем наличие обязательных данных
                if not wallet or not amount:
                    logger.warning(f"⚠️ [АВТОВЫВОД] Отсутствуют обязательные данные в задаче: wallet={wallet}, amount={amount}")
                    logger.warning(f"⚠️ [АВТОВЫВОД] Отправляю задачу на ручной вывод (неполные данные)")
                    try:
                        admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                        if admin_chat_id:
                            try:
                                admin_chat_id_int = int(admin_chat_id)
                                await send_admin_notification(
                                    deal_id=deal_id or 0,
                                    user_id=user_id or 0,
                                    wallet=wallet or "не указан",
                                    amount=amount or "не указана",
                                    task_type=task_type,
                                    error_reason="В задаче отсутствуют обязательные данные (wallet или amount)"
                                )
                            except ValueError:
                                logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}")
                        else:
                            logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
                    except Exception as e:
                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления о ручном выводе: {e}", exc_info=True)
                    
                    # Удаляем задачу из очереди
                    try:
                        queue = await _wq_load()
                        await _wq_remove(deal_id)
                        logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (требует ручного вывода, неполные данные)")
                    except Exception as e:
                        logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                elif wallet and amount:
                    try:
                        logger.info(f"💰 [АВТОВЫВОД] === НАЧАЛО ОБРАБОТКИ ЗАДАЧИ ===")
                        logger.info(f"💰 [АВТОВЫВОД] Сделка #{deal_id}, пользователь {user_id}")
                        logger.info(f"💰 [АВТОВЫВОД] Кошелек: {wallet}, сумма: {amount} BTC")
                        
                        # Формируем команду
                        command = f"/withdraw {wallet} {amount}"
                        logger.info(f"💰 [АВТОВЫВОД] Команда: {command}")
                        
                        # Получаем chat_id бота
                        bot_username = WITHDRAW_BOT_USERNAME.replace("@", "")
                        logger.info(f"💰 [АВТОВЫВОД] Получаю chat_id бота @{bot_username}...")
                        try:
                            bot_chat = await app.get_chat(bot_username)
                            bot_chat_id = bot_chat.id
                            logger.info(f"✅ [АВТОВЫВОД] Chat ID бота: {bot_chat_id}")
                        except Exception as e:
                            logger.error(f"❌ [АВТОВЫВОД] Не удалось получить chat_id бота: {e}")
                            # Пробуем через get_dialogs
                            logger.info(f"🔍 [АВТОВЫВОД] Пробую найти бота через get_dialogs...")
                            bot_username_normalized = bot_username.lower()
                            bot_chat_id = None
                            async for dialog in app.get_dialogs():
                                if dialog.chat.username and dialog.chat.username.lower() == bot_username_normalized:
                                    bot_chat_id = dialog.chat.id
                                    logger.info(f"✅ [АВТОВЫВОД] Бот найден через get_dialogs, Chat ID: {bot_chat_id}")
                                    break
                            
                            if not bot_chat_id:
                                logger.error(f"❌ [АВТОВЫВОД] Бот @{WITHDRAW_BOT_USERNAME} не найден!")
                                raise Exception(f"Бот @{WITHDRAW_BOT_USERNAME} не найден")
                        
                        # Отправляем команду
                        logger.info(f"💰 [АВТОВЫВОД] Отправляю команду боту @{WITHDRAW_BOT_USERNAME}...")
                        sent_message = await app.send_message(bot_chat_id, command)
                        logger.info(f"✅ [АВТОВЫВОД] Команда отправлена боту @{WITHDRAW_BOT_USERNAME}, message_id: {sent_message.id}")
                        logger.info(f"✅ [АВТОВЫВОД] Ожидаю ответ от бота для автоматического нажатия кнопки подтверждения...")
                        
                        # Ждем ответ от бота (максимум 5 секунд)
                        await asyncio.sleep(2)
                        
                        # Ищем ответ от бота с кнопкой подтверждения
                        response_found = False
                        confirmation_message_id = None
                        async for msg in app.get_chat_history(bot_chat_id, limit=10):
                            if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == bot_username.lower():
                                if msg.reply_markup and msg.reply_markup.inline_keyboard:
                                    # Ищем кнопку подтверждения
                                    for row in msg.reply_markup.inline_keyboard:
                                        for button in row:
                                            button_text = button.text.lower()
                                            if any(keyword in button_text for keyword in ["подтверждаю", "подтвердить", "confirm", "✅", "да", "yes", "ok"]):
                                                logger.info(f"🔘 [АВТОВЫВОД] Найдена кнопка подтверждения: {button.text}")
                                                confirmation_message_id = msg.id
                                                
                                                # Нажимаем кнопку
                                                try:
                                                    from pyrogram.raw import functions
                                                    peer = await app.resolve_peer(bot_chat_id)
                                                    callback_data_bytes = button.callback_data.encode('utf-8') if isinstance(button.callback_data, str) else button.callback_data
                                                    
                                                    result = await app.invoke(
                                                        functions.messages.GetBotCallbackAnswer(
                                                            peer=peer,
                                                            msg_id=msg.id,
                                                            data=callback_data_bytes
                                                        )
                                                    )
                                                    logger.info(f"✅ [АВТОВЫВОД] Кнопка подтверждения нажата автоматически!")
                                                    response_found = True
                                                    break
                                                except Exception as e:
                                                    logger.error(f"❌ [АВТОВЫВОД] Ошибка при нажатии кнопки: {e}", exc_info=True)
                                                    # Ошибка при нажатии кнопки - отправляем на ручной вывод
                                                    raise  # Пробрасываем ошибку дальше, чтобы она была обработана в общем блоке except
                                    
                                    if response_found:
                                        break
                            
                            if response_found:
                                break
                        
                        if not response_found:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Кнопка подтверждения не найдена в ответе от бота, но продолжаю ожидание подтверждения в канале пересылок...")
                            # Кнопка не найдена, но это не критично - подтверждение может прийти в канале пересылок
                            # Продолжаем ожидание подтверждения в канале пересылок
                        
                        # В любом случае ждем подтверждение успешного вывода в канале пересылок
                        logger.info(f"⏳ [АВТОВЫВОД] Ожидаю подтверждение успешного вывода от @{WITHDRAW_BOT_USERNAME} в канале пересылок...")
                        
                        # Запоминаем время начала ожидания и последний message_id в канале пересылок
                        start_time = time.time()
                        wait_duration = 10  # Ждем 10 секунд
                        
                        # Получаем последний message_id в канале пересылок перед ожиданием
                        last_message_id_before = None
                        try:
                            async for msg in app.get_chat_history(TARGET_CHAT_ID, limit=1):
                                last_message_id_before = msg.id
                                break
                        except Exception as e:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось получить последний message_id канала: {e}")
                        
                        # Ждем до wait_duration секунд, проверяя канал пересылок
                        approval_found = False
                        error_detected = False  # Флаг для обнаружения ошибки "Неверный кошелек"
                        deal_completed = False  # Флаг для успешного завершения сделки
                        logger.info(f"⏳ [АВТОВЫВОД] Начинаю ожидание подтверждения для сделки #{deal_id} (максимум {wait_duration} секунд)")
                        
                        try:
                                while (time.time() - start_time) < wait_duration:
                                    await asyncio.sleep(0.5)  # Проверяем каждые 0.5 секунды
                                    
                                    # Проверяем новые сообщения в канале пересылок
                                    try:
                                        async for msg in app.get_chat_history(TARGET_CHAT_ID, limit=20):
                                            # Пропускаем сообщения, которые были до начала ожидания
                                            if last_message_id_before and msg.id <= last_message_id_before:
                                                continue
                                            
                                            # Проверяем текст сообщения (может быть переслано от @BmbP2p_bot)
                                            msg_text = (msg.text or msg.caption or "").strip()
                                            
                                            # Более подробное логирование для задач типа refund
                                            if task_type == "refund":
                                                logger.info(f"🔍 [АВТОВЫВОД] [REFUND] Проверяю сообщение {msg.id} для сделки #{deal_id}: {msg_text[:200]}...")
                                            else:
                                                logger.info(f"🔍 [АВТОВЫВОД] Проверяю сообщение {msg.id} для сделки #{deal_id}: {msg_text[:100]}...")
                                            
                                            # Проверяем на ошибку "⚠️Неверный кошелек!" - если есть, прерываем ожидание
                                            if "неверный кошелек" in msg_text.lower() or "⚠️неверный кошелек" in msg_text.lower() or "⚠️ Неверный кошелек" in msg_text:
                                                logger.warning(f"⚠️ [АВТОВЫВОД] Обнаружена ошибка 'Неверный кошелек!' в канале пересылок для сделки #{deal_id} - прерываю ожидание подтверждения")
                                                approval_found = False  # Явно помечаем как не найденное
                                                error_detected = True  # Помечаем, что обнаружена ошибка
                                                break  # Прерываем цикл проверки сообщений
                                            
                                            # Ищем текст "✅ Ваша заявка на вывод одобрена" (остальное не важно)
                                            # Проверяем точное совпадение текста подтверждения
                                            if "ваша заявка на вывод одобрена" in msg_text.lower():
                                                logger.info(f"✅ [АВТОВЫВОД] НАЙДЕНО подтверждение для сделки #{deal_id}! Текст: {msg_text[:100]}")
                                                logger.info(f"✅ [АВТОВЫВОД] Получено подтверждение успешного вывода в канале пересылок: {msg_text[:100]}")
                                                approval_found = True
                                                
                                                # Если это задача типа "release" (вывод покупателю), завершаем сделку
                                                if task_type == "release":
                                                    logger.info(f"🎉 [АВТОВЫВОД] Подтверждение получено для сделки #{deal_id}, завершаю сделку...")
                                                    try:
                                                        # Импортируем функцию завершения сделки
                                                        from database import complete_deal_after_withdrawal_confirmation
                                                        
                                                        # Завершаем сделку
                                                        logger.info(f"🔄 [АВТОВЫВОД] Вызываю complete_deal_after_withdrawal_confirmation для сделки #{deal_id}")
                                                        success = await complete_deal_after_withdrawal_confirmation(deal_id, bot=None)
                                                        if success:
                                                            logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена после подтверждения вывода")
                                                            # Удаляем задачу из очереди ТОЛЬКО после успешного завершения сделки
                                                            try:
                                                                # Читаем актуальную очередь из файла
                                                                queue = await _wq_load()
                                                                # Удаляем задачу
                                                                await _wq_remove(deal_id)
                                                                logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди после успешного завершения")
                                                            except Exception as e:
                                                                logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                                                            # ВАЖНО: Устанавливаем флаг, что сделка успешно завершена
                                                            deal_completed = True
                                                            # Прерываем все циклы и выходим из обработки, так как сделка успешно завершена
                                                            break
                                                        else:
                                                            logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось завершить сделку #{deal_id} (функция вернула False) - задача остается в очереди")
                                                    except ImportError as e:
                                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка импорта complete_deal_after_withdrawal_confirmation: {e}", exc_info=True)
                                                    except Exception as e:
                                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при завершении сделки #{deal_id}: {e}", exc_info=True)
                                                elif task_type == "refund":
                                                    # Если это задача типа "refund" (возврат депозита), просто удаляем задачу из очереди
                                                    logger.info(f"✅ [АВТОВЫВОД] Подтверждение возврата депозита получено для сделки #{deal_id}, удаляю задачу из очереди...")
                                                    try:
                                                        # Читаем актуальную очередь из файла
                                                        queue = await _wq_load()
                                                        # Удаляем задачу
                                                        await _wq_remove(deal_id, "refund")
                                                        logger.info(f"✅ [АВТОВЫВОД] Задача возврата депозита для сделки #{deal_id} удалена из очереди после успешного подтверждения")
                                                        # Помечаем, что задача успешно обработана
                                                        deal_completed = True
                                                        approval_found = True  # Явно устанавливаем approval_found для refund
                                                        # Прерываем все циклы и выходим из обработки
                                                        break
                                                    except Exception as e:
                                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при удалении задачи возврата из очереди: {e}", exc_info=True)
                                                        # Даже если удаление не удалось, подтверждение получено, помечаем это
                                                        approval_found = True
                                                
                                                # Прерываем цикл проверки сообщений
                                                break
                                        
                                        if approval_found or error_detected or deal_completed:
                                            break  # Прерываем внешний цикл while, если найдено подтверждение, ошибка или сделка завершена
                                    except Exception as e:
                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при проверке канала пересылок: {e}", exc_info=True)
                                        # Ошибка при проверке канала - помечаем, что подтверждение не найдено
                                        approval_found = False
                                        error_detected = True
                                        break  # Прерываем цикл проверки
                                
                                # Логируем результат ожидания
                                elapsed_time = time.time() - start_time
                                if deal_completed:
                                    if task_type == "refund":
                                        logger.info(f"✅ [АВТОВЫВОД] Возврат депозита для сделки #{deal_id} успешно обработан за {elapsed_time:.1f} секунд")
                                    else:
                                        logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена за {elapsed_time:.1f} секунд")
                                elif approval_found:
                                    logger.info(f"✅ [АВТОВЫВОД] Подтверждение найдено для сделки #{deal_id} за {elapsed_time:.1f} секунд")
                                else:
                                    logger.warning(f"⚠️ [АВТОВЫВОД] Подтверждение НЕ найдено для сделки #{deal_id} после ожидания {elapsed_time:.1f} секунд (максимум {wait_duration} секунд)")
                        except Exception as e:
                            logger.error(f"❌ [АВТОВЫВОД] Критическая ошибка в цикле ожидания подтверждения для сделки #{deal_id}: {e}", exc_info=True)
                            approval_found = False  # Убеждаемся, что approval_found = False при ошибке
                            error_detected = True  # Помечаем, что обнаружена ошибка
                        
                        # ВАЖНО: Если сделка успешно завершена ИЛИ задача типа refund успешно обработана, НЕ проверяем дальше и НЕ отправляем на ручной вывод
                        if deal_completed:
                            if task_type == "refund":
                                logger.info(f"✅ [АВТОВЫВОД] Возврат депозита для сделки #{deal_id} успешно обработан, пропускаю проверку на ручной вывод")
                            else:
                                logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена, пропускаю проверку на ручной вывод")
                            # ВАЖНО: Для задач типа refund, если deal_completed = True, задача уже удалена из очереди, выходим из обработки
                            # (не нужно делать continue, так как задача уже обработана и удалена)
                        elif approval_found and task_type == "refund":
                            # Для задач типа refund, если подтверждение найдено, но deal_completed не установлен (например, из-за ошибки удаления из очереди),
                            # все равно пропускаем проверку на ручной вывод, так как возврат уже выполнен
                            logger.info(f"✅ [АВТОВЫВОД] Подтверждение возврата депозита для сделки #{deal_id} получено, пропускаю проверку на ручной вывод")
                            # Пытаемся удалить задачу из очереди еще раз
                            try:
                                queue = await _wq_load()
                                await _wq_remove(deal_id, "refund")
                                logger.info(f"✅ [АВТОВЫВОД] Задача возврата депозита для сделки #{deal_id} удалена из очереди")
                            except Exception as e:
                                logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при повторном удалении задачи возврата из очереди: {e}")
                            # ВАЖНО: Для задач типа refund, если approval_found = True, задача уже обработана, выходим из обработки
                            # (не нужно делать continue, так как задача уже обработана)
                        else:
                                # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Проверяем, не завершена ли сделка в базе данных
                                # (на случай, если она была завершена в другом месте, например, в личном чате)
                                deal_already_completed = False
                                try:
                                    from database import get_deal, DealStatus
                                    current_deal = await get_deal(deal_id)
                                    if current_deal and current_deal.get('status') == DealStatus.RELEASED and current_deal.get('completed_at'):
                                        logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} уже завершена в базе данных (статус: RELEASED), пропускаю проверку на ручной вывод")
                                        deal_already_completed = True
                                        # Удаляем задачу из очереди, так как сделка уже завершена
                                        try:
                                            queue = await _wq_load()
                                            await _wq_remove(deal_id)
                                            logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (сделка уже завершена)")
                                        except Exception as e:
                                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                                except Exception as check_error:
                                    logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при проверке статуса сделки #{deal_id}: {check_error}")
                                
                                # Если сделка уже завершена, пропускаем отправку на ручной вывод
                                if deal_already_completed:
                                    # Задача уже обработана, переходим к следующей задаче
                                    break  # Выходим из обработки текущей задачи
                                
                                # КРИТИЧЕСКИ ВАЖНО: После 15 секунд ожидания ОБЯЗАТЕЛЬНО проверяем и отправляем на ручной вывод
                                # Это гарантирует, что если автовывод не сработал, система автоматически предложит ручной вывод
                                elapsed_time = time.time() - start_time
                                
                                # Проверяем approval_found ПОСЛЕ выхода из цикла while
                                # Это гарантирует, что мы всегда проверяем результат, даже если произошла ошибка
                                # ВАЖНО: Если автовывод не сработал в течение 10 секунд ИЛИ произошла ошибка - ОБЯЗАТЕЛЬНО отправляем на ручной вывод
                                # НО: Для задач типа refund, если approval_found = True, не отправляем на ручной вывод
                                # ВАЖНО: Для задач типа refund, если approval_found = True, не отправляем на ручной вывод
                                if task_type == "refund" and approval_found:
                                    logger.info(f"✅ [АВТОВЫВОД] Для задачи типа refund подтверждение найдено, пропускаю проверку на ручной вывод")
                                elif not approval_found:
                                    # ПРОВЕРКА: Если прошло 10 секунд ИЛИ обнаружена ошибка - ОБЯЗАТЕЛЬНО отправляем на ручной вывод
                                    should_send_manual = elapsed_time >= wait_duration or error_detected
                                    
                                    if should_send_manual:
                                        if error_detected:
                                            logger.error(f"❌ [АВТОВЫВОД] КРИТИЧЕСКИ ВАЖНО: Обнаружена ошибка при автовыводе для сделки #{deal_id} - ОБЯЗАТЕЛЬНО отправляю на ручной вывод!")
                                        else:
                                            logger.error(f"❌ [АВТОВЫВОД] КРИТИЧЕСКИ ВАЖНО: Автовывод не сработал в течение {wait_duration} секунд для сделки #{deal_id} - ОБЯЗАТЕЛЬНО отправляю на ручной вывод!")
                                        
                                        # Подтверждение не пришло или произошла ошибка - отправляем на ручной вывод
                                        logger.warning(f"⚠️ [АВТОВЫВОД] Подтверждение успешного вывода НЕ получено (время: {elapsed_time:.1f} сек, ошибка: {error_detected}) - отправляю на ручной вывод")
                                        
                                        try:
                                            admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                                            if admin_chat_id:
                                                try:
                                                    # Убираем пробелы и невидимые символы
                                                    admin_chat_id_clean = admin_chat_id.strip()
                                                    admin_chat_id_int = int(admin_chat_id_clean)
                                                    error_reason = "обнаружена ошибка" if error_detected else f"не получено подтверждение в течение {wait_duration} секунд"
                                                    await send_admin_notification(
                                                        deal_id=deal_id,
                                                        user_id=user_id,
                                                        wallet=wallet,
                                                        amount=amount,
                                                        task_type=task_type,
                                                        error_reason=f"Команда /withdraw была отправлена, но {error_reason}"
                                                    )
                                                except ValueError as ve:
                                                    logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}, ошибка: {ve}")
                                                except Exception as send_error:
                                                    logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке сообщения админам: {send_error}", exc_info=True)
                                            else:
                                                logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
                                        except Exception as e:
                                            logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления о ручном выводе: {e}", exc_info=True)
                                        
                                        # ВАЖНО: Удаляем задачу из очереди, чтобы НЕ обрабатывать ее повторно!
                                        # Задача отправлена на ручной вывод, повторная обработка не нужна
                                        try:
                                            # Читаем актуальную очередь из файла
                                            queue = await _wq_load()
                                            # Удаляем задачу
                                            await _wq_remove(deal_id)
                                            logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (требует ручного вывода)")
                                        except Exception as e:
                                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                        
                        # Сохраняем очередь ТОЛЬКО если задача была удалена (после успешного завершения или отправки на ручной вывод)
                        # Проверяем, была ли задача удалена (по deal_id)
                        try:
                            current_queue = await _wq_load()
                            task_removed = not any(t.get("deal_id") == deal_id for t in current_queue)
                            if task_removed:
                                logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} была удалена из очереди (осталось задач: {len(current_queue)})")
                        except Exception as e:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при проверке очереди: {e}")
                        
                    except Exception as e:
                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке команды вывода: {e}", exc_info=True)
                        # При любой ошибке отправляем на ручной вывод
                        logger.warning(f"⚠️ [АВТОВЫВОД] Отправляю задачу на ручной вывод из-за ошибки: {e}")
                        try:
                            admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                            if admin_chat_id:
                                try:
                                    admin_chat_id_int = int(admin_chat_id)
                                    await send_admin_notification(
                                        deal_id=deal_id,
                                        user_id=user_id,
                                        wallet=wallet,
                                        amount=amount,
                                        task_type=task_type,
                                        error_reason=f"Ошибка при автовыводе: {str(e)[:200]}"
                                    )
                                except ValueError:
                                    logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}")
                            else:
                                logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
                        except Exception as e2:
                            logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления о ручном выводе: {e2}", exc_info=True)
                        
                        # Удаляем задачу из очереди, чтобы не обрабатывать ее повторно
                        try:
                            # Читаем актуальную очередь из файла
                            queue = await _wq_load()
                            # Удаляем задачу
                            await _wq_remove(deal_id)
                            logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (требует ручного вывода из-за ошибки)")
                        except Exception as e3:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e3}")
                else:
                    logger.warning(f"⚠️ [АВТОВЫВОД] Неверные данные в задаче: {task}")
                    # Удаляем неверную задачу
                    try:
                        await _wq_remove(deal_id)
                        logger.info(f"✅ [АВТОВЫВОД] Неверная задача удалена из очереди")
                    except:
                        pass
            
            # Проверяем очередь каждую секунду
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"❌ [АВТОВЫВОД] Ошибка в обработке очереди: {e}", exc_info=True)
            await asyncio.sleep(1)


if __name__ == "__main__":
    async def main():
        max_retries = 5
        retry_delay = 10
        
        while True:
            try:
                # Запускаем клиент
                logger.info("🔌 Запускаю Pyrogram клиент...")
                await app.start()
                logger.info("✅ Pyrogram клиент запущен")
                
                # Запускаем обработчик очереди возврата депозитов в фоне
                asyncio.create_task(process_withdraw_queue())
                logger.info("✅ Обработчик очереди возврата депозитов запущен")
                
                # Настраиваем глобальный обработчик исключений для asyncio после запуска
                try:
                    loop = asyncio.get_running_loop()
                    loop.set_exception_handler(asyncio_exception_handler)
                except RuntimeError:
                    loop = asyncio.get_event_loop()
                    loop.set_exception_handler(asyncio_exception_handler)
                
                logger.info("=" * 60)
                logger.info("МОСТИК ЗАПУЩЕН")
                logger.info("=" * 60)
                logger.info(f"Ожидаю сообщения от: @{SOURCE_BOT_USERNAME}")
                logger.info(f"Пересылаю в чат: {TARGET_CHAT_ID}")
                logger.info("")
                
                # Инициализируем кэш чата при старте
                await init_chat_cache()
                
                logger.info("Мостик запущен, проверяю доступность чата...")
                if await check_chat_access():
                    logger.info("✅ Мостик готов к работе!")
                    logger.info("")
                    logger.info("📋 ИНСТРУКЦИЯ:")
                    logger.info("1. @BmbP2p_bot должен отправить вам уведомление о депозите")
                    logger.info("2. Мостик автоматически перехватит это сообщение")
                    logger.info("3. Сообщение будет переслано в канал/группу")
                    logger.info("")
                    logger.info("🔍 ДЛЯ ОТЛАДКИ:")
                    logger.info("- Отправьте ЛЮБОЕ сообщение в личные сообщения (от любого бота или пользователя)")
                    logger.info("- В логах появится информация о всех полученных личных сообщениях")
                    logger.info("")
                    logger.info("⏳ Ожидаю сообщения...")
                else:
                    logger.warning("⚠️ Чат недоступен, но мостик продолжит работу. Проверьте доступ позже.")
                    logger.info("💡 Примечание: ошибки о неизвестных каналах будут игнорироваться")
                
                # Держим приложение запущенным
                try:
                    await asyncio.Event().wait()  # Бесконечное ожидание
                except KeyboardInterrupt:
                    logger.info("Остановка мостика...")
                    break
                except Exception as e:
                    logger.error(f"❌ Ошибка в основном цикле: {e}", exc_info=True)
                    raise
                finally:
                    try:
                        await app.stop()
                    except:
                        pass
                    
            except (ConnectionError, OSError, Exception) as e:
                logger.error(f"❌ Ошибка соединения: {e}", exc_info=True)
                logger.warning(f"⚠️ Переподключение через {retry_delay} секунд...")
                
                try:
                    await app.stop()
                except:
                    pass
                
                await asyncio.sleep(retry_delay)
                
                # Увеличиваем задержку для следующей попытки (максимум 60 секунд)
                retry_delay = min(retry_delay * 1.5, 60)
                
            except KeyboardInterrupt:
                logger.info("Остановка мостика...")
                break
    
    try:
        app.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по запросу пользователя")
    except Exception as e:
        logger.error(f"Ошибка при запуске: {e}", exc_info=True)
        raise


@app.on_message(filters.private)
async def on_any_private_message(_, msg):
    """Обрабатывает все личные сообщения и проверяет отправителя"""
    txt = msg.text or msg.caption or ""
    
    # Логируем все личные сообщения для отладки
    from_username = msg.from_user.username if msg.from_user else None
    from_id = msg.from_user.id if msg.from_user else None
    logger.info(f"🔍 [FORWARDER DEBUG] Личное сообщение от: {from_username or 'N/A'} (ID: {from_id or 'N/A'})")
    logger.info(f"🔍 [FORWARDER DEBUG] Текст: {txt[:100] if txt else 'N/A'}")
    logger.info(f"🔍 [FORWARDER] Дошли до проверки from_user")
    
    # Проверяем, что сообщение от нужного бота
    logger.info(f"🔍 [FORWARDER] Проверяю from_user: {msg.from_user is not None}")
    if not msg.from_user:
        logger.info("⚠️ [FORWARDER] Сообщение без from_user, пропускаем")
        return
    
    logger.info(f"🔍 [FORWARDER] Проверяю from_username: {from_username}")
    if not from_username:
        logger.info("⚠️ [FORWARDER] Сообщение без username, пропускаем")
        return
    
    # Нормализуем username для сравнения (убираем @ и приводим к нижнему регистру)
    logger.info(f"🔍 [FORWARDER] SOURCE_BOT_USERNAMES из .env: '{SOURCE_BOT_USERNAMES_RAW}'")
    from_username_normalized = from_username.lower().strip() if from_username else None
    
    logger.info(
        f"🔍 [FORWARDER] Сравниваю: ожидаем один из {SOURCE_BOT_USERNAMES} "
        f"(из SOURCE_BOT_USERNAMES='{SOURCE_BOT_USERNAMES_RAW}'), "
        f"получили '{from_username_normalized}' (из from_username='{from_username}')"
    )
    
    # Проверяем, это сообщение от бота для депозитов (SOURCE_BOT_USERNAME) или от бота для вывода (@BmbP2p_bot)
    is_deposit_bot = from_username_normalized in SOURCE_BOT_USERNAMES
    is_withdraw_bot = from_username_normalized == WITHDRAW_BOT_USERNAME_NORMALIZED
    
    if is_withdraw_bot:
        # Это сообщение от бота для вывода - проверяем, есть ли кнопка подтверждения
        logger.info(f"💰 [АВТОВЫВОД] Получено сообщение от бота вывода @{from_username}")
        
        # Проверяем, есть ли ошибка "Неверный кошелек!"
        if "неверный кошелек" in txt.lower() or "⚠️неверный кошелек" in txt.lower() or "⚠️ Неверный кошелек" in txt:
            logger.warning(f"⚠️ [АВТОВЫВОД] Обнаружена ошибка 'Неверный кошелек!' - отправляю на ручной вывод")
            # Отправляем уведомление админам
            try:
                from dotenv import load_dotenv
                admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                if admin_chat_id:
                    try:
                        # Извлекаем данные из текста ошибки
                        deal_id_match = re.search(r'#(\d+)', txt)
                        deal_id = int(deal_id_match.group(1)) if deal_id_match else 0
                        wallet_match = re.search(r'кошелек[:\s]+([a-zA-Z0-9]+)', txt, re.IGNORECASE)
                        wallet = wallet_match.group(1) if wallet_match else "не указан"
                        amount_match = re.search(r'(\d+\.?\d*)\s*BTC', txt, re.IGNORECASE)
                        amount = amount_match.group(1) if amount_match else "не указана"
                        user_id_match = re.search(r'ID[:\s]+(\d+)', txt)
                        user_id = int(user_id_match.group(1)) if user_id_match else 0
                        
                        await send_admin_notification(
                            deal_id=deal_id,
                            user_id=user_id,
                            wallet=wallet,
                            amount=amount,
                            task_type="release",
                            error_reason="Неверный кошелек!"
                        )
                    except ValueError:
                        logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}")
                else:
                    logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
            except Exception as e:
                logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления админам: {e}", exc_info=True)
            # Не пересылаем это сообщение в обычный чат, так как это ошибка
            return
        
        # Проверяем, есть ли подтверждение "✅ Ваша заявка на вывод одобрена" в личном чате
        if "ваша заявка на вывод одобрена" in txt.lower():
            logger.info(f"✅ [АВТОВЫВОД] Получено подтверждение вывода в личном чате от @{from_username}: {txt[:100]}")
            # Ищем активную задачу на вывод для этого сообщения
            try:
                queue = await _wq_load()
                if queue:
                    
                    # Ищем задачу типа "release" (вывод покупателю) или "refund" (возврат депозита) - берем самую последнюю
                    release_tasks = [task for task in queue if task.get("type") == "release"]
                    refund_tasks = [task for task in queue if task.get("type") == "refund"]
                    
                    # Обрабатываем задачи типа "release"
                    if release_tasks:
                        # Берем последнюю задачу (самую свежую)
                        task = release_tasks[-1]
                        deal_id = task.get("deal_id")
                        if deal_id:
                            logger.info(f"🎉 [АВТОВЫВОД] Найдена задача release для сделки #{deal_id}, завершаю сделку...")
                            try:
                                from database import complete_deal_after_withdrawal_confirmation
                                logger.info(f"🔄 [АВТОВЫВОД] Вызываю complete_deal_after_withdrawal_confirmation для сделки #{deal_id}")
                                success = await complete_deal_after_withdrawal_confirmation(deal_id, bot=None)
                                if success:
                                    logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена после подтверждения вывода")
                                    # Удаляем задачу из очереди ТОЛЬКО после успешного завершения сделки
                                    try:
                                        await _wq_remove(task.get('deal_id'), task.get('type'))
                                        logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди после успешного завершения")
                                    except Exception as e:
                                        logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                                else:
                                    logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось завершить сделку #{deal_id} (функция вернула False) - задача остается в очереди")
                            except ImportError as e:
                                logger.error(f"❌ [АВТОВЫВОД] Ошибка импорта complete_deal_after_withdrawal_confirmation: {e}", exc_info=True)
                            except Exception as e:
                                logger.error(f"❌ [АВТОВЫВОД] Ошибка при завершении сделки #{deal_id}: {e}", exc_info=True)
                    
                    # Обрабатываем задачи типа "refund"
                    if refund_tasks:
                        # Берем последнюю задачу (самую свежую)
                        task = refund_tasks[-1]
                        deal_id = task.get("deal_id")
                        if deal_id:
                            logger.info(f"✅ [АВТОВЫВОД] Найдена задача refund для сделки #{deal_id}, удаляю из очереди...")
                            try:
                                # Удаляем задачу из очереди
                                await _wq_remove(task.get('deal_id'), task.get('type'))
                                logger.info(f"✅ [АВТОВЫВОД] Задача возврата депозита для сделки #{deal_id} удалена из очереди после подтверждения в личном чате")
                            except Exception as e:
                                logger.error(f"❌ [АВТОВЫВОД] Ошибка при удалении задачи возврата из очереди: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"❌ [АВТОВЫВОД] Ошибка при обработке подтверждения в личном чате: {e}", exc_info=True)
        
        # Проверяем, есть ли кнопка подтверждения
        if msg.reply_markup and msg.reply_markup.inline_keyboard:
            # Ищем кнопку подтверждения
            for row in msg.reply_markup.inline_keyboard:
                for button in row:
                    button_text = button.text.lower()
                    # Ищем кнопку с текстом "подтверждаю", "подтвердить", "✅" и т.д.
                    if any(keyword in button_text for keyword in ["подтверждаю", "подтвердить", "confirm", "✅", "да", "yes", "ok"]):
                        logger.info(f"🔘 [АВТОВЫВОД] Найдена кнопка подтверждения: {button.text}, callback_data: {button.callback_data}")
                        
                        # Нажимаем кнопку автоматически
                        try:
                            if button.callback_data:
                                # Отправляем callback_query
                                from pyrogram.raw import functions, types
                                peer = await app.resolve_peer(msg.chat.id)
                                callback_data_bytes = button.callback_data.encode('utf-8') if isinstance(button.callback_data, str) else button.callback_data
                                
                                result = await app.invoke(
                                    functions.messages.GetBotCallbackAnswer(
                                        peer=peer,
                                        msg_id=msg.id,
                                        data=callback_data_bytes
                                    )
                                )
                                logger.info(f"✅ [АВТОВЫВОД] Кнопка подтверждения нажата автоматически, ответ: {result}")
                                return  # Не пересылаем это сообщение
                            else:
                                logger.warning(f"⚠️ [АВТОВЫВОД] У кнопки нет callback_data")
                        except Exception as e:
                            logger.error(f"❌ [АВТОВЫВОД] Ошибка при нажатии кнопки: {e}", exc_info=True)
        
        # Если это не кнопка подтверждения или ошибка - просто пересылаем сообщение
        logger.info(f"📨 [FORWARDER] Пересылаю сообщение от бота вывода в чат {TARGET_CHAT_ID}")
        try:
            success = await send_to_target_chat(msg, txt)
            if success:
                logger.info(f"✅ [FORWARDER] Сообщение успешно переслано в чат {TARGET_CHAT_ID}")
        except Exception as e:
            logger.error(f"❌ [FORWARDER] Ошибка при пересылке сообщения: {e}", exc_info=True)
        return
    
    # Проверяем по username (без учета регистра и @)
    # Используем ТОЛЬКО username из SOURCE_BOT_USERNAME из .env
    if is_deposit_bot:
        logger.info(f"✅ [FORWARDER] Найдено сообщение от бота (получено от: @{from_username}, ожидаем: @{SOURCE_BOT_USERNAME})")
    else:
        # Не от нужного бота
        logger.info(f"⚠️ [FORWARDER] Сообщение не от нужного бота (получено от: @{from_username or 'N/A'}, ожидаем: @{SOURCE_BOT_USERNAME}), пропускаем")
        return
    
    if not txt:
        logger.warning(f"⚠️ [FORWARDER] Сообщение без текста, пропускаем. msg.text={msg.text}, msg.caption={msg.caption}")
        return
    
    logger.info(f"📨 [FORWARDER] Получено сообщение от бота: {txt[:200]}")
    logger.info(f"📨 [FORWARDER] Пытаюсь переслать в чат {TARGET_CHAT_ID}")
    
    # Пересылаем оригинальное сообщение как есть (с оригинальным форматом)
    # Обработчик в main_handlers.py сам распарсит формат "Сумма X BTC ... 🤵🏼От: @username"
    logger.info(f"🔄 [FORWARDER] ДО try блока, вызываю send_to_target_chat для сообщения от @{from_username}")
    try:
        logger.info(f"🔄 [FORWARDER] ВНУТРИ try блока, вызываю send_to_target_chat")
        success = await send_to_target_chat(msg, txt)
        logger.info(f"🔄 [FORWARDER] send_to_target_chat вернул: {success}")
        
        if success:
            logger.info(f"✅ [FORWARDER] Сообщение успешно переслано в чат {TARGET_CHAT_ID}")
        else:
            logger.error(f"❌ [FORWARDER] Не удалось переслать сообщение в чат {TARGET_CHAT_ID}")
    except Exception as e:
        logger.error(f"❌ [FORWARDER] Ошибка при пересылке сообщения: {e}", exc_info=True)
        import traceback
        logger.error(f"❌ [FORWARDER] Traceback: {traceback.format_exc()}")


async def check_chat_access():
    """Проверка доступности целевого чата"""
    try:
        # Пробуем получить информацию о чате
        # Сначала пробуем как есть
        chat = None
        working_id = None
        chat_ids_to_try = [TARGET_CHAT_ID]
        
        # Если ID отрицательный и не начинается с -100, пробуем преобразовать
        if isinstance(TARGET_CHAT_ID, int) and TARGET_CHAT_ID < 0:
            if not str(TARGET_CHAT_ID).startswith("-100"):
                converted_id = int(f"-100{abs(TARGET_CHAT_ID)}")
                chat_ids_to_try.append(converted_id)
                logger.info(f"🔄 Будет попытка с преобразованным ID для канала: {converted_id}")
        
        # Пробуем каждый ID
        for chat_id in chat_ids_to_try:
            try:
                chat = await app.get_chat(chat_id)
                working_id = chat_id
                if chat_id != TARGET_CHAT_ID:
                    logger.warning(f"⚠️ Работает ID {chat_id} вместо {TARGET_CHAT_ID}")
                    logger.warning(f"💡 Рекомендуется обновить .env: DEPOSIT_NOTIFY_CHAT_ID={chat_id}")
                break
            except ValueError:
                continue
            except Exception as e:
                logger.debug(f"⚠️ Ошибка при проверке ID {chat_id}: {e}")
                continue
        
        if not chat:
            raise ValueError("Chat not found")
        
        logger.info(f"✅ Чат найден: {chat.title or chat.id} (тип: {chat.type})")
        
        # Пробуем отправить тестовое сообщение (оно будет удалено сразу)
        # Пробуем разные форматы ID
        test_sent = False
        chat_ids_to_try = [TARGET_CHAT_ID]
        if isinstance(TARGET_CHAT_ID, int) and TARGET_CHAT_ID < 0 and not str(TARGET_CHAT_ID).startswith("-100"):
            converted_id = int(f"-100{abs(TARGET_CHAT_ID)}")
            chat_ids_to_try.append(converted_id)
        
        for chat_id in chat_ids_to_try:
            try:
                test_msg = await app.send_message(
                    chat_id, 
                    "🔧 Тестовое сообщение от мостика (будет удалено)"
                )
                # Удаляем тестовое сообщение
                try:
                    await app.delete_messages(chat_id, test_msg.id)
                except:
                    pass
                logger.info(f"✅ Права на отправку сообщений подтверждены (ID: {chat_id})")
                test_sent = True
                break
            except Exception as send_err:
                logger.debug(f"⚠️ Не удалось отправить тестовое сообщение в {chat_id}: {send_err}")
                continue
        
        if not test_sent:
            logger.warning("⚠️ Не удалось отправить тестовое сообщение ни в один из вариантов ID")
            logger.warning("Мостик может пересылать сообщения, но не сможет отправлять новые")
        
        return True
    except ValueError as e:
        error_str = str(e)
        if "Peer id invalid" in error_str or "chat not found" in error_str.lower():
            logger.error(f"❌ Чат с ID {TARGET_CHAT_ID} не найден или недоступен")
            logger.error("")
            logger.error("🔍 ВОЗМОЖНЫЕ ПРИЧИНЫ:")
            logger.error("1. Аккаунт, от которого запущен мостик, НЕ добавлен в этот чат")
            logger.error("2. ID чата указан неправильно")
            logger.error("3. Для каналов используйте формат: -1001234567890")
            logger.error("")
            logger.error("💡 РЕШЕНИЕ:")
            logger.error("1. Запустите скрипт: py get_chat_id.py")
            logger.error("2. Найдите нужный чат в списке")
            logger.error("3. Скопируйте правильный ID и обновите .env файл")
            logger.error("4. Убедитесь, что ВАШ ЛИЧНЫЙ АККАУНТ добавлен в чат (не бот!)")
        else:
            logger.error(f"❌ Ошибка доступа к чату: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Не удалось получить доступ к чату {TARGET_CHAT_ID}: {e}")
        logger.error("")
        logger.error("🔍 ПРОВЕРЬТЕ:")
        logger.error("1. Вы добавлены в этот канал/группу?")
        logger.error("2. ID чата правильный?")
        logger.error("3. Для каналов используйте формат: -1001234567890")
        logger.error("")
        logger.error("💡 Запустите: py get_chat_id.py для получения правильного ID")
        return False


@app.on_raw_update()
async def on_raw_update(client, update, users, chats):
    # Игнорируем обновления о неизвестных чатах
    # Проверяем, есть ли обновление, связанное с нашим ботом-источником
    try:
        # Пытаемся обработать только если это обновление связано с личными сообщениями
        # Все остальные обновления игнорируем
        pass
    except Exception:
        # Игнорируем все ошибки при обработке raw_update
        pass


def asyncio_exception_handler(loop, context):
    """Глобальный обработчик исключений asyncio"""
    exception = context.get('exception')
    if exception:
        # Игнорируем ошибки о неизвестных peer ID (каналах)
        if isinstance(exception, (ValueError, KeyError)):
            error_msg = str(exception)
            if "Peer id invalid" in error_msg or "ID not found" in error_msg:
                return  # Игнорируем эту ошибку
    # Логируем другие ошибки
    logger.debug(f"Игнорируемая ошибка asyncio: {context.get('message', 'Unknown error')}")


async def init_chat_cache():
    """Инициализация кэша чата при старте"""
    global _cached_chat_id, _cached_chat_found
    
    logger.info("🔍 Инициализация кэша чата...")
    try:
        # Пробуем найти чат через get_dialogs
        target_id_for_comparison = TARGET_CHAT_ID_INT if TARGET_CHAT_ID_INT is not None else None
        if target_id_for_comparison is None and isinstance(TARGET_CHAT_ID, str) and TARGET_CHAT_ID.lstrip('-').isdigit():
            try:
                target_id_for_comparison = int(TARGET_CHAT_ID)
            except:
                pass
        
        if target_id_for_comparison:
            logger.info(f"🔍 Ищу чат через get_dialogs, ID: {target_id_for_comparison}")
            async for dialog in app.get_dialogs():
                if dialog.chat.id == target_id_for_comparison:
                    _cached_chat_id = dialog.chat.id
                    _cached_chat_found = True
                    logger.info(f"✅ Чат найден и закэширован: {dialog.chat.title if hasattr(dialog.chat, 'title') else 'N/A'} (ID: {dialog.chat.id})")
                    return True
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при инициализации кэша: {e}")
    
    # Если не нашли через get_dialogs, пробуем get_chat
    try:
        chat_info = await app.get_chat(TARGET_CHAT_ID)
        _cached_chat_id = chat_info.id
        _cached_chat_found = True
        logger.info(f"✅ Чат найден через get_chat и закэширован: {chat_info.title if hasattr(chat_info, 'title') else 'N/A'} (ID: {chat_info.id})")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Не удалось инициализировать кэш через get_chat: {e}")
        return False


async def process_withdraw_queue():
    """Обработка очереди задач на возврат депозита из файла"""
    import json
    WITHDRAW_BOT_USERNAME = "BmbP2p_bot"
    
    logger.info(f"💰 [АВТОВЫВОД] === ЗАПУСК ОБРАБОТЧИКА ОЧЕРЕДИ ===")
    logger.info(f"💰 [АВТОВЫВОД] Файл очереди: {WITHDRAW_QUEUE_FILE}")
    logger.info(f"💰 [АВТОВЫВОД] Файл существует: {WITHDRAW_QUEUE_FILE.exists()}")
    
    processed_count = 0
    last_file_check = 0
    
    while True:
        try:
            queue = await _wq_load()
            if queue:
                logger.info(f"💰 [АВТОВЫВОД] ✅ Найдено задач в очереди: {len(queue)}")
                for i, task in enumerate(queue):
                    logger.info(f"💰 [АВТОВЫВОД] Задача #{i+1}: сделка {task.get('deal_id')}, кошелек {task.get('wallet')}, сумма {task.get('amount')} BTC")
            
            # Обрабатываем задачи из очереди
            if queue:
                # ВАЖНО: НЕ удаляем задачу из очереди сразу! Берем копию для обработки
                task = queue[0]  # Берем первую задачу БЕЗ удаления
                wallet = task.get("wallet")
                amount = task.get("amount")
                deal_id = task.get("deal_id")
                user_id = task.get("user_id")
                task_type = task.get("type", "refund")  # Тип задачи: release или refund
                
                logger.info(f"💰 [АВТОВЫВОД] Тип задачи: {task_type}")
                
                # Проверяем наличие обязательных данных
                if not wallet or not amount:
                    logger.warning(f"⚠️ [АВТОВЫВОД] Отсутствуют обязательные данные в задаче: wallet={wallet}, amount={amount}")
                    logger.warning(f"⚠️ [АВТОВЫВОД] Отправляю задачу на ручной вывод (неполные данные)")
                    try:
                        admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                        if admin_chat_id:
                            try:
                                admin_chat_id_int = int(admin_chat_id)
                                await send_admin_notification(
                                    deal_id=deal_id or 0,
                                    user_id=user_id or 0,
                                    wallet=wallet or "не указан",
                                    amount=amount or "не указана",
                                    task_type=task_type,
                                    error_reason="В задаче отсутствуют обязательные данные (wallet или amount)"
                                )
                            except ValueError:
                                logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}")
                        else:
                            logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
                    except Exception as e:
                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления о ручном выводе: {e}", exc_info=True)
                    
                    # Удаляем задачу из очереди
                    try:
                        queue = await _wq_load()
                        await _wq_remove(deal_id)
                        logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (требует ручного вывода, неполные данные)")
                    except Exception as e:
                        logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                elif wallet and amount:
                    try:
                        logger.info(f"💰 [АВТОВЫВОД] === НАЧАЛО ОБРАБОТКИ ЗАДАЧИ ===")
                        logger.info(f"💰 [АВТОВЫВОД] Сделка #{deal_id}, пользователь {user_id}")
                        logger.info(f"💰 [АВТОВЫВОД] Кошелек: {wallet}, сумма: {amount} BTC")
                        
                        # Формируем команду
                        command = f"/withdraw {wallet} {amount}"
                        logger.info(f"💰 [АВТОВЫВОД] Команда: {command}")
                        
                        # Получаем chat_id бота
                        bot_username = WITHDRAW_BOT_USERNAME.replace("@", "")
                        logger.info(f"💰 [АВТОВЫВОД] Получаю chat_id бота @{bot_username}...")
                        try:
                            bot_chat = await app.get_chat(bot_username)
                            bot_chat_id = bot_chat.id
                            logger.info(f"✅ [АВТОВЫВОД] Chat ID бота: {bot_chat_id}")
                        except Exception as e:
                            logger.error(f"❌ [АВТОВЫВОД] Не удалось получить chat_id бота: {e}")
                            # Пробуем через get_dialogs
                            logger.info(f"🔍 [АВТОВЫВОД] Пробую найти бота через get_dialogs...")
                            bot_username_normalized = bot_username.lower()
                            bot_chat_id = None
                            async for dialog in app.get_dialogs():
                                if dialog.chat.username and dialog.chat.username.lower() == bot_username_normalized:
                                    bot_chat_id = dialog.chat.id
                                    logger.info(f"✅ [АВТОВЫВОД] Бот найден через get_dialogs, Chat ID: {bot_chat_id}")
                                    break
                            
                            if not bot_chat_id:
                                logger.error(f"❌ [АВТОВЫВОД] Бот @{WITHDRAW_BOT_USERNAME} не найден!")
                                raise Exception(f"Бот @{WITHDRAW_BOT_USERNAME} не найден")
                        
                        # Отправляем команду
                        logger.info(f"💰 [АВТОВЫВОД] Отправляю команду боту @{WITHDRAW_BOT_USERNAME}...")
                        sent_message = await app.send_message(bot_chat_id, command)
                        logger.info(f"✅ [АВТОВЫВОД] Команда отправлена боту @{WITHDRAW_BOT_USERNAME}, message_id: {sent_message.id}")
                        logger.info(f"✅ [АВТОВЫВОД] Ожидаю ответ от бота для автоматического нажатия кнопки подтверждения...")
                        
                        # Ждем ответ от бота (максимум 5 секунд)
                        await asyncio.sleep(2)
                        
                        # Ищем ответ от бота с кнопкой подтверждения
                        response_found = False
                        confirmation_message_id = None
                        async for msg in app.get_chat_history(bot_chat_id, limit=10):
                            if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == bot_username.lower():
                                if msg.reply_markup and msg.reply_markup.inline_keyboard:
                                    # Ищем кнопку подтверждения
                                    for row in msg.reply_markup.inline_keyboard:
                                        for button in row:
                                            button_text = button.text.lower()
                                            if any(keyword in button_text for keyword in ["подтверждаю", "подтвердить", "confirm", "✅", "да", "yes", "ok"]):
                                                logger.info(f"🔘 [АВТОВЫВОД] Найдена кнопка подтверждения: {button.text}")
                                                confirmation_message_id = msg.id
                                                
                                                # Нажимаем кнопку
                                                try:
                                                    from pyrogram.raw import functions
                                                    peer = await app.resolve_peer(bot_chat_id)
                                                    callback_data_bytes = button.callback_data.encode('utf-8') if isinstance(button.callback_data, str) else button.callback_data
                                                    
                                                    result = await app.invoke(
                                                        functions.messages.GetBotCallbackAnswer(
                                                            peer=peer,
                                                            msg_id=msg.id,
                                                            data=callback_data_bytes
                                                        )
                                                    )
                                                    logger.info(f"✅ [АВТОВЫВОД] Кнопка подтверждения нажата автоматически!")
                                                    response_found = True
                                                    break
                                                except Exception as e:
                                                    logger.error(f"❌ [АВТОВЫВОД] Ошибка при нажатии кнопки: {e}", exc_info=True)
                                                    # Ошибка при нажатии кнопки - отправляем на ручной вывод
                                                    raise  # Пробрасываем ошибку дальше, чтобы она была обработана в общем блоке except
                                    
                                    if response_found:
                                        break
                            
                            if response_found:
                                break
                        
                        if not response_found:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Кнопка подтверждения не найдена в ответе от бота, но продолжаю ожидание подтверждения в канале пересылок...")
                            # Кнопка не найдена, но это не критично - подтверждение может прийти в канале пересылок
                            # Продолжаем ожидание подтверждения в канале пересылок
                        
                        # В любом случае ждем подтверждение успешного вывода в канале пересылок
                        logger.info(f"⏳ [АВТОВЫВОД] Ожидаю подтверждение успешного вывода от @{WITHDRAW_BOT_USERNAME} в канале пересылок...")
                        
                        # Запоминаем время начала ожидания и последний message_id в канале пересылок
                        start_time = time.time()
                        wait_duration = 10  # Ждем 10 секунд
                        
                        # Получаем последний message_id в канале пересылок перед ожиданием
                        last_message_id_before = None
                        try:
                            async for msg in app.get_chat_history(TARGET_CHAT_ID, limit=1):
                                last_message_id_before = msg.id
                                break
                        except Exception as e:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось получить последний message_id канала: {e}")
                        
                        # Ждем до wait_duration секунд, проверяя канал пересылок
                        approval_found = False
                        error_detected = False  # Флаг для обнаружения ошибки "Неверный кошелек"
                        deal_completed = False  # Флаг для успешного завершения сделки
                        logger.info(f"⏳ [АВТОВЫВОД] Начинаю ожидание подтверждения для сделки #{deal_id} (максимум {wait_duration} секунд)")
                        
                        try:
                                while (time.time() - start_time) < wait_duration:
                                    await asyncio.sleep(0.5)  # Проверяем каждые 0.5 секунды
                                    
                                    # Проверяем новые сообщения в канале пересылок
                                    try:
                                        async for msg in app.get_chat_history(TARGET_CHAT_ID, limit=20):
                                            # Пропускаем сообщения, которые были до начала ожидания
                                            if last_message_id_before and msg.id <= last_message_id_before:
                                                continue
                                            
                                            # Проверяем текст сообщения (может быть переслано от @BmbP2p_bot)
                                            msg_text = (msg.text or msg.caption or "").strip()
                                            
                                            # Более подробное логирование для задач типа refund
                                            if task_type == "refund":
                                                logger.info(f"🔍 [АВТОВЫВОД] [REFUND] Проверяю сообщение {msg.id} для сделки #{deal_id}: {msg_text[:200]}...")
                                            else:
                                                logger.info(f"🔍 [АВТОВЫВОД] Проверяю сообщение {msg.id} для сделки #{deal_id}: {msg_text[:100]}...")
                                            
                                            # Проверяем на ошибку "⚠️Неверный кошелек!" - если есть, прерываем ожидание
                                            if "неверный кошелек" in msg_text.lower() or "⚠️неверный кошелек" in msg_text.lower() or "⚠️ Неверный кошелек" in msg_text:
                                                logger.warning(f"⚠️ [АВТОВЫВОД] Обнаружена ошибка 'Неверный кошелек!' в канале пересылок для сделки #{deal_id} - прерываю ожидание подтверждения")
                                                approval_found = False  # Явно помечаем как не найденное
                                                error_detected = True  # Помечаем, что обнаружена ошибка
                                                break  # Прерываем цикл проверки сообщений
                                            
                                            # Ищем текст "✅ Ваша заявка на вывод одобрена" (остальное не важно)
                                            # Проверяем точное совпадение текста подтверждения
                                            if "ваша заявка на вывод одобрена" in msg_text.lower():
                                                logger.info(f"✅ [АВТОВЫВОД] НАЙДЕНО подтверждение для сделки #{deal_id}! Текст: {msg_text[:100]}")
                                                logger.info(f"✅ [АВТОВЫВОД] Получено подтверждение успешного вывода в канале пересылок: {msg_text[:100]}")
                                                approval_found = True
                                                
                                                # Если это задача типа "release" (вывод покупателю), завершаем сделку
                                                if task_type == "release":
                                                    logger.info(f"🎉 [АВТОВЫВОД] Подтверждение получено для сделки #{deal_id}, завершаю сделку...")
                                                    try:
                                                        # Импортируем функцию завершения сделки
                                                        from database import complete_deal_after_withdrawal_confirmation
                                                        
                                                        # Завершаем сделку
                                                        logger.info(f"🔄 [АВТОВЫВОД] Вызываю complete_deal_after_withdrawal_confirmation для сделки #{deal_id}")
                                                        success = await complete_deal_after_withdrawal_confirmation(deal_id, bot=None)
                                                        if success:
                                                            logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена после подтверждения вывода")
                                                            # Удаляем задачу из очереди ТОЛЬКО после успешного завершения сделки
                                                            try:
                                                                # Читаем актуальную очередь из файла
                                                                queue = await _wq_load()
                                                                # Удаляем задачу
                                                                await _wq_remove(deal_id)
                                                                logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди после успешного завершения")
                                                            except Exception as e:
                                                                logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                                                            # ВАЖНО: Устанавливаем флаг, что сделка успешно завершена
                                                            deal_completed = True
                                                            # Прерываем все циклы и выходим из обработки, так как сделка успешно завершена
                                                            break
                                                        else:
                                                            logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось завершить сделку #{deal_id} (функция вернула False) - задача остается в очереди")
                                                    except ImportError as e:
                                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка импорта complete_deal_after_withdrawal_confirmation: {e}", exc_info=True)
                                                    except Exception as e:
                                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при завершении сделки #{deal_id}: {e}", exc_info=True)
                                                elif task_type == "refund":
                                                    # Если это задача типа "refund" (возврат депозита), просто удаляем задачу из очереди
                                                    logger.info(f"✅ [АВТОВЫВОД] Подтверждение возврата депозита получено для сделки #{deal_id}, удаляю задачу из очереди...")
                                                    try:
                                                        # Читаем актуальную очередь из файла
                                                        queue = await _wq_load()
                                                        # Удаляем задачу
                                                        await _wq_remove(deal_id, "refund")
                                                        logger.info(f"✅ [АВТОВЫВОД] Задача возврата депозита для сделки #{deal_id} удалена из очереди после успешного подтверждения")
                                                        # Помечаем, что задача успешно обработана
                                                        deal_completed = True
                                                        approval_found = True  # Явно устанавливаем approval_found для refund
                                                        # Прерываем все циклы и выходим из обработки
                                                        break
                                                    except Exception as e:
                                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при удалении задачи возврата из очереди: {e}", exc_info=True)
                                                        # Даже если удаление не удалось, подтверждение получено, помечаем это
                                                        approval_found = True
                                                
                                                # Прерываем цикл проверки сообщений
                                                break
                                        
                                        if approval_found or error_detected or deal_completed:
                                            break  # Прерываем внешний цикл while, если найдено подтверждение, ошибка или сделка завершена
                                    except Exception as e:
                                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при проверке канала пересылок: {e}", exc_info=True)
                                        # Ошибка при проверке канала - помечаем, что подтверждение не найдено
                                        approval_found = False
                                        error_detected = True
                                        break  # Прерываем цикл проверки
                                
                                # Логируем результат ожидания
                                elapsed_time = time.time() - start_time
                                if deal_completed:
                                    if task_type == "refund":
                                        logger.info(f"✅ [АВТОВЫВОД] Возврат депозита для сделки #{deal_id} успешно обработан за {elapsed_time:.1f} секунд")
                                    else:
                                        logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена за {elapsed_time:.1f} секунд")
                                elif approval_found:
                                    logger.info(f"✅ [АВТОВЫВОД] Подтверждение найдено для сделки #{deal_id} за {elapsed_time:.1f} секунд")
                                else:
                                    logger.warning(f"⚠️ [АВТОВЫВОД] Подтверждение НЕ найдено для сделки #{deal_id} после ожидания {elapsed_time:.1f} секунд (максимум {wait_duration} секунд)")
                        except Exception as e:
                            logger.error(f"❌ [АВТОВЫВОД] Критическая ошибка в цикле ожидания подтверждения для сделки #{deal_id}: {e}", exc_info=True)
                            approval_found = False  # Убеждаемся, что approval_found = False при ошибке
                            error_detected = True  # Помечаем, что обнаружена ошибка
                        
                        # ВАЖНО: Если сделка успешно завершена ИЛИ задача типа refund успешно обработана, НЕ проверяем дальше и НЕ отправляем на ручной вывод
                        if deal_completed:
                            if task_type == "refund":
                                logger.info(f"✅ [АВТОВЫВОД] Возврат депозита для сделки #{deal_id} успешно обработан, пропускаю проверку на ручной вывод")
                            else:
                                logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} успешно завершена, пропускаю проверку на ручной вывод")
                            # ВАЖНО: Для задач типа refund, если deal_completed = True, задача уже удалена из очереди, выходим из обработки
                            # (не нужно делать continue, так как задача уже обработана и удалена)
                        elif approval_found and task_type == "refund":
                            # Для задач типа refund, если подтверждение найдено, но deal_completed не установлен (например, из-за ошибки удаления из очереди),
                            # все равно пропускаем проверку на ручной вывод, так как возврат уже выполнен
                            logger.info(f"✅ [АВТОВЫВОД] Подтверждение возврата депозита для сделки #{deal_id} получено, пропускаю проверку на ручной вывод")
                            # Пытаемся удалить задачу из очереди еще раз
                            try:
                                queue = await _wq_load()
                                await _wq_remove(deal_id, "refund")
                                logger.info(f"✅ [АВТОВЫВОД] Задача возврата депозита для сделки #{deal_id} удалена из очереди")
                            except Exception as e:
                                logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при повторном удалении задачи возврата из очереди: {e}")
                            # ВАЖНО: Для задач типа refund, если approval_found = True, задача уже обработана, выходим из обработки
                            # (не нужно делать continue, так как задача уже обработана)
                        else:
                                # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА: Проверяем, не завершена ли сделка в базе данных
                                # (на случай, если она была завершена в другом месте, например, в личном чате)
                                deal_already_completed = False
                                try:
                                    from database import get_deal, DealStatus
                                    current_deal = await get_deal(deal_id)
                                    if current_deal and current_deal.get('status') == DealStatus.RELEASED and current_deal.get('completed_at'):
                                        logger.info(f"✅ [АВТОВЫВОД] Сделка #{deal_id} уже завершена в базе данных (статус: RELEASED), пропускаю проверку на ручной вывод")
                                        deal_already_completed = True
                                        # Удаляем задачу из очереди, так как сделка уже завершена
                                        try:
                                            queue = await _wq_load()
                                            await _wq_remove(deal_id)
                                            logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (сделка уже завершена)")
                                        except Exception as e:
                                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                                except Exception as check_error:
                                    logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при проверке статуса сделки #{deal_id}: {check_error}")
                                
                                # Если сделка уже завершена, пропускаем отправку на ручной вывод
                                if deal_already_completed:
                                    # Задача уже обработана, переходим к следующей задаче
                                    break  # Выходим из обработки текущей задачи
                                
                                # КРИТИЧЕСКИ ВАЖНО: После 15 секунд ожидания ОБЯЗАТЕЛЬНО проверяем и отправляем на ручной вывод
                                # Это гарантирует, что если автовывод не сработал, система автоматически предложит ручной вывод
                                elapsed_time = time.time() - start_time
                                
                                # Проверяем approval_found ПОСЛЕ выхода из цикла while
                                # Это гарантирует, что мы всегда проверяем результат, даже если произошла ошибка
                                # ВАЖНО: Если автовывод не сработал в течение 10 секунд ИЛИ произошла ошибка - ОБЯЗАТЕЛЬНО отправляем на ручной вывод
                                # НО: Для задач типа refund, если approval_found = True, не отправляем на ручной вывод
                                # ВАЖНО: Для задач типа refund, если approval_found = True, не отправляем на ручной вывод
                                if task_type == "refund" and approval_found:
                                    logger.info(f"✅ [АВТОВЫВОД] Для задачи типа refund подтверждение найдено, пропускаю проверку на ручной вывод")
                                elif not approval_found:
                                    # ПРОВЕРКА: Если прошло 10 секунд ИЛИ обнаружена ошибка - ОБЯЗАТЕЛЬНО отправляем на ручной вывод
                                    should_send_manual = elapsed_time >= wait_duration or error_detected
                                    
                                    if should_send_manual:
                                        if error_detected:
                                            logger.error(f"❌ [АВТОВЫВОД] КРИТИЧЕСКИ ВАЖНО: Обнаружена ошибка при автовыводе для сделки #{deal_id} - ОБЯЗАТЕЛЬНО отправляю на ручной вывод!")
                                        else:
                                            logger.error(f"❌ [АВТОВЫВОД] КРИТИЧЕСКИ ВАЖНО: Автовывод не сработал в течение {wait_duration} секунд для сделки #{deal_id} - ОБЯЗАТЕЛЬНО отправляю на ручной вывод!")
                                        
                                        # Подтверждение не пришло или произошла ошибка - отправляем на ручной вывод
                                        logger.warning(f"⚠️ [АВТОВЫВОД] Подтверждение успешного вывода НЕ получено (время: {elapsed_time:.1f} сек, ошибка: {error_detected}) - отправляю на ручной вывод")
                                        
                                        try:
                                            admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                                            if admin_chat_id:
                                                try:
                                                    # Убираем пробелы и невидимые символы
                                                    admin_chat_id_clean = admin_chat_id.strip()
                                                    admin_chat_id_int = int(admin_chat_id_clean)
                                                    error_reason = "обнаружена ошибка" if error_detected else f"не получено подтверждение в течение {wait_duration} секунд"
                                                    await send_admin_notification(
                                                        deal_id=deal_id,
                                                        user_id=user_id,
                                                        wallet=wallet,
                                                        amount=amount,
                                                        task_type=task_type,
                                                        error_reason=f"Команда /withdraw была отправлена, но {error_reason}"
                                                    )
                                                except ValueError as ve:
                                                    logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}, ошибка: {ve}")
                                                except Exception as send_error:
                                                    logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке сообщения админам: {send_error}", exc_info=True)
                                            else:
                                                logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
                                        except Exception as e:
                                            logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления о ручном выводе: {e}", exc_info=True)
                                        
                                        # ВАЖНО: Удаляем задачу из очереди, чтобы НЕ обрабатывать ее повторно!
                                        # Задача отправлена на ручной вывод, повторная обработка не нужна
                                        try:
                                            # Читаем актуальную очередь из файла
                                            queue = await _wq_load()
                                            # Удаляем задачу
                                            await _wq_remove(deal_id)
                                            logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (требует ручного вывода)")
                                        except Exception as e:
                                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e}")
                        
                        # Сохраняем очередь ТОЛЬКО если задача была удалена (после успешного завершения или отправки на ручной вывод)
                        # Проверяем, была ли задача удалена (по deal_id)
                        try:
                            current_queue = await _wq_load()
                            task_removed = not any(t.get("deal_id") == deal_id for t in current_queue)
                            if task_removed:
                                logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} была удалена из очереди (осталось задач: {len(current_queue)})")
                        except Exception as e:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при проверке очереди: {e}")
                        
                    except Exception as e:
                        logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке команды вывода: {e}", exc_info=True)
                        # При любой ошибке отправляем на ручной вывод
                        logger.warning(f"⚠️ [АВТОВЫВОД] Отправляю задачу на ручной вывод из-за ошибки: {e}")
                        try:
                            admin_chat_id = os.getenv("ADMIN_CHAT_ID")
                            if admin_chat_id:
                                try:
                                    admin_chat_id_int = int(admin_chat_id)
                                    await send_admin_notification(
                                        deal_id=deal_id,
                                        user_id=user_id,
                                        wallet=wallet,
                                        amount=amount,
                                        task_type=task_type,
                                        error_reason=f"Ошибка при автовыводе: {str(e)[:200]}"
                                    )
                                except ValueError:
                                    logger.error(f"❌ [АВТОВЫВОД] Неверный формат ADMIN_CHAT_ID: {admin_chat_id}")
                            else:
                                logger.warning(f"⚠️ [АВТОВЫВОД] ADMIN_CHAT_ID не указан в .env, не могу отправить уведомление")
                        except Exception as e2:
                            logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке уведомления о ручном выводе: {e2}", exc_info=True)
                        
                        # Удаляем задачу из очереди, чтобы не обрабатывать ее повторно
                        try:
                            # Читаем актуальную очередь из файла
                            queue = await _wq_load()
                            # Удаляем задачу
                            await _wq_remove(deal_id)
                            logger.info(f"✅ [АВТОВЫВОД] Задача для сделки #{deal_id} удалена из очереди (требует ручного вывода из-за ошибки)")
                        except Exception as e3:
                            logger.warning(f"⚠️ [АВТОВЫВОД] Ошибка при удалении задачи из очереди: {e3}")
                else:
                    logger.warning(f"⚠️ [АВТОВЫВОД] Неверные данные в задаче: {task}")
                    # Удаляем неверную задачу
                    try:
                        await _wq_remove(deal_id)
                        logger.info(f"✅ [АВТОВЫВОД] Неверная задача удалена из очереди")
                    except:
                        pass
            
            # Проверяем очередь каждую секунду
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"❌ [АВТОВЫВОД] Ошибка в обработке очереди: {e}", exc_info=True)
            await asyncio.sleep(1)


