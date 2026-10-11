"""
Обработчики рейтингов и отзывов
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import (
    get_deal, add_rating, check_can_rate, get_user_ratings,
    get_user, get_user_statistics
)
from keyboards import stars_kb, speed_kb
from utils import format_rating_stars, format_profile_info

router = Router()


# ==================== FSM СОСТОЯНИЯ ====================

class RatingStates(StatesGroup):
    WAIT_STARS = State()
    WAIT_SPEED = State()
    WAIT_COMMENT = State()


# ==================== НАЧАЛО ОЦЕНКИ ====================

@router.callback_query(F.data.startswith("rate:start:"))
async def rate_start(callback: CallbackQuery, state: FSMContext):
    """Начало оценки контрагента"""
    parts = callback.data.split(":")
    deal_id = int(parts[2])
    partner_id = int(parts[3])
    
    # Проверяем можно ли оставить отзыв
    if not await check_can_rate(deal_id, callback.from_user.id):
        await callback.answer("❌ Вы уже оценили этого контрагента", show_alert=True)
        return
    
    await state.set_state(RatingStates.WAIT_STARS)
    await state.update_data(deal_id=deal_id, partner_id=partner_id)
    
    await callback.message.edit_text(
        "⭐️ <b>Оценка контрагента</b>\n\n"
        "Как бы вы оценили работу с этим контрагентом?\n"
        "Выберите от 1 до 5 звёзд:",
        reply_markup=stars_kb(deal_id, partner_id),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rate:skip:"))
async def rate_skip(callback: CallbackQuery, state: FSMContext):
    """Пропустить оценку"""
    await state.clear()
    await callback.message.edit_text("✅ Оценка пропущена. Спасибо за использование сервиса!")
    await callback.answer()


# ==================== ВЫБОР ЗВЁЗД ====================

@router.callback_query(F.data.startswith("rate:stars:"))
async def rate_stars(callback: CallbackQuery, state: FSMContext):
    """Выбор количества звёзд"""
    parts = callback.data.split(":")
    try:
        stars = int(parts[2])
        deal_id = int(parts[3])
        partner_id = int(parts[4])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверные данные оценки", show_alert=True)
        return
    
    await state.update_data(stars=stars)
    
    await callback.message.edit_text(
        f"⭐️ <b>Оценка: {stars}/5</b>\n\n"
        "Как быстро контрагент отвечал?",
        reply_markup=speed_kb(deal_id, partner_id, stars),
        parse_mode="HTML"
    )
    await callback.answer()


# ==================== ВЫБОР СКОРОСТИ ====================

@router.callback_query(F.data.startswith("rate:speed:"))
async def rate_speed(callback: CallbackQuery, state: FSMContext):
    """Выбор скорости ответа"""
    parts = callback.data.split(":")
    try:
        speed = int(parts[2])
        deal_id = int(parts[3])
        partner_id = int(parts[4])
        stars = int(parts[5])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверные данные оценки", show_alert=True)
        return
    
    await state.update_data(speed=speed)
    await state.set_state(RatingStates.WAIT_COMMENT)
    
    await callback.message.edit_text(
        f"⭐️ <b>Оценка: {stars}/5</b>\n"
        f"⚡️ <b>Скорость: {speed}/5</b>\n\n"
        "💬 Напишите комментарий (необязательно):\n\n"
        "Или нажмите кнопку ниже, чтобы пропустить.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить комментарий", callback_data=f"rate:comment:skip:{deal_id}:{partner_id}:{stars}:{speed}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rate:comment:skip:"))
async def rate_comment_skip(callback: CallbackQuery, state: FSMContext):
    """Пропустить комментарий"""
    parts = callback.data.split(":")
    deal_id = int(parts[3])
    partner_id = int(parts[4])
    stars = int(parts[5])
    speed = int(parts[6])
    
    # Сохраняем рейтинг без комментария
    try:
        await add_rating(deal_id, callback.from_user.id, partner_id, stars, speed_rating=speed, comment=None)
        await callback.message.edit_text(
            "✅ <b>Спасибо за оценку!</b>\n\n"
            "Ваш отзыв помогает другим пользователям.",
            parse_mode="HTML"
        )
        await state.clear()
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    
    await callback.answer()


@router.message(RatingStates.WAIT_COMMENT)
async def rate_comment_received(message: Message, state: FSMContext):
    """Получение комментария"""
    data = await state.get_data()
    deal_id = data.get("deal_id")
    partner_id = data.get("partner_id")
    stars = data.get("stars")
    speed = data.get("speed")
    
    if not all([deal_id, partner_id, stars]):
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="mydeals:back")]
        ])
        await message.answer(
            "❌ Сессия истекла. Начните заново.",
            reply_markup=kb
        )
        return
    
    if not message.text:
        await message.answer("❌ Напишите комментарий текстом или нажмите «Пропустить».")
        return
    comment = message.text.strip()
    
    # Валидация
    if len(comment) > 500:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"rate:skip:{deal_id}")]
        ])
        await message.answer(
            "❌ Комментарий слишком длинный (макс. 500 символов)",
            reply_markup=kb
        )
        return
    
    # Сохраняем рейтинг с комментарием
    try:
        await add_rating(deal_id, message.from_user.id, partner_id, stars, speed_rating=speed, comment=comment)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="mydeals:back")]
        ])
        await message.answer(
            "✅ <b>Спасибо за оценку!</b>\n\n"
            "Ваш отзыв помогает другим пользователям.",
            parse_mode="HTML",
            reply_markup=kb
        )
        await state.clear()
    except Exception as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"rate:skip:{deal_id}")]
        ])
        await message.answer(
            f"❌ Ошибка: {e}",
            reply_markup=kb
        )


# ==================== ПРОСМОТР ОТЗЫВОВ ====================

@router.callback_query(F.data == "profile:reviews")
async def profile_reviews(callback: CallbackQuery):
    """Просмотр своих отзывов"""
    ratings = await get_user_ratings(callback.from_user.id, limit=10)
    
    if not ratings:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
        ])
        try:
            await callback.message.edit_text(
                "📝 <b>Ваши отзывы</b>\n\nУ вас пока нет отзывов",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                "📝 <b>Ваши отзывы</b>\n\nУ вас пока нет отзывов",
                reply_markup=kb,
                parse_mode="HTML"
            )
        await callback.answer()
        return
    
    lines = ["📝 <b>Ваши отзывы:</b>\n"]
    
    for r in ratings:
        from_display_name = r.get("from_display_name") or "Пользователь"
        stars = "⭐️" * r["stars"]
        created = r["created_at"][:10]  # Дата без времени
        
        lines.append(
            f"\n{stars} ({r['stars']}/5)\n"
            f"От: {from_display_name}\n"
            f"Дата: {created}"
        )
        
        if r.get("speed_rating"):
            speed_emoji = "⚡️" if r["speed_rating"] >= 4 else "👍" if r["speed_rating"] >= 3 else "🐌"
            lines.append(f"Скорость: {speed_emoji} {r['speed_rating']}/5")
        
        if r.get("comment"):
            lines.append(f"Комментарий: {r['comment'][:100]}")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
    ])
    
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "profile:back")
async def profile_back(callback: CallbackQuery):
    """Назад к профилю"""
    from database import upsert_user, get_user, get_user_statistics
    from utils import format_profile_info
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    await upsert_user(callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
    
    user = await get_user(callback.from_user.id)
    stats = await get_user_statistics(callback.from_user.id)
    
    if not user or not stats:
        await callback.message.edit_text("❌ Ошибка загрузки профиля")
        return
    
    profile_text = await format_profile_info(user, stats)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Мои реквизиты", callback_data="templates:payment"),
            InlineKeyboardButton(text="💼 BTC-Кошелек", callback_data="profile:btc_wallet")
        ],
        [
            InlineKeyboardButton(text="📝 Мои отзывы", callback_data="profile:reviews"),
            InlineKeyboardButton(text="📊 Публичный профиль", callback_data=f"profile:public:{callback.from_user.id}")
        ],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])
    
    try:
        await callback.message.edit_text(profile_text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(profile_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("profile:public:"))
async def profile_public(callback: CallbackQuery):
    """Публичный профиль пользователя"""
    parts = callback.data.split(":")
    user_id = int(parts[2])
    
    # Проверяем, есть ли информация о возврате (формат: profile:public:user_id:back:deal:start:deal_id или profile:public:user_id:back:deal:view:deal_id)
    back_callback = None
    if len(parts) >= 6 and parts[3] == "back":
        # Формируем callback_data для возврата
        if parts[4] == "deal" and len(parts) >= 7:
            deal_id = parts[6]
            if parts[5] == "start":
                back_callback = f"deal:start:{deal_id}"
            elif parts[5] == "view":
                back_callback = f"deal:view:{deal_id}"
        # Можно добавить другие варианты возврата здесь
    
    user = await get_user(user_id)
    stats = await get_user_statistics(user_id)
    
    if not user or not stats:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Показываем username только для своего профиля
    is_own_profile = (user_id == callback.from_user.id)
    # Для публичного профиля используем is_public=True
    profile_text = await format_profile_info(user, stats, show_username=is_own_profile, is_public=True)
    
    # Добавляем последние отзывы
    ratings = await get_user_ratings(user_id, limit=3)
    
    if ratings:
        profile_text += "\n\n📝 <b>Последние отзывы:</b>\n"
        for r in ratings:
            # В отзывах тоже не показываем username, только звёзды и комментарий
            stars = "⭐️" * r["stars"]
            profile_text += f"\n{stars}"
            if r.get("comment"):
                profile_text += f"\n{r['comment'][:100]}"
    
    # Определяем callback_data для кнопки "Назад"
    if back_callback:
        # Если есть информация о возврате, используем её
        back_button_callback = back_callback
    elif is_own_profile:
        # Если это свой профиль, возвращаемся к своему профилю
        back_button_callback = "profile:back"
    else:
        # По умолчанию возвращаемся к "мои сделки"
        back_button_callback = "mydeals:back"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_button_callback)]
    ])
    
    await callback.message.edit_text(profile_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()
    # Показываем username только для своего профиля
    is_own_profile = (user_id == callback.from_user.id)
    # Для публичного профиля используем is_public=True
    profile_text = await format_profile_info(user, stats, show_username=is_own_profile, is_public=True)
    
    # Добавляем последние отзывы
    ratings = await get_user_ratings(user_id, limit=3)
    
    if ratings:
        profile_text += "\n\n📝 <b>Последние отзывы:</b>\n"
        for r in ratings:
            # В отзывах тоже не показываем username, только звёзды и комментарий
            stars = "⭐️" * r["stars"]
            profile_text += f"\n{stars}"
            if r.get("comment"):
                profile_text += f"\n{r['comment'][:100]}"
    
    # Определяем callback_data для кнопки "Назад"
    if back_callback:
        # Если есть информация о возврате, используем её
        back_button_callback = back_callback
    elif is_own_profile:
        # Если это свой профиль, возвращаемся к своему профилю
        back_button_callback = "profile:back"
    else:
        # По умолчанию возвращаемся к "мои сделки"
        back_button_callback = "mydeals:back"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_button_callback)]
    ])
    
    await callback.message.edit_text(profile_text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()