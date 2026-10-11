"""
Обработчики чата между участниками сделки
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from database import (
    get_deal, can_access_chat, get_deal_messages,
    add_deal_message, get_chat_participants, DealStatus
)
from keyboards import chat_kb, quick_replies_kb
from utils import format_chat_message

router = Router()


async def _deal_id_from_callback(callback: CallbackQuery) -> int | None:
    try:
        return int(callback.data.split(":")[2])
    except (ValueError, IndexError, AttributeError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return None


# ==================== FSM СОСТОЯНИЯ ====================

class ChatStates(StatesGroup):
    WAIT_MESSAGE = State()


# ==================== ОТКРЫТИЕ ЧАТА ====================

@router.callback_query(F.data.startswith("chat:open:"))
async def chat_open(callback: CallbackQuery):
    """Открытие чата сделки"""
    deal_id = await _deal_id_from_callback(callback)
    if deal_id is None:
        return
    
    # Проверяем доступ
    if not await can_access_chat(callback.from_user.id, deal_id):
        await callback.answer("❌ Нет доступа к этому чату", show_alert=True)
        return
    
    # Получаем сообщения
    messages = await get_deal_messages(deal_id, limit=30)
    
    if not messages:
        text = f"💬 <b>Чат сделки #{deal_id}</b>\n\nЧат пуст. Напишите первое сообщение!"
    else:
        lines = [f"💬 <b>Чат сделки #{deal_id}</b>\n"]
        for msg in messages[-20:]:  # Последние 20 сообщений
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
    
    # Пытаемся отредактировать сообщение, если это возможно
    try:
        await callback.message.edit_text(text, reply_markup=chat_kb(deal_id), parse_mode="HTML")
    except Exception:
        # Если редактирование не удалось (например, это новое сообщение), отправляем новое
        await callback.message.answer(text, reply_markup=chat_kb(deal_id), parse_mode="HTML")
    
    await callback.answer()


@router.callback_query(F.data.startswith("chat:view:"))
async def chat_view(callback: CallbackQuery):
    """Просмотр чата (то же что open)"""
    deal_id = await _deal_id_from_callback(callback)
    if deal_id is None:
        return
    
    # Проверяем доступ
    if not await can_access_chat(callback.from_user.id, deal_id):
        await callback.answer("❌ Нет доступа к этому чату", show_alert=True)
        return
    
    # Получаем сообщения
    messages = await get_deal_messages(deal_id, limit=30)
    
    if not messages:
        text = f"💬 <b>Чат сделки #{deal_id}</b>\n\nЧат пуст. Напишите первое сообщение!"
    else:
        lines = [f"💬 <b>Чат сделки #{deal_id}</b>\n"]
        for msg in messages[-20:]:
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
    
    await callback.message.edit_text(text, reply_markup=chat_kb(deal_id), parse_mode="HTML")
    await callback.answer()


# ==================== ОБНОВЛЕНИЕ ЧАТА ====================

@router.callback_query(F.data.startswith("chat:refresh:"))
async def chat_refresh(callback: CallbackQuery):
    """Обновление чата"""
    deal_id = await _deal_id_from_callback(callback)
    if deal_id is None:
        return
    
    # Проверяем доступ
    if not await can_access_chat(callback.from_user.id, deal_id):
        await callback.answer("❌ Нет доступа к этому чату", show_alert=True)
        return
    
    # Получаем сообщения
    messages = await get_deal_messages(deal_id, limit=30)
    
    if not messages:
        text = f"💬 <b>Чат сделки #{deal_id}</b>\n\nЧат пуст."
    else:
        lines = [f"💬 <b>Чат сделки #{deal_id}</b>\n"]
        for msg in messages[-20:]:
            lines.append(format_chat_message(msg))
        text = "\n".join(lines)
    
    try:
        await callback.message.edit_text(text, reply_markup=chat_kb(deal_id), parse_mode="HTML")
    except Exception:
        # Если текст не изменился
        pass
    
    await callback.answer("🔄 Обновлено")


# ==================== НАПИСАТЬ СООБЩЕНИЕ ====================

@router.callback_query(F.data.startswith("chat:write:"))
async def chat_write(callback: CallbackQuery, state: FSMContext):
    """Начать написание сообщения"""
    deal_id = await _deal_id_from_callback(callback)
    if deal_id is None:
        return
    
    # Проверяем доступ
    if not await can_access_chat(callback.from_user.id, deal_id):
        await callback.answer("❌ Нет доступа к этому чату", show_alert=True)
        return
    
    # Проверяем статус сделки
    d = await get_deal(deal_id)
    allowed_statuses = [DealStatus.MATCHED, DealStatus.PAYMENT_PENDING, DealStatus.DISPUTED]
    
    if not d or d["status"] not in allowed_statuses:
        await callback.answer("❌ Чат недоступен на текущем этапе сделки", show_alert=True)
        return
    
    await state.set_state(ChatStates.WAIT_MESSAGE)
    await state.update_data(deal_id=deal_id)
    
    # Показываем быстрые ответы
    await callback.message.answer(
        "✨ <b>Написать сообщение</b> ✨\n\n"
        "💬 <b>Выберите готовый вариант или напишите своё сообщение:</b>",
        reply_markup=quick_replies_kb(deal_id),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(ChatStates.WAIT_MESSAGE)
async def chat_message_received(message: Message, state: FSMContext):
    """Получение сообщения от пользователя"""
    data = await state.get_data()
    deal_id = data.get("deal_id")
    
    if not deal_id:
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="mydeals:back")]
        ])
        await message.answer(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=kb
        )
        return
    
    # Проверяем доступ
    if not await can_access_chat(message.from_user.id, deal_id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="mydeals:back")]
        ])
        await message.answer(
            "❌ Нет доступа к этому чату",
            reply_markup=kb
        )
        await state.clear()
        return
    
    if not message.text:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"chat:open:{deal_id}")]
        ])
        await message.answer(
            "❌ Напишите сообщение текстом",
            reply_markup=kb
        )
        return

    text = message.text.strip()
    
    if not text:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"chat:open:{deal_id}")]
        ])
        await message.answer(
            "❌ Сообщение не может быть пустым",
            reply_markup=kb
        )
        return
    
    # Добавляем сообщение
    try:
        await add_deal_message(deal_id, message.from_user.id, text)
        await message.answer(
            "✅ Сообщение отправлено",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")],
                [InlineKeyboardButton(text="◀️ К сделке", callback_data=f"mydeal:view:{deal_id}")]
            ])
        )
        
        # Уведомляем второго участника
        participants = await get_chat_participants(deal_id)
        if participants:
            other_user_id = participants["partner_id"] if message.from_user.id == participants["owner_id"] else participants["owner_id"]
            
            if other_user_id:
                try:
                    from database import send_notification
                    from database import get_user
                    sender = await get_user(message.from_user.id)
                    sender_name = sender.get("display_name") if sender else "Контрагент"
                    notification = (
                        f"💬 <b>Новое сообщение в чате</b>\n\n"
                        f"Сделка #{deal_id}\n"
                        f"От {sender_name}:\n"
                        f"{text[:100]}"
                    )
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")]
                    ])
                    await send_notification(message.bot, other_user_id, notification, reply_markup=kb)
                except Exception:
                    pass
        
        await state.clear()
    except ValueError as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"chat:open:{deal_id}")]
        ])
        await message.answer(
            f"❌ {e}",
            reply_markup=kb
        )
    except Exception as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"chat:open:{deal_id}")]
        ])
        await message.answer(
            f"❌ Ошибка отправки: {e}",
            reply_markup=kb
        )


# ==================== БЫСТРЫЕ ОТВЕТЫ ====================

@router.callback_query(F.data.startswith("chat:quick:"))
async def chat_quick_reply(callback: CallbackQuery):
    """Быстрый ответ"""
    parts = callback.data.split(":")
    action = parts[2]
    deal_id = int(parts[3])
    
    # Проверяем доступ
    if not await can_access_chat(callback.from_user.id, deal_id):
        await callback.answer("❌ Нет доступа к этому чату", show_alert=True)
        return
    
    # Определяем текст быстрого ответа
    quick_messages = {
        "confirm": "✅ Подтверждаю",
        "time": "⏰ Нужно немного времени",
        "details": "❓ Уточните, пожалуйста, детали"
    }
    
    text = quick_messages.get(action)
    
    if not text:
        await callback.answer("❌ Неизвестный вариант", show_alert=True)
        return
    
    # Отправляем сообщение
    try:
        await add_deal_message(deal_id, callback.from_user.id, text)
        await callback.answer("✅ Отправлено")
        
        # Обновляем чат
        messages = await get_deal_messages(deal_id, limit=30)
        lines = [f"💬 <b>Чат сделки #{deal_id}</b>\n"]
        for msg in messages[-20:]:
            lines.append(format_chat_message(msg))
        
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=chat_kb(deal_id),
            parse_mode="HTML"
        )
        
        # Уведомляем второго участника
        participants = await get_chat_participants(deal_id)
        if participants:
            other_user_id = participants["partner_id"] if callback.from_user.id == participants["owner_id"] else participants["owner_id"]
            
            if other_user_id:
                try:
                    from database import send_notification
                    from database import get_user
                    sender = await get_user(callback.from_user.id)
                    sender_name = sender.get("display_name") if sender else "Контрагент"
                    notification = (
                        f"💬 <b>Новое сообщение в чате</b>\n\n"
                        f"Сделка #{deal_id}\n"
                        f"От {sender_name}:\n"
                        f"{text}"
                    )
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="💬 Открыть чат", callback_data=f"chat:open:{deal_id}")]
                    ])
                    await send_notification(callback.message.bot, other_user_id, notification, reply_markup=kb)
                except Exception:
                    pass
        
    except ValueError as e:
        await callback.answer(f"❌ {e}", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)