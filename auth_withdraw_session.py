"""
Скрипт для авторизации сессии auto_withdraw
Запустите этот скрипт один раз для авторизации сессии
"""
import asyncio
from auto_withdraw import get_withdraw_client

async def main():
    print("Avtorizaciya sessii dlya avtovyvoda...")
    print("Vvedite nomer telefona ili token bota:")
    client = get_withdraw_client()
    await client.start()
    print("Sessiya uspeshno avtorizovana!")

if __name__ == "__main__":
    asyncio.run(main())


Запустите этот скрипт один раз для авторизации сессии
"""
import asyncio
from auto_withdraw import get_withdraw_client

async def main():
    print("Avtorizaciya sessii dlya avtovyvoda...")
    print("Vvedite nomer telefona ili token bota:")
    client = get_withdraw_client()
    await client.start()
    print("Sessiya uspeshno avtorizovana!")

if __name__ == "__main__":
    asyncio.run(main())

