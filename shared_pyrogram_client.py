"""
Общий модуль для переиспользования Pyrogram клиента между forwarder и auto_withdraw
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from pyrogram import Client

BASE_DIR = Path(__file__).parent.absolute()

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(BASE_DIR / 'shared_client.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Загружаем .env
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH")
SESSION_NAME = os.getenv("TG_SESSION", "forwarder_session")

if not API_ID or not API_HASH:
    raise SystemExit("Please set TG_API_ID, TG_API_HASH in .env")

# Глобальный клиент, который будет использоваться и forwarder, и auto_withdraw
_shared_client = None
_client_lock = None

def get_shared_client():
    """Получить общий Pyrogram клиент"""
    global _shared_client, _client_lock
    
    if _shared_client is None:
        logger.info(f"🔌 Создаю общий Pyrogram клиент, сессия: {SESSION_NAME}")
        _shared_client = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=str(BASE_DIR)
        )
        # Импортируем Lock только когда нужно
        import asyncio
        _client_lock = asyncio.Lock()
    
    return _shared_client, _client_lock

async def ensure_client_started():
    """Убедиться, что клиент запущен"""
    client, lock = get_shared_client()
    
    if client.is_connected:
        logger.info(f"✅ Общий Pyrogram клиент уже запущен")
        return client
    
    # Если клиент не запущен, пробуем запустить с повторными попытками
    # (forwarder может использовать его в другом процессе)
    max_retries = 10
    for attempt in range(max_retries):
        try:
            async with lock:
                # Двойная проверка после получения блокировки
                if client.is_connected:
                    return client
                
                logger.info(f"🔌 Запускаю общий Pyrogram клиент (попытка {attempt + 1}/{max_retries})...")
                await client.start()
                logger.info(f"✅ Общий Pyrogram клиент запущен")
                return client
        except Exception as e:
            error_msg = str(e)
            if "database is locked" in error_msg.lower() and attempt < max_retries - 1:
                wait_time = (attempt + 1) * 0.5
                logger.warning(f"⚠️ База данных заблокирована (forwarder использует клиент), жду {wait_time} сек...")
                import asyncio
                await asyncio.sleep(wait_time)
                continue
            else:
                # Если клиент уже запущен в другом процессе, просто используем его
                if client.is_connected:
                    return client
                raise
    
    # Если не удалось запустить, но клиент уже подключен (запущен в другом процессе)
    if client.is_connected:
        return client
    
    raise ConnectionError("Не удалось запустить клиент после всех попыток")


"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from pyrogram import Client

BASE_DIR = Path(__file__).parent.absolute()

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(BASE_DIR / 'shared_client.log'), encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Загружаем .env
env_path = BASE_DIR / '.env'
load_dotenv(dotenv_path=env_path)

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH")
SESSION_NAME = os.getenv("TG_SESSION", "forwarder_session")

if not API_ID or not API_HASH:
    raise SystemExit("Please set TG_API_ID, TG_API_HASH in .env")

# Глобальный клиент, который будет использоваться и forwarder, и auto_withdraw
_shared_client = None
_client_lock = None

def get_shared_client():
    """Получить общий Pyrogram клиент"""
    global _shared_client, _client_lock
    
    if _shared_client is None:
        logger.info(f"🔌 Создаю общий Pyrogram клиент, сессия: {SESSION_NAME}")
        _shared_client = Client(
            SESSION_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            workdir=str(BASE_DIR)
        )
        # Импортируем Lock только когда нужно
        import asyncio
        _client_lock = asyncio.Lock()
    
    return _shared_client, _client_lock

async def ensure_client_started():
    """Убедиться, что клиент запущен"""
    client, lock = get_shared_client()
    
    if client.is_connected:
        logger.info(f"✅ Общий Pyrogram клиент уже запущен")
        return client
    
    # Если клиент не запущен, пробуем запустить с повторными попытками
    # (forwarder может использовать его в другом процессе)
    max_retries = 10
    for attempt in range(max_retries):
        try:
            async with lock:
                # Двойная проверка после получения блокировки
                if client.is_connected:
                    return client
                
                logger.info(f"🔌 Запускаю общий Pyrogram клиент (попытка {attempt + 1}/{max_retries})...")
                await client.start()
                logger.info(f"✅ Общий Pyrogram клиент запущен")
                return client
        except Exception as e:
            error_msg = str(e)
            if "database is locked" in error_msg.lower() and attempt < max_retries - 1:
                wait_time = (attempt + 1) * 0.5
                logger.warning(f"⚠️ База данных заблокирована (forwarder использует клиент), жду {wait_time} сек...")
                import asyncio
                await asyncio.sleep(wait_time)
                continue
            else:
                # Если клиент уже запущен в другом процессе, просто используем его
                if client.is_connected:
                    return client
                raise
    
    # Если не удалось запустить, но клиент уже подключен (запущен в другом процессе)
    if client.is_connected:
        return client
    
    raise ConnectionError("Не удалось запустить клиент после всех попыток")

