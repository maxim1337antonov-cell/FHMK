"""
Скрипт для получения правильного ID чата/канала
"""
import asyncio
import os
from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH")
SESSION_NAME = "get_chat_id_session"  # Отдельная сессия, чтобы не конфликтовать с forwarder.py

if not API_ID or not API_HASH:
    print("❌ Ошибка: TG_API_ID и TG_API_HASH должны быть указаны в .env")
    exit(1)

app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)


async def main():
    await app.start()
    
    print("\n" + "="*60)
    print("  СПИСОК ДОСТУПНЫХ ЧАТОВ/КАНАЛОВ")
    print("="*60 + "\n")
    
    try:
        # Получаем все диалоги
        async for dialog in app.get_dialogs():
            chat = dialog.chat
            
            # Определяем тип чата
            if chat.type.name == "CHANNEL":
                chat_type = "📢 КАНАЛ"
                # Для каналов используем формат -100...
                if chat.id < 0:
                    # Преобразуем ID канала в правильный формат
                    # ID канала в формате -100... получается так:
                    # Если ID = -1234567890, то правильный формат = -1001234567890
                    # Но если уже в формате -100..., то оставляем как есть
                    if str(chat.id).startswith("-100"):
                        correct_id = chat.id
                    else:
                        # Преобразуем обычный ID в формат -100...
                        # Убираем минус, добавляем -100
                        correct_id = int(f"-100{abs(chat.id)}")
                else:
                    correct_id = chat.id
            elif chat.type.name == "GROUP":
                chat_type = "👥 ГРУППА"
                correct_id = chat.id
            elif chat.type.name == "SUPERGROUP":
                chat_type = "👥 СУПЕРГРУППА"
                # Для супергрупп тоже может понадобиться формат -100...
                if chat.id < 0 and not str(chat.id).startswith("-100"):
                    correct_id = int(f"-100{abs(chat.id)}")
                else:
                    correct_id = chat.id
            else:
                chat_type = "💬 ЛИЧНЫЙ"
                correct_id = chat.id
            
            # Выводим информацию
            title = chat.title or chat.first_name or "Без названия"
            username = f"@{chat.username}" if chat.username else "нет username"
            
            print(f"{chat_type}: {title}")
            print(f"   Username: {username}")
            print(f"   ID (текущий): {chat.id}")
            print(f"   ID (для .env): {correct_id}")
            print()
        
        print("="*60)
        print("\n💡 ИНСТРУКЦИЯ:")
        print("1. Найдите нужный чат/канал в списке выше")
        print("2. Скопируйте 'ID (для .env)' и вставьте в .env файл")
        print("3. Для каналов используйте формат: -1001234567890")
        print("4. Убедитесь, что ваш аккаунт добавлен в этот чат/канал")
        print("\n")
        
    except Exception as e:
        print(f"❌ Ошибка при получении списка чатов: {e}")
        print("\n💡 Убедитесь, что:")
        print("1. Вы правильно указали TG_API_ID и TG_API_HASH в .env")
        print("2. Вы авторизовались в forwarder.py (запустите его сначала)")
    
    await app.stop()


if __name__ == "__main__":
    app.run(main())
