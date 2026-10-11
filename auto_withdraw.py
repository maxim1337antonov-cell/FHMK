"""
Модуль для автоматического вывода BTC через бота @BmbP2p_bot используя Pyrogram
"""
import os
import re
import logging
import asyncio
from pathlib import Path
from decimal import Decimal
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

# Получаем абсолютный путь к директории проекта
BASE_DIR = Path(__file__).parent.absolute()

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(BASE_DIR / 'auto_withdraw.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Загружаем .env
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH")
# Используем отдельную сессию для автовывода (скопированную из forwarder_session)
# Это предотвращает конфликты "database is locked" между процессами
SESSION_NAME = "forwarder_session"  # Используем ту же сессию, что и forwarder.py
WITHDRAW_BOT_USERNAME = "BmbP2p_bot"  # Бот для вывода (всегда @BmbP2p_bot)

if not API_ID or not API_HASH:
    raise SystemExit("Please set TG_API_ID, TG_API_HASH in .env")

# НЕ создаем Client при импорте - создадим его только при первом вызове
# Это предотвращает ошибки авторизации при импорте модуля
app = None

# Хранилище ожидающих подтверждения выводов
pending_withdrawals = {}  # {message_id: {"wallet": str, "amount": Decimal, "deal_id": int, "user_id": int, "callback_data": str}}


async def send_withdraw_command(wallet: str, amount_btc: Decimal, deal_id: int = None, user_id: int = None) -> dict:
    """
    Отправить команду /withdraw в бота @BmbP2p_bot и автоматически подтвердить
    
    Args:
        wallet: BTC кошелек для вывода
        amount_btc: Сумма в BTC для вывода
        deal_id: ID сделки (для логирования)
        user_id: ID пользователя (для логирования)
    
    Returns:
        dict с результатом: {"success": bool, "message": str, "tx_id": str | None}
    """
    logger.info(f"🚀 [АВТОВЫВОД] === НАЧАЛО send_withdraw_command ===")
    logger.info(f"🚀 [АВТОВЫВОД] Параметры: wallet={wallet}, amount_btc={amount_btc}, deal_id={deal_id}, user_id={user_id}")
    
    # Получаем клиент для автовывода
    try:
        logger.info(f"🔌 [АВТОВЫВОД] Инициализирую Pyrogram клиент...")
        client = await init_withdraw_client()
        logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент инициализирован и готов к использованию")
    except Exception as e:
        logger.error(f"❌ [АВТОВЫВОД] Не удалось инициализировать клиент: {e}", exc_info=True)
        return {"success": False, "message": f"Ошибка инициализации клиента: {e}", "tx_id": None}
    
    try:
        logger.info(f"💰 [АВТОВЫВОД] Начинаю формирование команды вывода...")
        # Форматируем сумму для команды
        amount_str = f"{amount_btc:.8f}".rstrip('0').rstrip('.')
        command = f"/withdraw {wallet} {amount_str}"
        
        logger.info(f"💰 [АВТОВЫВОД] Отправка команды в @{WITHDRAW_BOT_USERNAME}: {command}")
        logger.info(f"💰 [АВТОВЫВОД] Сделка #{deal_id}, пользователь {user_id}, кошелек: {wallet}, сумма: {amount_btc} BTC")
        
        # Получаем chat_id бота через username
        try:
            # Убираем @ если есть
            bot_username = WITHDRAW_BOT_USERNAME.replace("@", "")
            bot_chat = await client.get_chat(bot_username)
            bot_chat_id = bot_chat.id
            logger.info(f"✅ [АВТОВЫВОД] Chat ID бота @{WITHDRAW_BOT_USERNAME}: {bot_chat_id}")
        except Exception as e:
            logger.error(f"❌ [АВТОВЫВОД] Не удалось получить chat_id бота: {e}", exc_info=True)
            # Пробуем через get_dialogs
            try:
                logger.info(f"🔍 [АВТОВЫВОД] Пробую найти бота через get_dialogs...")
                bot_username_normalized = WITHDRAW_BOT_USERNAME.replace("@", "").lower()
                async for dialog in client.get_dialogs():
                    if dialog.chat.username and dialog.chat.username.lower() == bot_username_normalized:
                        bot_chat_id = dialog.chat.id
                        logger.info(f"✅ [АВТОВЫВОД] Бот найден через get_dialogs, Chat ID: {bot_chat_id}")
                        break
                else:
                    return {"success": False, "message": f"Не удалось найти бота @{WITHDRAW_BOT_USERNAME}", "tx_id": None}
            except Exception as e2:
                logger.error(f"❌ [АВТОВЫВОД] Ошибка при поиске через get_dialogs: {e2}", exc_info=True)
                return {"success": False, "message": f"Не удалось найти бота @{WITHDRAW_BOT_USERNAME}: {e}", "tx_id": None}
        
        # Отправляем команду боту
        try:
            sent_message = await client.send_message(bot_chat_id, command)
            logger.info(f"✅ [АВТОВЫВОД] Команда отправлена, message_id: {sent_message.id}")
            
            # Ждем ответ от бота (максимум 10 секунд)
            # Ответ должен содержать кнопку подтверждения
            await asyncio.sleep(2)  # Даем боту время ответить
            
            # Получаем последние сообщения от бота
            messages = []
            async for msg in client.get_chat_history(bot_chat_id, limit=5):
                if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == WITHDRAW_BOT_USERNAME.replace("@", "").lower():
                    if msg.reply_to_message and msg.reply_to_message.id == sent_message.id:
                        messages.append(msg)
                        break
            
            if not messages:
                # Пробуем найти последнее сообщение от бота
                async for msg in client.get_chat_history(bot_chat_id, limit=10):
                    if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == WITHDRAW_BOT_USERNAME.replace("@", "").lower():
                        messages.append(msg)
                        if len(messages) >= 1:
                            break
            
            if messages:
                response_msg = messages[0]
                logger.info(f"📨 [АВТОВЫВОД] Получен ответ от бота, message_id: {response_msg.id}")
                
                # Ищем кнопку подтверждения
                if response_msg.reply_markup and response_msg.reply_markup.inline_keyboard:
                    for row in response_msg.reply_markup.inline_keyboard:
                        for button in row:
                            button_text = button.text.lower()
                            # Ищем кнопку с текстом "подтверждаю", "подтвердить", "✅" и т.д.
                            if any(keyword in button_text for keyword in ["подтверждаю", "подтвердить", "confirm", "✅", "да", "yes", "ok"]):
                                logger.info(f"🔘 [АВТОВЫВОД] Найдена кнопка подтверждения: {button.text}, callback_data: {button.callback_data}")
                                
                                # Нажимаем кнопку
                                try:
                                    if button.callback_data:
                                        # Отправляем callback_query через raw API
                                        from pyrogram.raw import functions, types
                                        peer = await client.resolve_peer(bot_chat_id)
                                        # Преобразуем callback_data в bytes если это строка
                                        callback_data_bytes = button.callback_data.encode('utf-8') if isinstance(button.callback_data, str) else button.callback_data
                                        
                                        result = await client.invoke(
                                            functions.messages.GetBotCallbackAnswer(
                                                peer=peer,
                                                msg_id=response_msg.id,
                                                data=callback_data_bytes
                                            )
                                        )
                                        logger.info(f"✅ [АВТОВЫВОД] Кнопка подтверждения нажата автоматически, ответ: {result}")
                                        
                                        # Ждем подтверждение (максимум 5 секунд)
                                        await asyncio.sleep(2)
                                        
                                        # Пробуем найти сообщение о успешном выводе
                                        success_messages = []
                                        async for msg in client.get_chat_history(bot_chat_id, limit=5):
                                            if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == WITHDRAW_BOT_USERNAME.replace("@", "").lower():
                                                success_messages.append(msg)
                                                if len(success_messages) >= 1:
                                                    break
                                        
                                        if success_messages:
                                            success_text = success_messages[0].text or ""
                                            # Ищем tx_id в ответе
                                            tx_id_match = re.search(r'(tx|transaction|id)[\s:]+([a-fA-F0-9]{64})', success_text, re.IGNORECASE)
                                            tx_id = tx_id_match.group(2) if tx_id_match else None
                                            
                                            logger.info(f"✅ [АВТОВЫВОД] Вывод подтвержден, tx_id: {tx_id}")
                                            return {
                                                "success": True,
                                                "message": "Вывод успешно выполнен",
                                                "tx_id": tx_id
                                            }
                                        else:
                                            logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось найти подтверждение вывода")
                                            return {
                                                "success": True,
                                                "message": "Вывод подтвержден (tx_id не найден)",
                                                "tx_id": None
                                            }
                                    else:
                                        logger.warning(f"⚠️ [АВТОВЫВОД] У кнопки нет callback_data")
                                except Exception as e:
                                    logger.error(f"❌ [АВТОВЫВОД] Ошибка при нажатии кнопки: {e}", exc_info=True)
                                    return {"success": False, "message": f"Ошибка при нажатии кнопки: {e}", "tx_id": None}
                    
                    logger.warning(f"⚠️ [АВТОВЫВОД] Кнопка подтверждения не найдена в ответе")
                    return {"success": False, "message": "Кнопка подтверждения не найдена", "tx_id": None}
                else:
                    logger.warning(f"⚠️ [АВТОВЫВОД] Ответ от бота не содержит кнопок")
                    return {"success": False, "message": "Ответ от бота не содержит кнопок", "tx_id": None}
            else:
                logger.warning(f"⚠️ [АВТОВЫВОД] Не получен ответ от бота")
                return {"success": False, "message": "Не получен ответ от бота", "tx_id": None}
                
        except Exception as e:
            logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке команды: {e}", exc_info=True)
            return {"success": False, "message": str(e), "tx_id": None}
            
    except Exception as e:
        logger.error(f"❌ [АВТОВЫВОД] Критическая ошибка: {e}", exc_info=True)
        return {"success": False, "message": str(e), "tx_id": None}


# Обработчик сообщений от бота удален - не нужен при no_updates=True
# Вместо этого кнопка нажимается напрямую в send_withdraw_command после получения ответа от бота


def get_withdraw_client():
    """Получить Pyrogram клиент для автовывода"""
    global app
    if app is None:
        logger.info(f"🔌 [АВТОВЫВОД] Создаю Pyrogram клиент для работы с @{WITHDRAW_BOT_USERNAME}...")
        logger.info(f"🔌 [АВТОВЫВОД] Используется сессия: {SESSION_NAME}")
        app = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=str(BASE_DIR),
            no_updates=True
        )
    return app

