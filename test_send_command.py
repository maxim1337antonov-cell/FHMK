"""
Тест отправки команды /withdraw в бота
"""
import asyncio
from decimal import Decimal
from auto_withdraw import send_withdraw_command, init_withdraw_client, stop_withdraw_client

async def test():
    print("=" * 60)
    print("TEST: Sending /withdraw command to bot")
    print("=" * 60)
    
    try:
        # Инициализируем клиент
        print("\n[1/4] Initializing client...")
        client = await init_withdraw_client()
        print(f"      Client initialized: {client.is_connected}")
        
        # Тестируем отправку команды
        print("\n[2/4] Sending /withdraw command...")
        print("      Wallet: bc1qtest1234567890abcdefghijklmnopqrstuvwxyz")
        print("      Amount: 0.00001 BTC")
        
        result = await send_withdraw_command(
            wallet="bc1qtest1234567890abcdefghijklmnopqrstuvwxyz",
            amount_btc=Decimal("0.00001"),
            deal_id=999,
            user_id=123456
        )
        
        print("\n[3/4] Result received:")
        print(f"      Success: {result.get('success')}")
        print(f"      Message: {result.get('message')}")
        print(f"      TX ID: {result.get('tx_id')}")
        
        if result["success"]:
            print("\n[4/4] TEST PASSED: Command sent successfully!")
        else:
            print(f"\n[4/4] TEST FAILED: {result.get('message')}")
            
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await stop_withdraw_client()
        except:
            pass

if __name__ == "__main__":
    asyncio.run(test())

        import traceback
        traceback.print_exc()
    finally:
        try:
            await stop_withdraw_client()
        except:
            pass

if __name__ == "__main__":
    asyncio.run(test())
