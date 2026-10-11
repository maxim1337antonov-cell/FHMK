"""
Тестовый скрипт для проверки отправки команды /withdraw в бота
"""
import asyncio
from decimal import Decimal
from auto_withdraw import send_withdraw_command, init_withdraw_client

async def test():
    print("Testing withdraw command...")
    
    # Инициализируем клиент
    try:
        print("Initializing client...")
        await init_withdraw_client()
        print("Client initialized")
    except Exception as e:
        print(f"Error initializing client: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Тестируем отправку команды
    print("\nSending test /withdraw command...")
    result = await send_withdraw_command(
        wallet="bc1qtest1234567890abcdefghijklmnopqrstuvwxyz",
        amount_btc=Decimal("0.00001"),
        deal_id=999,
        user_id=123456
    )
    
    print(f"\nResult: {result}")
    
    if result["success"]:
        print("Test passed! Command sent successfully.")
    else:
        print(f"Test failed: {result.get('message', 'Unknown error')}")

if __name__ == "__main__":
    asyncio.run(test())


"""
import asyncio
from decimal import Decimal
from auto_withdraw import send_withdraw_command, init_withdraw_client

async def test():
    print("Testing withdraw command...")
    
    # Инициализируем клиент
    try:
        print("Initializing client...")
        await init_withdraw_client()
        print("Client initialized")
    except Exception as e:
        print(f"Error initializing client: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Тестируем отправку команды
    print("\nSending test /withdraw command...")
    result = await send_withdraw_command(
        wallet="bc1qtest1234567890abcdefghijklmnopqrstuvwxyz",
        amount_btc=Decimal("0.00001"),
        deal_id=999,
        user_id=123456
    )
    
    print(f"\nResult: {result}")
    
    if result["success"]:
        print("Test passed! Command sent successfully.")
    else:
        print(f"Test failed: {result.get('message', 'Unknown error')}")

if __name__ == "__main__":
    asyncio.run(test())