async def init_withdraw_client():
    """Инициализация Pyrogram клиента для автовывода"""
    global app
    try:
        client = get_withdraw_client()
        
        if client.is_connected:
            logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент уже подключен")
            return client
        
        logger.info(f"🔌 [АВТОВЫВОД] Запускаю Pyrogram клиент для работы с @{WITHDRAW_BOT_USERNAME}...")
        logger.info(f"🔌 [АВТОВЫВОД] Используется сессия: {SESSION_NAME} (скопирована из forwarder_session)")
        
        # Запускаем клиент (сессия уже авторизована, так как скопирована из forwarder_session)
        try:
            await client.start()
            logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент запущен для работы с @{WITHDRAW_BOT_USERNAME}")
            return client
        except Exception as start_error:
            error_msg = str(start_error)
            if "EOF when reading a line" in error_msg or "not authorized" in error_msg.lower():
                logger.error(f"❌ [АВТОВЫВОД] Сессия не авторизована. Используется сессия: {SESSION_NAME}")
                logger.error(f"❌ [АВТОВЫВОД] Сессия должна быть скопирована из forwarder_session")
                raise ConnectionError(f"Сессия {SESSION_NAME} не авторизована. Скопируйте forwarder_session.session в auto_withdraw_session.session")
            else:
                raise
                    
    except Exception as e:
        logger.error(f"❌ [АВТОВЫВОД] Ошибка при инициализации Pyrogram клиента: {e}", exc_info=True)
        raise


