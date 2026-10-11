"""
Обработчики шаблонов (сохранённые реквизиты и избранные контрагенты)
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import (
    get_saved_payment_details, add_saved_payment_details,
    delete_saved_payment_details, get_payment_detail_by_id,
    get_favorite_partners, add_favorite_partner,
    remove_favorite_partner, update_favorite_note,
    get_user
)
from keyboards import saved_details_kb, favorites_kb
from utils import validate_payment_details

router = Router()


# ==================== FSM СОСТОЯНИЯ ====================

class TemplateStates(StatesGroup):
    WAIT_PAYMENT_TITLE = State()
    WAIT_PAYMENT_DETAILS = State()
    WAIT_FAVORITE_NOTE = State()


# ==================== СОХРАНЁННЫЕ РЕКВИЗИТЫ ====================

@router.callback_query(F.data == "templates:payment")
async def templates_payment(callback: CallbackQuery):
    """Список сохранённых реквизитов"""
    saved = await get_saved_payment_details(callback.from_user.id)
    
    if not saved:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить реквизиты", callback_data="templates:add_payment")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")]
        ])
        await callback.message.edit_text(
            "💳 <b>Сохранённые реквизиты</b>\n\n"
            "У вас пока нет сохранённых реквизитов.\n"
            "Вы можете добавить до 3 шаблонов.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    lines = ["💳 <b>Сохранённые реквизиты:</b>\n"]
    for detail in saved:
        lines.append(f"\n📋 <b>{detail['title']}</b>")
        lines.append(f"{detail['details'][:100]}...")
    
    kb_rows = []
    for detail in saved:
        kb_rows.append([
            InlineKeyboardButton(text=f"📋 {detail['title']}", callback_data=f"detail:view:{detail['id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"detail:delete:{detail['id']}")
        ])
    
    if len(saved) < 3:
        kb_rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="templates:add_payment")])
    
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    await callback.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "templates:add_payment")
async def templates_add_payment(callback: CallbackQuery, state: FSMContext):
    """Начало добавления реквизитов"""
    saved = await get_saved_payment_details(callback.from_user.id)
    
    if len(saved) >= 3:
        await callback.answer("❌ Максимум 3 шаблона. Удалите старые.", show_alert=True)
        return
    
    await state.set_state(TemplateStates.WAIT_PAYMENT_TITLE)
    
    await callback.message.edit_text(
        "📝 <b>Добавление реквизитов</b>\n\n"
        "Шаг 1/2: Введите название для этих реквизитов\n"
        "Например: Сбербанк, Тинькофф USDT, и т.п.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(TemplateStates.WAIT_PAYMENT_TITLE)
async def payment_title_received(message: Message, state: FSMContext):
    """Получение названия реквизитов"""
    if not message.text:
        await message.answer("❌ Укажите название текстом")
        return
    title = message.text.strip()
    
    if len(title) < 2:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")]
        ])
        await message.answer(
            "❌ Название слишком короткое (минимум 2 символа)",
            reply_markup=kb
        )
        return
    
    if len(title) > 50:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")]
        ])
        await message.answer(
            "❌ Название слишком длинное (максимум 50 символов)",
            reply_markup=kb
        )
        return
    
    await state.update_data(title=title)
    await state.set_state(TemplateStates.WAIT_PAYMENT_DETAILS)
    
    await message.answer(
        f"✅ Название: <b>{title}</b>\n\n"
        "Шаг 2/2: Теперь введите сами реквизиты\n"
        "Например: Карта Сбербанк 1234 5678 9012 3456, получатель Иван Иванов",
        parse_mode="HTML"
    )


@router.message(TemplateStates.WAIT_PAYMENT_DETAILS)
async def payment_details_received(message: Message, state: FSMContext):
    """Получение реквизитов"""
    if not message.text:
        await message.answer("❌ Укажите реквизиты текстом")
        return
    details = message.text.strip()
    
    if not validate_payment_details(details):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")]
        ])
        await message.answer(
            "❌ Реквизиты слишком короткие или длинные (5-500 символов)",
            reply_markup=kb
        )
        return
    
    data = await state.get_data()
    title = data.get("title")
    
    # Сохраняем
    try:
        await add_saved_payment_details(message.from_user.id, title, details)
        await message.answer(
            "✅ <b>Реквизиты сохранены!</b>\n\n"
            f"Название: {title}\n"
            f"Теперь вы можете быстро использовать их при создании заявок.",
            parse_mode="HTML"
        )
        await state.clear()
    except ValueError as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")]
        ])
        await message.answer(
            f"❌ {e}",
            reply_markup=kb
        )
    except Exception as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")]
        ])
        await message.answer(
            f"❌ Ошибка: {e}",
            reply_markup=kb
        )


@router.callback_query(F.data.startswith("detail:view:"))
async def detail_view(callback: CallbackQuery):
    """Просмотр реквизита"""
    detail_id = int(callback.data.split(":")[2])
    detail = await get_payment_detail_by_id(detail_id)
    
    if not detail or detail["user_id"] != callback.from_user.id:
        await callback.answer("❌ Реквизит не найден", show_alert=True)
        return
    
    text = (
        f"📋 <b>{detail['title']}</b>\n\n"
        f"{detail['details']}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"detail:delete:{detail_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="templates:payment")]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("detail:delete:"))
async def detail_delete(callback: CallbackQuery):
    """Удаление реквизита"""
    detail_id = int(callback.data.split(":")[2])
    
    success = await delete_saved_payment_details(detail_id, callback.from_user.id)
    
    if success:
        await callback.answer("✅ Удалено")
        # Возвращаемся к списку
        await templates_payment(callback)
    else:
        await callback.answer("❌ Не удалось удалить", show_alert=True)


# ==================== ИЗБРАННЫЕ КОНТРАГЕНТЫ ====================

@router.callback_query(F.data == "templates:favorites")
async def templates_favorites(callback: CallbackQuery):
    """Список избранных контрагентов"""
    favorites = await get_favorite_partners(callback.from_user.id)
    
    if not favorites:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")]
        ])
        await callback.message.edit_text(
            "⭐️ <b>Избранные контрагенты</b>\n\n"
            "У вас пока нет избранных контрагентов.\n"
            "Добавляйте проверенных партнёров после успешных сделок!",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    kb_rows = []
    for fav in favorites:
        display_name = fav.get("display_name")
        if display_name:
            user_display = display_name
        else:
            user_display = f"ID {fav['partner_id']}"
        rating = fav.get("average_rating") or 0
        rating_str = "⭐️" * int(rating) if rating > 0 else "Нет оценок"
        deals = fav.get("total_deals") or 0
        
        label = f"{user_display} | {rating_str} | {deals} сделок"
        kb_rows.append([
            InlineKeyboardButton(text=label, callback_data=f"favorite:view:{fav['partner_id']}")
        ])
    
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="profile:back")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    await callback.message.edit_text(
        "⭐️ <b>Избранные контрагенты</b>\n\n"
        "Ваши проверенные партнёры:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("favorite:view:"))
async def favorite_view(callback: CallbackQuery):
    """Просмотр избранного контрагента"""
    partner_id = int(callback.data.split(":")[2])
    
    # Получаем информацию о пользователе
    from database import get_user_statistics
    
    user = await get_user(partner_id)
    stats = await get_user_statistics(partner_id)
    
    if not user or not stats:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    display_name = user.get("display_name")
    if display_name:
        user_display = display_name
    else:
        user_display = f"ID {user['tg_id']}"
    
    rating = stats["average_rating"]
    rating_str = "⭐️" * int(rating) if rating > 0 else "Нет оценок"
    
    text = (
        f"⭐️ <b>Избранный контрагент</b>\n\n"
        f"👤 {user_display}\n"
        f"⭐️ Рейтинг: {rating:.1f}/5.0 {rating_str}\n"
        f"✅ Успешных сделок: {stats['success_count']}\n"
        f"📊 Всего сделок: {stats['total_deals']}\n"
        f"💰 Общий оборот: {stats['total_volume']} BTC"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Полный профиль", callback_data=f"profile:public:{partner_id}")],
        [InlineKeyboardButton(text="🗑 Удалить из избранного", callback_data=f"favorite:remove:{partner_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="templates:favorites")]
    ])
    
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("favorite:add:"))
async def favorite_add(callback: CallbackQuery):
    """Добавление в избранное"""
    parts = callback.data.split(":")
    partner_id = int(parts[2])
    deal_id = int(parts[3]) if len(parts) > 3 else None
    
    try:
        success = await add_favorite_partner(callback.from_user.id, partner_id)
        
        if success:
            await callback.answer("✅ Добавлено в избранное")
            
            # Обновляем сообщение
            if deal_id:
                await callback.message.edit_text(
                    "✅ <b>Контрагент добавлен в избранное!</b>\n\n"
                    "Теперь вы сможете быстро находить его заявки.",
                    parse_mode="HTML"
                )
        else:
            await callback.answer("❌ Уже в избранном", show_alert=True)
    except ValueError as e:
        await callback.answer(f"❌ {e}", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("favorite:remove:"))
async def favorite_remove(callback: CallbackQuery):
    """Удаление из избранного"""
    partner_id = int(callback.data.split(":")[2])
    
    success = await remove_favorite_partner(callback.from_user.id, partner_id)
    
    if success:
        await callback.answer("✅ Удалено из избранного")
        # Возвращаемся к списку
        await templates_favorites(callback)
    else:
        await callback.answer("❌ Не удалось удалить", show_alert=True)