"""
Инициализация всех обработчиков
"""
from aiogram import Router

# Импортируем все роутеры
from handlers.main_handlers import router as main_router
from handlers.sell_handlers import router as sell_router
from handlers.buy_handlers import router as buy_router
from handlers.deal_handlers import router as deal_router
from handlers.admin_handlers import router as admin_router
from handlers.chat_handlers import router as chat_router
from handlers.rating_handlers import router as rating_router
from handlers.template_handlers import router as template_router
from handlers.garant_handlers import router as garant_router
from handlers.garant_auto_handlers import router as garant_auto_router


def setup_handlers() -> Router:
    """
    Объединяет все роутеры в один главный
    """
    main_router_combined = Router()
    
    # Порядок важен! Более специфичные роутеры должны быть выше
    # main_router должен быть ПЕРВЫМ, чтобы обработчики текстовых кнопок главного меню не перехватывались другими роутерами
    main_router_combined.include_router(main_router)
    main_router_combined.include_router(admin_router)
    main_router_combined.include_router(sell_router)
    main_router_combined.include_router(garant_auto_router)
    main_router_combined.include_router(garant_router)
    main_router_combined.include_router(buy_router)
    main_router_combined.include_router(deal_router)
    main_router_combined.include_router(chat_router)
    main_router_combined.include_router(rating_router)
    main_router_combined.include_router(template_router)
    
    return main_router_combined