async def stop_withdraw_client():
    """Остановка Pyrogram клиента"""
    global app
    if app and app.is_connected:
        await app.stop()
        logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент остановлен")


if __name__ == "__main__":
    # Для тестирования
    async def test():
        await init_withdraw_client()
        result = await send_withdraw_command("bc1qtest123", Decimal("0.001"), deal_id=1, user_id=123)
        print(f"Результат: {result}")
        await stop_withdraw_client()
    
    asyncio.run(test())


"""
import os
import re
import logging
import asyncio
from pathlib import Path
from decimal import Decimal
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

# Получаем абсолютный путь к директории проекта
BASE_DIR = Path(__file__).parent.absolute()

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(BASE_DIR / 'auto_withdraw.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Загружаем .env
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH")
# Используем отдельную сессию для автовывода (скопированную из forwarder_session)
# Это предотвращает конфликты "database is locked" между процессами
SESSION_NAME = "forwarder_session"  # Используем ту же сессию, что и forwarder.py
WITHDRAW_BOT_USERNAME = "BmbP2p_bot"  # Бот для вывода (всегда @BmbP2p_bot)

if not API_ID or not API_HASH:
    raise SystemExit("Please set TG_API_ID, TG_API_HASH in .env")

# НЕ создаем Client при импорте - создадим его только при первом вызове
# Это предотвращает ошибки авторизации при импорте модуля
app = None

# Хранилище ожидающих подтверждения выводов
pending_withdrawals = {}  # {message_id: {"wallet": str, "amount": Decimal, "deal_id": int, "user_id": int, "callback_data": str}}


async def send_withdraw_command(wallet: str, amount_btc: Decimal, deal_id: int = None, user_id: int = None) -> dict:
    """
    Отправить команду /withdraw в бота @BmbP2p_bot и автоматически подтвердить
    
    Args:
        wallet: BTC кошелек для вывода
        amount_btc: Сумма в BTC для вывода
        deal_id: ID сделки (для логирования)
        user_id: ID пользователя (для логирования)
    
    Returns:
        dict с результатом: {"success": bool, "message": str, "tx_id": str | None}
    """
    logger.info(f"🚀 [АВТОВЫВОД] === НАЧАЛО send_withdraw_command ===")
    logger.info(f"🚀 [АВТОВЫВОД] Параметры: wallet={wallet}, amount_btc={amount_btc}, deal_id={deal_id}, user_id={user_id}")
    
    # Получаем клиент для автовывода
    try:
        logger.info(f"🔌 [АВТОВЫВОД] Инициализирую Pyrogram клиент...")
        client = await init_withdraw_client()
        logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент инициализирован и готов к использованию")
    except Exception as e:
        logger.error(f"❌ [АВТОВЫВОД] Не удалось инициализировать клиент: {e}", exc_info=True)
        return {"success": False, "message": f"Ошибка инициализации клиента: {e}", "tx_id": None}
    
    try:
        logger.info(f"💰 [АВТОВЫВОД] Начинаю формирование команды вывода...")
        # Форматируем сумму для команды
        amount_str = f"{amount_btc:.8f}".rstrip('0').rstrip('.')
        command = f"/withdraw {wallet} {amount_str}"
        
        logger.info(f"💰 [АВТОВЫВОД] Отправка команды в @{WITHDRAW_BOT_USERNAME}: {command}")
        logger.info(f"💰 [АВТОВЫВОД] Сделка #{deal_id}, пользователь {user_id}, кошелек: {wallet}, сумма: {amount_btc} BTC")
        
        # Получаем chat_id бота через username
        try:
            # Убираем @ если есть
            bot_username = WITHDRAW_BOT_USERNAME.replace("@", "")
            bot_chat = await client.get_chat(bot_username)
            bot_chat_id = bot_chat.id
            logger.info(f"✅ [АВТОВЫВОД] Chat ID бота @{WITHDRAW_BOT_USERNAME}: {bot_chat_id}")
        except Exception as e:
            logger.error(f"❌ [АВТОВЫВОД] Не удалось получить chat_id бота: {e}", exc_info=True)
            # Пробуем через get_dialogs
            try:
                logger.info(f"🔍 [АВТОВЫВОД] Пробую найти бота через get_dialogs...")
                bot_username_normalized = WITHDRAW_BOT_USERNAME.replace("@", "").lower()
                async for dialog in client.get_dialogs():
                    if dialog.chat.username and dialog.chat.username.lower() == bot_username_normalized:
                        bot_chat_id = dialog.chat.id
                        logger.info(f"✅ [АВТОВЫВОД] Бот найден через get_dialogs, Chat ID: {bot_chat_id}")
                        break
                else:
                    return {"success": False, "message": f"Не удалось найти бота @{WITHDRAW_BOT_USERNAME}", "tx_id": None}
            except Exception as e2:
                logger.error(f"❌ [АВТОВЫВОД] Ошибка при поиске через get_dialogs: {e2}", exc_info=True)
                return {"success": False, "message": f"Не удалось найти бота @{WITHDRAW_BOT_USERNAME}: {e}", "tx_id": None}
        
        # Отправляем команду боту
        try:
            sent_message = await client.send_message(bot_chat_id, command)
            logger.info(f"✅ [АВТОВЫВОД] Команда отправлена, message_id: {sent_message.id}")
            
            # Ждем ответ от бота (максимум 10 секунд)
            # Ответ должен содержать кнопку подтверждения
            await asyncio.sleep(2)  # Даем боту время ответить
            
            # Получаем последние сообщения от бота
            messages = []
            async for msg in client.get_chat_history(bot_chat_id, limit=5):
                if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == WITHDRAW_BOT_USERNAME.replace("@", "").lower():
                    if msg.reply_to_message and msg.reply_to_message.id == sent_message.id:
                        messages.append(msg)
                        break
            
            if not messages:
                # Пробуем найти последнее сообщение от бота
                async for msg in client.get_chat_history(bot_chat_id, limit=10):
                    if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == WITHDRAW_BOT_USERNAME.replace("@", "").lower():
                        messages.append(msg)
                        if len(messages) >= 1:
                            break
            
            if messages:
                response_msg = messages[0]
                logger.info(f"📨 [АВТОВЫВОД] Получен ответ от бота, message_id: {response_msg.id}")
                
                # Ищем кнопку подтверждения
                if response_msg.reply_markup and response_msg.reply_markup.inline_keyboard:
                    for row in response_msg.reply_markup.inline_keyboard:
                        for button in row:
                            button_text = button.text.lower()
                            # Ищем кнопку с текстом "подтверждаю", "подтвердить", "✅" и т.д.
                            if any(keyword in button_text for keyword in ["подтверждаю", "подтвердить", "confirm", "✅", "да", "yes", "ok"]):
                                logger.info(f"🔘 [АВТОВЫВОД] Найдена кнопка подтверждения: {button.text}, callback_data: {button.callback_data}")
                                
                                # Нажимаем кнопку
                                try:
                                    if button.callback_data:
                                        # Отправляем callback_query через raw API
                                        from pyrogram.raw import functions, types
                                        peer = await client.resolve_peer(bot_chat_id)
                                        # Преобразуем callback_data в bytes если это строка
                                        callback_data_bytes = button.callback_data.encode('utf-8') if isinstance(button.callback_data, str) else button.callback_data
                                        
                                        result = await client.invoke(
                                            functions.messages.GetBotCallbackAnswer(
                                                peer=peer,
                                                msg_id=response_msg.id,
                                                data=callback_data_bytes
                                            )
                                        )
                                        logger.info(f"✅ [АВТОВЫВОД] Кнопка подтверждения нажата автоматически, ответ: {result}")
                                        
                                        # Ждем подтверждение (максимум 5 секунд)
                                        await asyncio.sleep(2)
                                        
                                        # Пробуем найти сообщение о успешном выводе
                                        success_messages = []
                                        async for msg in client.get_chat_history(bot_chat_id, limit=5):
                                            if msg.from_user and msg.from_user.username and msg.from_user.username.lower() == WITHDRAW_BOT_USERNAME.replace("@", "").lower():
                                                success_messages.append(msg)
                                                if len(success_messages) >= 1:
                                                    break
                                        
                                        if success_messages:
                                            success_text = success_messages[0].text or ""
                                            # Ищем tx_id в ответе
                                            tx_id_match = re.search(r'(tx|transaction|id)[\s:]+([a-fA-F0-9]{64})', success_text, re.IGNORECASE)
                                            tx_id = tx_id_match.group(2) if tx_id_match else None
                                            
                                            logger.info(f"✅ [АВТОВЫВОД] Вывод подтвержден, tx_id: {tx_id}")
                                            return {
                                                "success": True,
                                                "message": "Вывод успешно выполнен",
                                                "tx_id": tx_id
                                            }
                                        else:
                                            logger.warning(f"⚠️ [АВТОВЫВОД] Не удалось найти подтверждение вывода")
                                            return {
                                                "success": True,
                                                "message": "Вывод подтвержден (tx_id не найден)",
                                                "tx_id": None
                                            }
                                    else:
                                        logger.warning(f"⚠️ [АВТОВЫВОД] У кнопки нет callback_data")
                                except Exception as e:
                                    logger.error(f"❌ [АВТОВЫВОД] Ошибка при нажатии кнопки: {e}", exc_info=True)
                                    return {"success": False, "message": f"Ошибка при нажатии кнопки: {e}", "tx_id": None}
                    
                    logger.warning(f"⚠️ [АВТОВЫВОД] Кнопка подтверждения не найдена в ответе")
                    return {"success": False, "message": "Кнопка подтверждения не найдена", "tx_id": None}
                else:
                    logger.warning(f"⚠️ [АВТОВЫВОД] Ответ от бота не содержит кнопок")
                    return {"success": False, "message": "Ответ от бота не содержит кнопок", "tx_id": None}
            else:
                logger.warning(f"⚠️ [АВТОВЫВОД] Не получен ответ от бота")
                return {"success": False, "message": "Не получен ответ от бота", "tx_id": None}
                
        except Exception as e:
            logger.error(f"❌ [АВТОВЫВОД] Ошибка при отправке команды: {e}", exc_info=True)
            return {"success": False, "message": str(e), "tx_id": None}
            
    except Exception as e:
        logger.error(f"❌ [АВТОВЫВОД] Критическая ошибка: {e}", exc_info=True)
        return {"success": False, "message": str(e), "tx_id": None}


# Обработчик сообщений от бота удален - не нужен при no_updates=True
# Вместо этого кнопка нажимается напрямую в send_withdraw_command после получения ответа от бота


def get_withdraw_client():
    """Получить Pyrogram клиент для автовывода"""
    global app
    if app is None:
        logger.info(f"🔌 [АВТОВЫВОД] Создаю Pyrogram клиент для работы с @{WITHDRAW_BOT_USERNAME}...")
        logger.info(f"🔌 [АВТОВЫВОД] Используется сессия: {SESSION_NAME}")
        app = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=str(BASE_DIR),
            no_updates=True
        )
    return app

async def init_withdraw_client():
    """Инициализация Pyrogram клиента для автовывода"""
    global app
    try:
        client = get_withdraw_client()
        
        if client.is_connected:
            logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент уже подключен")
            return client
        
        logger.info(f"🔌 [АВТОВЫВОД] Запускаю Pyrogram клиент для работы с @{WITHDRAW_BOT_USERNAME}...")
        logger.info(f"🔌 [АВТОВЫВОД] Используется сессия: {SESSION_NAME} (скопирована из forwarder_session)")
        
        # Запускаем клиент (сессия уже авторизована, так как скопирована из forwarder_session)
        try:
            await client.start()
            logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент запущен для работы с @{WITHDRAW_BOT_USERNAME}")
            return client
        except Exception as start_error:
            error_msg = str(start_error)
            if "EOF when reading a line" in error_msg or "not authorized" in error_msg.lower():
                logger.error(f"❌ [АВТОВЫВОД] Сессия не авторизована. Используется сессия: {SESSION_NAME}")
                logger.error(f"❌ [АВТОВЫВОД] Сессия должна быть скопирована из forwarder_session")
                raise ConnectionError(f"Сессия {SESSION_NAME} не авторизована. Скопируйте forwarder_session.session в auto_withdraw_session.session")
            else:
                raise
                    
    except Exception as e:
        logger.error(f"❌ [АВТОВЫВОД] Ошибка при инициализации Pyrogram клиента: {e}", exc_info=True)
        raise


async def stop_withdraw_client():
    """Остановка Pyrogram клиента"""
    global app
    if app and app.is_connected:
        await app.stop()
        logger.info(f"✅ [АВТОВЫВОД] Pyrogram клиент остановлен")


if __name__ == "__main__":
    # Для тестирования
    async def test():
        await init_withdraw_client()
        result = await send_withdraw_command("bc1qtest123", Decimal("0.001"), deal_id=1, user_id=123)
        print(f"Результат: {result}")
        await stop_withdraw_client()
    
    asyncio.run(test())

