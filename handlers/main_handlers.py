"""
Главные обработчики (старт, помощь, профиль, меню)
"""
import re
import logging
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import (
    upsert_user, get_user, get_user_statistics,
    get_user_active_deals, get_user_history_deals,
    list_active_deals_by_side, get_favorite_partners,
    DealStatus, find_deal_by_username_and_status, confirm_deposit,
    get_saved_payment_details, get_payment_detail_by_id, get_deal, send_notification,
    find_user_by_username, update_user_name, update_user_btc_wallet, is_user_registered
)
from database import is_valid_btc_bech32
from keyboards import (
    sell_or_buy_kb, my_deals_kb, main_menu_kb, p2p_menu_kb
)
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import (
    format_profile_info, get_status_emoji, get_role_name,
    format_datetime, get_status_name, btc_to_rub, get_btc_rub_rate,
    get_btc_usd_rate, get_usd_rub_rate, rub_to_btc
)
from config import HELP_TEXT, DEPOSIT_NOTIFY_CHAT_ID, BOT_TOKEN, BTC_TO_RUB_RATE
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)
router = Router()


# ==================== FSM СОСТОЯНИЯ ДЛЯ РЕГИСТРАЦИИ ====================

class RegisterStates(StatesGroup):
    WAIT_NAME = State()
    WAIT_BTC_WALLET = State()


class ProfileStates(StatesGroup):
    WAIT_BTC_WALLET = State()


# ==================== ОБРАБОТКА УВЕДОМЛЕНИЙ О ДЕПОЗИТАХ ====================
# ВАЖНО: Этот обработчик должен быть первым, чтобы перехватывать сообщения из DEPOSIT_NOTIFY_CHAT_ID

if DEPOSIT_NOTIFY_CHAT_ID:
    # Основной обработчик автоподтверждения
    # Используем F.text | F.caption чтобы обрабатывать и текстовые сообщения, и сообщения с подписями
    @router.message(
        F.chat.id == DEPOSIT_NOTIFY_CHAT_ID,
        (F.text | F.caption),
        flags={"deposit_notify": True}  # Флаг для идентификации
    )
    async def deposit_notify_handler(message: Message):
        """Обработчик сообщений из чата уведомлений о депозитах.
        Парсит сообщение и автоматически подтверждает депозит.
        
        Формат сообщения: "От: @username\nСумма: 0.001 BTC\nТранзакция: ..."
        """
        logger.info(f"📨 [АВТОПОДТВЕРЖДЕНИЕ] Получено сообщение из канала уведомлений (ID: {DEPOSIT_NOTIFY_CHAT_ID})")
        logger.info(f"📨 [АВТОПОДТВЕРЖДЕНИЕ] Chat ID сообщения: {message.chat.id}")
        logger.info(f"📨 [АВТОПОДТВЕРЖДЕНИЕ] Тип чата: {message.chat.type}")
        logger.info(f"📨 [АВТОПОДТВЕРЖДЕНИЕ] Текст сообщения: {message.text[:200] if message.text else 'нет текста'}")
        logger.info(f"📨 [АВТОПОДТВЕРЖДЕНИЕ] От: {message.from_user.username if message.from_user else 'N/A'}")
        
        text = message.text or ""
        
        # Новый формат уведомления:
        # "Сумма 0.00001 BTC (84 RUB)
        # Была зачислена на ваш счет.
        # 🤵🏼От: @K0_T_1K"
        
        # Ищем сумму в формате "Сумма X BTC"
        amount_match = re.search(r'Сумма\s+([\d.]+)\s+BTC', text)
        if not amount_match:
            logger.warning(f"⚠️ [АВТОПОДТВЕРЖДЕНИЕ] Сообщение не содержит 'Сумма X BTC': {text[:200]}")
            return
        
        amount = Decimal(amount_match.group(1))
        logger.info(f"💰 [АВТОПОДТВЕРЖДЕНИЕ] Найдена сумма депозита: {amount} BTC")
        
        # Ищем username в формате "@username" или "🤵🏼От: @username"
        username_match = re.search(r'[🤵🏼От:\s]*@([A-Za-z0-9_]+)', text)
        if not username_match:
            logger.warning(f"⚠️ [АВТОПОДТВЕРЖДЕНИЕ] Сообщение не содержит '@username': {text[:200]}")
            return
        
        username = username_match.group(1)
        logger.info(f"🔍 Найден username в уведомлении о депозите: @{username}")
        
        # Находим пользователя по username
        user = await find_user_by_username(username)
        if not user:
            logger.warning(f"⚠️ Пользователь с username @{username} не найден в базе")
            return
        
        logger.info(f"👤 Найден пользователь: {user['tg_id']} (@{username})")
        
        # Находим активную сделку пользователя со статусом AWAITING_DEPOSIT или CONFIRMING
        from database import get_user_active_deals, DealStatus, get_deal
        deals = await get_user_active_deals(user["tg_id"])
        deal_id = None
        matching_deal = None
        
        for deal in deals:
            if deal["status"] in [DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING]:
                # Проверяем, совпадает ли сумма
                deal_amount = Decimal(deal.get("max_btc") or "0")
                # Сравниваем суммы с небольшой погрешностью (0.00000001 BTC)
                if abs(deal_amount - amount) < Decimal("0.00000001"):
                    deal_id = deal["id"]
                    matching_deal = deal
                    logger.info(f"✅ Найдена сделка #{deal_id} с совпадающей суммой: {deal_amount} BTC")
                    break
        
        if not deal_id:
            logger.warning(f"⚠️ Не найдена сделка с совпадающей суммой {amount} BTC для пользователя @{username}")
            return

        from database import claim_deposit_message
        if not await claim_deposit_message(message.chat.id, message.message_id):
            logger.info(f"ℹ️ [АВТОПОДТВЕРЖДЕНИЕ] Сообщение {message.message_id} уже обработано, пропускаю")
            return
                
        # Подтверждаем депозит (без отправки уведомления - оно будет отправлено после нажатия "Депозит внесён")
        try:
            await confirm_deposit(deal_id)
            if deal_id:
                logger.info(f"✅ Депозит подтверждён автоматически для сделки #{deal_id} (уведомление будет отправлено после нажатия 'Депозит внесён')")
            else:
                logger.warning(f"⚠️ Не найдена подходящая сделка для подтверждения депозита от @{username}")
        except Exception as e:
            logger.error(f"❌ Ошибка при подтверждении депозита: {e}", exc_info=True)


# ==================== ОБРАБОТЧИКИ РЕГИСТРАЦИИ ====================

@router.message(RegisterStates.WAIT_NAME)
async def register_name(message: Message, state: FSMContext):
    """Получение имени пользователя"""
    if not message.text:
        await message.answer(
            "👤 <b>Укажите ваше имя текстом:</b>",
            parse_mode="HTML"
        )
        return
    name = message.text.strip()
    
    if len(name) < 2:
        await message.answer(
            "❌ <b>Имя слишком короткое</b>\n\n"
            "👤 <b>Укажите ваше имя (минимум 2 символа):</b>",
            parse_mode="HTML"
        )
        return
    
    if len(name) > 200:
        await message.answer(
            "❌ <b>Имя слишком длинное</b>\n\n"
            "👤 <b>Укажите ваше имя (максимум 200 символов):</b>",
            parse_mode="HTML"
        )
        return
    
    # Сохраняем имя
    await update_user_name(message.from_user.id, name)
    
    # Переходим к запросу BTC кошелька
    await state.set_state(RegisterStates.WAIT_BTC_WALLET)
    await message.answer(
        f"✅ <b>Имя сохранено:</b> {name}\n\n"
        "💼 <b>Теперь укажите ваш BTC кошелек:</b>\n\n"
        "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
        "⚠️ <b>Важно:</b> Этот кошелек будет использоваться во всех сделках, где требуется BTC адрес",
        parse_mode="HTML"
    )


@router.message(RegisterStates.WAIT_BTC_WALLET)
async def register_btc_wallet(message: Message, state: FSMContext):
    """Получение BTC кошелька пользователя"""
    if not message.text:
        await message.answer(
            "💼 <b>Укажите ваш BTC кошелек текстом:</b>",
            parse_mode="HTML"
        )
        return
    wallet = message.text.strip()
    
    # Валидация BTC адреса (базовая проверка)
    if not is_valid_btc_bech32(wallet) and not (wallet.startswith("1") or wallet.startswith("3") or wallet.startswith("bc1")):
        await message.answer(
            "❌ <b>Неверный формат BTC адреса</b>\n\n"
            "💼 <b>Укажите ваш BTC кошелек:</b>\n\n"
            "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
            "💡 <b>Пример:</b> <code>bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh</code>",
            parse_mode="HTML"
        )
        return
    
    # Сохраняем BTC кошелек
    await update_user_btc_wallet(message.from_user.id, wallet)
    data = await state.get_data()
    pending_deal_id = data.get("pending_deal_id")
    
    # Регистрация завершена
    await state.clear()
    await message.answer(
        f"✅ <b>Регистрация завершена!</b>\n\n"
        f"💼 <b>BTC кошелек сохранен:</b> <code>{wallet}</code>\n\n"
        "🎉 <b>Теперь вы можете пользоваться всеми функциями бота!</b>",
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )
    
    if pending_deal_id:
        d = await get_deal(pending_deal_id)
        if not d or d.get("status") != DealStatus.ACTIVE:
            await message.answer("❌ Заявка недоступна или уже не активна")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Открыть заявку", callback_data=f"deal:view:{pending_deal_id}")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            f"✅ Найдена заявка <b>#{pending_deal_id}</b>.\n\nНажмите кнопку ниже, чтобы открыть.",
            parse_mode="HTML",
            reply_markup=kb
        )


# ==================== КОМАНДЫ ====================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start"""
    await state.clear()
    await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    
    payload = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1].strip()
    pending_deal_id = None
    if payload.startswith("deal_"):
        deal_id_str = payload.split("_", 1)[1]
        try:
            pending_deal_id = int(deal_id_str)
        except ValueError:
            pending_deal_id = None
    
    # Проверяем, зарегистрирован ли пользователь
    if not await is_user_registered(message.from_user.id):
        # Пользователь не зарегистрирован, начинаем процесс регистрации
        user = await get_user(message.from_user.id)
        if not user or not user.get("name"):
            if pending_deal_id:
                await state.update_data(pending_deal_id=pending_deal_id)
            # Запрашиваем имя
            await state.set_state(RegisterStates.WAIT_NAME)
            await message.answer(
                "👋 <b>Добро пожаловать!</b>\n\n"
                "🤖 Я P2P бот для безопасного обмена BTC\n\n"
                "📝 <b>Для начала работы необходимо пройти регистрацию</b>\n\n"
                "👤 <b>Укажите ваше имя:</b>",
                parse_mode="HTML"
            )
            return
        elif not user.get("btc_wallet"):
            if pending_deal_id:
                await state.update_data(pending_deal_id=pending_deal_id)
            # Имя есть, но нет BTC кошелька
            await state.set_state(RegisterStates.WAIT_BTC_WALLET)
            await message.answer(
                "💼 <b>Укажите ваш BTC кошелек:</b>\n\n"
                "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
                "⚠️ <b>Важно:</b> Этот кошелек будет использоваться во всех сделках, где требуется BTC адрес",
                parse_mode="HTML"
            )
            return
    
    # Обработка deep-link для заявки: /start deal_<id>
    if pending_deal_id:
        d = await get_deal(pending_deal_id)
        if not d or d.get("status") != DealStatus.ACTIVE:
            await message.answer("❌ Заявка недоступна или уже не активна")
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Открыть заявку", callback_data=f"deal:view:{pending_deal_id}")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            f"✅ Найдена заявка <b>#{pending_deal_id}</b>.\n\nНажмите кнопку ниже, чтобы открыть.",
            parse_mode="HTML",
            reply_markup=kb
        )
        return

    # Пользователь зарегистрирован, показываем главное меню
    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "🤖 Я P2P бот для безопасного обмена BTC\n\n"
        "💡 <b>Выберите действие из меню:</b>",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    """Команда /help"""
    # Выходим из любого состояния FSM
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Комиссии", callback_data="help:fees")],
        [InlineKeyboardButton(text="🔒 Безопасность", callback_data="help:safety")],
        [InlineKeyboardButton(text="⚖️ Советы", callback_data="help:tips")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="help:contacts")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="help:faq")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])
    await message.answer(
        "✨ <b>Справка</b> ✨\n\n"
        "📋 <b>Выберите раздел, который вас интересует:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )




@router.callback_query(F.data == "help:fees")
async def help_fees(callback: CallbackQuery):
    """Комиссии"""
    text = (
        "💰 <b>Система комиссий</b>\n\n\n"
        "📊 <b>Для продавца:</b>\n"
        "• Продавец не платит комиссию\n\n\n"
        "💵 <b>Для покупателя:</b>\n"
        "• Платформа удерживает 0% комиссии из BTC (временно)\n\n\n"
        "⚠️ <b>При отмене сделки самостоятельно:</b>\n"
        "• Удерживается 1.5% штраф"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к справке", callback_data="help:back")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:safety")
async def help_safety(callback: CallbackQuery):
    """Безопасность"""
    text = (
        "🔒 <b>Безопасность сделок</b>\n\n\n"
        "🛡️ <b>Защита от мошенничества:</b>\n"
        "• Система рейтингов и отзывов\n"
        "• История всех сделок\n"
        "• Модерация депозитов\n"
        "• Возможность открыть спор\n\n\n"
        "⚠️ <b>Важные правила:</b>\n"
        "• Никогда не переводите средства напрямую\n"
        "• Проверяйте рейтинг контрагента\n\n\n"
        "📞 <b>При проблемах:</b>\n"
        "• Используйте кнопку \"ОТКРЫТЬ ДИСПУТ\"\n"
        "• Обратитесь к администраторам"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к справке", callback_data="help:back")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:contacts")
async def help_contacts(callback: CallbackQuery):
    """Контакты"""
    text = HELP_TEXT
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к справке", callback_data="help:back")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:faq")
async def help_faq(callback: CallbackQuery):
    """FAQ"""
    text = (
        "📚 <b>Часто задаваемые вопросы</b>\n\n\n"
        "❓ <b>Как долго ждать подтверждения депозита?</b>\n"
        "Обычно 3-5 минут после подтверждения\n\n\n"
        "❓ <b>Можно ли отменить сделку?</b>\n"
        "Да, но с удержанием 1.5% штрафа от суммы депозита\n\n\n"
        "❓ <b>Что делать, если контрагент не отвечает?</b>\n"
        "Используйте чат сделки или откройте спор\n\n\n"
        "❓ <b>Как повысить рейтинг?</b>\n"
        "Завершайте сделки успешно и быстро\n\n\n"
        "❓ <b>Есть ли комиссия у площадки?</b>\n"
        "Временно 0% для всех, продавец не платит комиссию"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к справке", callback_data="help:back")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:tips")
async def help_tips(callback: CallbackQuery):
    """Советы"""
    text = (
        "⚖️ <b>Советы</b>\n\n\n"
        "🎙️ <b>Как легко перевести рубли в BTC с помощью BMB?</b>\n\n"
        "• Открываем бота BigMafiaBot\n\n"
        "• Нажимаем «Баланс» - «Вывести» - «Другому пользователю»\n\n"
        "• Выбираем любого пользователя\n\n"
        "• Далее вводим сумму в рублях, которую хотите продать\n\n"
        "• Вот и у вас есть сумма в BTC\n\n"
        "• Отправлять деньги «любому пользователю» не нужно"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к справке", callback_data="help:back")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "help:back")
async def help_back(callback: CallbackQuery):
    """Назад к справке"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Комиссии", callback_data="help:fees")],
        [InlineKeyboardButton(text="🔒 Безопасность", callback_data="help:safety")],
        [InlineKeyboardButton(text="⚖️ Советы", callback_data="help:tips")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="help:contacts")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="help:faq")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])
    try:
        await callback.message.edit_text(
            "✨ <b>Справка</b> ✨\n\n"
            "📋 <b>Выберите раздел, который вас интересует:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Справка</b> ✨\n\n"
            "📋 <b>Выберите раздел, который вас интересует:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(Command("profile"))
async def cmd_profile(message: Message, state: FSMContext):
    """Команда /profile"""
    # Выходим из любого состояния FSM
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    
    user = await get_user(message.from_user.id)
    stats = await get_user_statistics(message.from_user.id)
    
    if not user or not stats:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            "❌ Ошибка загрузки профиля",
            reply_markup=kb
        )
        return
    
    profile_text = await format_profile_info(user, stats)
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Мои реквизиты", callback_data="templates:payment"),
            InlineKeyboardButton(text="💼 BTC-Кошелек", callback_data="profile:btc_wallet")
        ],
        [
            InlineKeyboardButton(text="📝 Мои отзывы", callback_data="profile:reviews"),
            InlineKeyboardButton(text="📊 Публичный профиль", callback_data=f"profile:public:{message.from_user.id}")
        ],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])
    
    await message.answer(profile_text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("create"))
async def cmd_create(message: Message, state: FSMContext):
    """Команда /create - Создать объявление"""
    # Очищаем состояние перед показом меню
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await message.answer(
        "✨ <b>Создание заявки</b> ✨",
        reply_markup=sell_or_buy_kb(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "sell_or_buy:menu")
async def sell_or_buy_menu(callback: CallbackQuery, state: FSMContext):
    """Меню Продать или Купить (callback)"""
    await state.clear()
    try:
        await callback.message.edit_text(
            "✨ <b>Создание заявки</b> ✨",
            reply_markup=sell_or_buy_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Создание заявки</b> ✨",
            reply_markup=sell_or_buy_kb(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.message(Command("buy"))
async def cmd_buy(message: Message, state: FSMContext):
    """Команда /buy - показывает активные заявки на продажу (SELL)"""
    # Выходим из любого состояния FSM
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    
    logger.info(f"📋 Запрос активных заявок на продажу для пользователя {message.from_user.id}")
    deals = await list_active_deals_by_side("SELL", exclude_owner_id=message.from_user.id, limit=1000)
    logger.info(f"📋 Получено {len(deals)} активных заявок на продажу")
    
    # Формируем первую страницу без сортировки
    text, kb = await build_buy_list_page(deals, page=1, user_id=message.from_user.id, sort_mode="")
    
    await message.answer(
        text,
            reply_markup=kb,
            parse_mode="HTML"
        )


@router.message(Command("sell"))
async def cmd_sell(message: Message, state: FSMContext):
    """Команда /sell - показывает активные заявки на покупку (BUY)"""
    # Выходим из любого состояния FSM
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    
    deals = await list_active_deals_by_side("BUY", exclude_owner_id=message.from_user.id, limit=10)
    
    if not deals:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            "📭 <b>Заявок на покупку пока нет</b>\n\n"
            "💡 <b>Создайте заявку на продажу, чтобы другие пользователи могли её увидеть!</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    
    # Получаем избранных контрагентов
    favorites = await get_favorite_partners(message.from_user.id)
    favorite_ids = {f["partner_id"] for f in favorites}
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from datetime import datetime
    from decimal import Decimal
    
    rows = []
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        rng = f"{min_btc}—{max_btc}" if min_btc != max_btc else f"{max_btc}"
        t = datetime.fromisoformat(d["expires_at"]).strftime("%H:%M")
        
        # Отмечаем избранных звёздочкой
        prefix = "⭐️ " if d["owner_id"] in favorite_ids else ""
        label = f"{prefix}{rng} BTC • до {t}"
        
        rows.append([InlineKeyboardButton(text=label, callback_data=f"deal:view:{d['id']}")])
    
    rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")])
    
    await message.answer(
        "✨ <b>Заявки на покупку</b> ✨\n\n"
        "⭐️ — избранный контрагент",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )


@router.message(Command("deals"))
async def cmd_deals(message: Message, state: FSMContext):
    """Команда /deals - Мои сделки"""
    # Выходим из любого состояния FSM
    current_state = await state.get_state()
    if current_state:
        await state.clear()
    await message.answer(
        "✨ <b>Мои сделки</b> ✨",
        reply_markup=my_deals_kb(),
        parse_mode="HTML"
    )




@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда /stats - личная статистика"""
    await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    
    user = await get_user(message.from_user.id)
    stats = await get_user_statistics(message.from_user.id)
    
    if not user or not stats:
        await message.answer("❌ <b>Ошибка загрузки статистики</b>", parse_mode="HTML")
        return
    
    profile_text = await format_profile_info(user, stats)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💳 Мои реквизиты", callback_data="templates:payment"),
            InlineKeyboardButton(text="💼 BTC-Кошелек", callback_data="profile:btc_wallet")
        ],
        [
            InlineKeyboardButton(text="📝 Мои отзывы", callback_data="profile:reviews"),
            InlineKeyboardButton(text="📊 Публичный профиль", callback_data=f"profile:public:{message.from_user.id}")
        ],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])
    
    await message.answer(profile_text, reply_markup=kb, parse_mode="HTML")


@router.message(Command("btc"))
async def cmd_btc(message: Message):
    """Команда /btc - курс BTC"""
    try:
        btc_usd = await get_btc_usd_rate()
        usd_rub = await get_usd_rub_rate()
        btc_rub = await get_btc_rub_rate()
        
        if btc_usd and usd_rub and btc_rub:
            text = (
                f"💹 <b>Курс BTC</b>\n\n"
                f"💵 <b>BTC/USD:</b> ${btc_usd:,.2f}\n"
                f"💰 <b>BTC/RUB:</b> {btc_rub:,.0f} ₽\n"
            )
        elif btc_usd:
            text = (
                f"💹 <b>Курс BTC</b>\n\n"
                f"💵 <b>BTC/USD:</b> ${btc_usd:,.2f}\n"
            )
        else:
            text = "❌ <b>Не удалось получить курс BTC</b>"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка получения курса BTC: {e}")
        await message.answer("❌ <b>Ошибка получения курса BTC</b>", parse_mode="HTML")


@router.message(Command("c"))
async def cmd_calculator(message: Message):
    """Команда /c [сумма] - калькулятор конвертации рублей в BTC"""
    try:
        # Получаем аргументы команды
        command_text = message.text or ""
        parts = command_text.split(maxsplit=1)
        
        if len(parts) < 2:
            await message.answer(
                "❌ <b>Неверный формат команды</b>\n\n"
                "💡 <b>Использование:</b> /c [сумма в рублях]\n\n"
                "📝 <b>Пример:</b> /c 1000",
                parse_mode="HTML"
            )
            return
        
        # Парсим сумму в рублях
        try:
            rub_amount_str = parts[1].strip().replace(",", ".").replace(" ", "")
            rub_amount = Decimal(rub_amount_str)
            
            if rub_amount <= 0:
                await message.answer("❌ Сумма должна быть больше нуля", parse_mode="HTML")
                return
        except (ValueError, InvalidOperation):
            await message.answer(
                "❌ <b>Неверный формат суммы</b>\n\n"
                "💡 <b>Используйте числа, например:</b> 1000 или 1 000",
                parse_mode="HTML"
            )
            return
        
        # Получаем курсы
        btc_rub_rate = await get_btc_rub_rate()
        btc_usd_rate = await get_btc_usd_rate()
        
        if not btc_rub_rate or btc_rub_rate <= 0:
            await message.answer("❌ <b>Не удалось получить курс BTC</b>", parse_mode="HTML")
            return
        
        # Конвертируем рубли в BTC
        btc_amount = rub_to_btc(rub_amount, btc_rub_rate)
        
        # Форматируем BTC сумму (до 8 знаков после запятой)
        btc_formatted = f"{btc_amount:.8f}".rstrip('0').rstrip('.')
        
        # Конвертируем в USD, если курс доступен
        if btc_usd_rate and btc_usd_rate > 0:
            usd_amount = btc_amount * btc_usd_rate
            usd_formatted = f"{usd_amount:.2f}".rstrip('0').rstrip('.')
            # Округляем до целого числа для отображения
            usd_display = f"{int(float(usd_formatted))}"
        else:
            usd_display = "N/A"
        
        # Форматируем сумму в рублях (с пробелами для тысяч)
        rub_formatted = f"{rub_amount:,.0f}".replace(",", " ")
        
        # Формируем текст ответа
        text = (
            f"💼 {btc_formatted} BTC\n"
            f"└ 🇺🇸 {usd_display} USD\n"
            f"└ 🇷🇺 {rub_formatted} RUB\n\n"
            f"💻 По курсу B."
        )
        
        await message.answer(text)
        
    except Exception as e:
        logger.error(f"Ошибка в калькуляторе: {e}", exc_info=True)
        await message.answer(
            "❌ <b>Ошибка при конвертации</b>\n\n"
            "Попробуйте позже или проверьте правильность ввода суммы.",
            parse_mode="HTML"
        )


@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext):
    """Команда /search [ID] - найти сделку по ID"""
    await state.clear()
    args = message.text.split()[1:] if message.text else []
    
    if not args:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            "🔍 <b>Поиск сделки</b>\n\n"
            "Использование: /search [ID]\n\n"
            "Пример: /search 123",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return
    
    try:
        deal_id = int(args[0])
        deal = await get_deal(deal_id)
        
        if not deal:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
            ])
            await message.answer(
                f"❌ Сделка #{deal_id} не найдена",
                reply_markup=kb
            )
            return
        
        from handlers.deal_handlers import deal_view_text
        text = await deal_view_text(deal_id, message.from_user.id)
        
        from handlers.deal_handlers import deal_view_kb
        kb = await deal_view_kb(deal_id, message.from_user.id)
        
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    except ValueError:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            "❌ Неверный ID сделки",
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"Ошибка поиска сделки: {e}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        await message.answer(
            "❌ Ошибка при поиске сделки",
            reply_markup=kb
        )


@router.message(Command("test_deposit"))
async def cmd_test_deposit(message: Message):
    """Тестовая команда для проверки депозита (только для админов)"""
    from config import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        return
    
    args = message.text.split()[1:] if message.text else []
    if len(args) < 2:
        await message.answer("Использование: /test_deposit @username 0.001")
        return
        
    username = args[0].lstrip("@")
    try:
        amount = Decimal(str(args[1]).replace(",", "."))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("❌ Некорректная сумма. Пример: /test_deposit @username 0.001")
        return
    
    user = await find_user_by_username(username)
    if not user:
        await message.answer(f"Пользователь @{username} не найден")
        return
    
    # Находим сделку пользователя со статусом AWAITING_DEPOSIT
    from database import find_deal_by_username_and_status
    deal = await find_deal_by_username_and_status(username, DealStatus.AWAITING_DEPOSIT)
    if not deal:
        await message.answer("❌ Не найдена подходящая сделка со статусом AWAITING_DEPOSIT")
        return
    
    deal_id = deal["id"]
    await confirm_deposit(deal_id)
    await message.answer(f"✅ Депозит подтверждён для сделки #{deal_id}")


# ==================== ОБРАБОТЧИКИ ГЛАВНОГО МЕНЮ ====================

@router.callback_query(F.data == "main:back")
async def main_back(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню (старая версия для совместимости)"""
    await state.clear()
    welcome_text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "🤖 Я P2P бот для безопасного обмена BTC\n\n"
        "💡 <b>Выберите действие из меню:</b>"
    )
    try:
        await callback.message.edit_text(
            welcome_text,
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            welcome_text,
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "menu:back")
async def menu_back(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    welcome_text = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "🤖 Я P2P бот для безопасного обмена BTC\n\n"
        "💡 <b>Выберите действие из меню:</b>"
    )
    try:
        await callback.message.edit_text(
            welcome_text,
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            welcome_text,
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
    await callback.answer()
    

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

async def build_buy_list_page(deals: list, page: int = 1, per_page: int = 8, user_id: int = None, sort_mode: str = ""):
    """
    Формирует страницу со списком заявок на продажу с пагинацией (формат как в истории сделок)
    
    Args:
        deals: Список всех заявок
        page: Номер страницы (начиная с 1)
        per_page: Количество заявок на странице
        user_id: ID пользователя для определения избранных контрагентов
        sort_mode: Режим сортировки: "" (по умолчанию), "asc" (⬇️ от малой к большой), "desc" (⬆️ от большой к малой)
    
    Returns:
        tuple: (text, keyboard)
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from decimal import Decimal
    from database import calculate_seller_amounts, get_favorite_partners
    from utils import get_btc_rub_rate, btc_to_rub
    
    # Сортируем заявки по цене (max_btc) в зависимости от режима сортировки
    if sort_mode == "asc":
        # Сортировка от маленькой цены к высокой
        deals = sorted(deals, key=lambda d: Decimal(d["max_btc"]))
    elif sort_mode == "desc":
        # Сортировка от высокой цены к маленькой
        deals = sorted(deals, key=lambda d: Decimal(d["max_btc"]), reverse=True)
    # Если sort_mode == "", оставляем исходный порядок
    
    total_deals = len(deals)
    total_pages = (total_deals + per_page - 1) // per_page  # Округление вверх
    
    # Ограничиваем номер страницы
    if page < 1:
        page = 1
    if page > total_pages and total_pages > 0:
        page = total_pages
    
    if total_deals == 0:
        text = "📭 <b>Заявок на продажу пока нет</b>\n\n💡 <b>Создайте заявку на покупку, чтобы найти продавцов!</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        return text, kb
    
    # Получаем избранных контрагентов
    favorites = await get_favorite_partners(user_id) if user_id else []
    favorite_ids = {f["partner_id"] for f in favorites}
    
    # Получаем курс BTC к рублям
    btc_rub_rate = await get_btc_rub_rate()
    
    # Вычисляем индексы для текущей страницы (страницы начинаются с 1)
    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, total_deals)
    page_deals = deals[start_idx:end_idx]
    
    # Формируем кнопки для заявок на текущей странице
    rows = []
    for d in page_deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        
        # Рассчитываем сумму в рублях (продавец получит max_btc)
        seller_receive_btc = max_btc
        seller_receive_rub = btc_to_rub(seller_receive_btc, btc_rub_rate)
        seller_receive_rub_formatted = f"{seller_receive_rub:,.0f}".replace(",", " ")
        seller_receive_btc_formatted = f"{seller_receive_btc:.8f}".rstrip('0').rstrip('.')
        
        # Отмечаем избранных звёздочкой
        star_prefix = "⭐️•" if d["owner_id"] in favorite_ids else ""
        label = f"{star_prefix}💸•{seller_receive_btc_formatted} BTC•({seller_receive_rub_formatted} Руб)"
        
        rows.append([InlineKeyboardButton(text=label, callback_data=f"deal:view:{d['id']}")])
    
    # Кнопки навигации (формат как в истории сделок)
    nav_buttons = []
    
    # Кнопка "Назад" (предыдущая страница)
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="<<", callback_data=f"buy:page:{page - 1}:{sort_mode}"))
    
    # Информация о странице с сортировкой
    # Определяем иконку в зависимости от режима сортировки
    if sort_mode == "asc":
        sort_icon = "⬇️"
    elif sort_mode == "desc":
        sort_icon = "⬆️"
    else:
        sort_icon = "📄"
    
    # Определяем следующий режим сортировки для переключения
    if sort_mode == "":
        next_sort = "asc"
    elif sort_mode == "asc":
        next_sort = "desc"
    else:  # sort_mode == "desc"
        next_sort = ""
    
    nav_buttons.append(InlineKeyboardButton(
        text=f"{sort_icon} {page}/{total_pages}", 
        callback_data=f"buy:page:info:{page}:{next_sort}"
    ))
    
    # Кнопка "Вперед" (следующая страница)
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text=">>", callback_data=f"buy:page:{page + 1}:{sort_mode}"))
    
    if nav_buttons:
        rows.append(nav_buttons)
    
    # Кнопка "Главное меню"
    rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")])
    
    # Формируем текст с информацией о количестве заявок
    text = f"🚀 <b>Активных заявок на продажу: {total_deals}</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb


@router.callback_query(F.data == "menu:p2p")
async def menu_p2p(callback: CallbackQuery, state: FSMContext):
    """Меню P2P"""
    await state.clear()
    text = "🤝 <b>P2P Маркет</b>\n\nВыберите нужное действие:"
    try:
        await callback.message.edit_text(text, reply_markup=p2p_menu_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=p2p_menu_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "menu:buy")
async def menu_buy(callback: CallbackQuery, state: FSMContext):
    """Меню: Купить - показывает первую страницу заявок на продажу"""
    await state.clear()
    await callback.answer()
    
    # Переходим на первую страницу без сортировки
    await _show_buy_list_page(callback, 1, sort_mode="")


@router.callback_query(F.data.startswith("buy:page:"))
async def buy_page(callback: CallbackQuery, state: FSMContext):
    """Переключение страницы в списке заявок на продажу или изменение сортировки"""
    try:
        parts = callback.data.split(":")
        if len(parts) >= 3 and parts[2] == "info":
            # Изменение сортировки
            # Формат: buy:page:info:{page}:{next_sort}
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            sort_mode = parts[4] if len(parts) > 4 else ""
            await callback.answer()
            await _show_buy_list_page(callback, page, sort_mode)
            return
        
        # Переключение страницы
        if len(parts) >= 3:
            page = int(parts[2])
            sort_mode = parts[3] if len(parts) > 3 else ""
        else:
            page = 1
            sort_mode = ""
        
        if page < 1:
            page = 1
        if page > 100:
            page = 100
    except (ValueError, IndexError):
        page = 1
        sort_mode = ""
    
    await callback.answer()
    await _show_buy_list_page(callback, page, sort_mode)


async def _show_buy_list_page(callback: CallbackQuery, page: int, sort_mode: str = ""):
    """Показать страницу списка заявок на продажу (формат как в истории сделок)"""
    from database import list_active_deals_by_side
    
    DEALS_PER_PAGE = 8
    MAX_PAGES = 100
    
    logger.info(f"📋 Запрос активных заявок на продажу для пользователя {callback.from_user.id}, страница {page}, сортировка: {sort_mode}")
    
    # Получаем все заявки
    deals = await list_active_deals_by_side("SELL", exclude_owner_id=callback.from_user.id, limit=1000)
    logger.info(f"📋 Получено {len(deals)} активных заявок на продажу")
    
    # Формируем страницу с учетом сортировки
    text, kb = await build_buy_list_page(deals, page=page, user_id=callback.from_user.id, sort_mode=sort_mode)
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при редактировании сообщения: {e}", exc_info=True)
        await callback.message.answer(
            text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    

@router.callback_query(F.data == "menu:sell")
async def menu_sell(callback: CallbackQuery, state: FSMContext):
    """Меню: Продать"""
    await state.clear()
    deals = await list_active_deals_by_side("BUY", exclude_owner_id=callback.from_user.id, limit=10)
    
    if not deals:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        try:
            await callback.message.edit_text(
                "📭 <b>Заявок на покупку пока нет</b>\n\n"
                "💡 <b>Создайте заявку на продажу, чтобы другие пользователи могли её увидеть!</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                "📭 <b>Заявок на покупку пока нет</b>\n\n"
                "💡 <b>Создайте заявку на продажу, чтобы другие пользователи могли её увидеть!</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        await callback.answer()
        return
    
    # Получаем избранных контрагентов
    favorites = await get_favorite_partners(callback.from_user.id)
    favorite_ids = {f["partner_id"] for f in favorites}
    
    from datetime import datetime
    from decimal import Decimal
    from utils import get_btc_rub_rate, btc_to_rub
    
    # Получаем курс BTC к рублям
    btc_rub_rate = await get_btc_rub_rate()
    
    rows = []
    for d in deals:
        min_btc = Decimal(d["min_btc"])
        max_btc = Decimal(d["max_btc"])
        
        # Рассчитываем сумму в рублях (продавец получит max_btc)
        seller_receive_btc = max_btc
        seller_receive_rub = btc_to_rub(seller_receive_btc, btc_rub_rate)
        seller_receive_rub_formatted = f"{seller_receive_rub:,.0f}".replace(",", " ")
        seller_receive_btc_formatted = f"{seller_receive_btc:.8f}".rstrip('0').rstrip('.')
        
        # Отмечаем избранных звёздочкой
        star_prefix = "⭐️•" if d["owner_id"] in favorite_ids else ""
        label = f"{star_prefix}💸•{seller_receive_btc_formatted} BTC•({seller_receive_rub_formatted} Руб)"
        
        rows.append([InlineKeyboardButton(text=label, callback_data=f"deal:view:{d['id']}")])
    
    rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")])
    
    try:
        await callback.message.edit_text(
            "✨ <b>Заявки на покупку</b> ✨\n\n"
        "⭐️ — избранный контрагент",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Заявки на покупку</b> ✨\n\n"
            "⭐️ — избранный контрагент",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "menu:create")
async def menu_create(callback: CallbackQuery, state: FSMContext):
    """Меню: Создать объявление"""
    await state.clear()
    try:
        await callback.message.edit_text(
            "✨ <b>Создание заявки</b> ✨",
            reply_markup=sell_or_buy_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Создание заявки</b> ✨",
            reply_markup=sell_or_buy_kb(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "menu:deals")
async def menu_deals(callback: CallbackQuery, state: FSMContext):
    """Меню: Мои сделки"""
    await state.clear()
    try:
        await callback.message.edit_text(
            "✨ <b>Мои сделки</b> ✨",
            reply_markup=my_deals_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Мои сделки</b> ✨",
            reply_markup=my_deals_kb(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "mydeals:back")
async def mydeals_back(callback: CallbackQuery, state: FSMContext):
    """Возврат к меню 'Мои сделки'"""
    await state.clear()
    try:
        await callback.message.edit_text(
            "✨ <b>Мои сделки</b> ✨",
            reply_markup=my_deals_kb(),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Мои сделки</b> ✨",
            reply_markup=my_deals_kb(),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "mydeals:active")
async def mydeals_active(callback: CallbackQuery, state: FSMContext):
    """Активные сделки пользователя"""
    await state.clear()
    await callback.answer()
    
    deals = await get_user_active_deals(callback.from_user.id, exclude_garant=True)
    
    if not deals:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:back")]
        ])
        try:
            await callback.message.edit_text(
                "📭 <b>У вас нет активных сделок</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                "📭 <b>У вас нет активных сделок</b>",
                reply_markup=kb,
                parse_mode="HTML"
        )
        return
    
    from keyboards import my_deals_list_keyboard
    kb = await my_deals_list_keyboard(deals)
    # Добавляем кнопку "Назад"
    kb.inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:back")])
    
    try:
        await callback.message.edit_text(
            "✨ <b>Ваши активные сделки</b> ✨\n\n"
            "📋 <b>Выберите сделку для просмотра:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Ваши активные сделки</b> ✨\n\n"
            "📋 <b>Выберите сделку для просмотра:</b>",
            reply_markup=kb,
            parse_mode="HTML"
    )


@router.callback_query(F.data == "mydeals:history")
async def mydeals_history(callback: CallbackQuery, state: FSMContext):
    """История сделок пользователя (первая страница)"""
    await state.clear()
    await callback.answer()
    
    # Переходим на первую страницу
    await _show_history_page(callback, 1)


@router.callback_query(F.data.startswith("mydeals:history:page:"))
async def mydeals_history_page(callback: CallbackQuery, state: FSMContext):
    """История сделок пользователя (конкретная страница)"""
    try:
        page = int(callback.data.split(":")[-1])
        if page < 1:
            page = 1
        if page > 100:
            page = 100
    except (ValueError, IndexError):
        page = 1
    
    await callback.answer()
    await _show_history_page(callback, page)


@router.callback_query(F.data == "mydeals:history:info")
async def mydeals_history_info(callback: CallbackQuery):
    """Информация о странице (не делает ничего, просто показывает текущую страницу)"""
    await callback.answer()


async def _show_history_page(callback: CallbackQuery, page: int):
    """Показать страницу истории сделок"""
    from database import get_user_history_deals, get_user_history_deals_count
    from keyboards import history_list_keyboard
    
    DEALS_PER_PAGE = 8
    MAX_PAGES = 100
    
    # Получаем общее количество сделок
    total_deals = await get_user_history_deals_count(callback.from_user.id, exclude_garant=True)
    
    if total_deals == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:back")]
        ])
        try:
            await callback.message.edit_text(
                "📭 <b>У вас нет завершённых сделок</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                "📭 <b>У вас нет завершённых сделок</b>",
                reply_markup=kb,
                parse_mode="HTML"
        )
        return
    
    # Рассчитываем количество страниц
    total_pages = min((total_deals + DEALS_PER_PAGE - 1) // DEALS_PER_PAGE, MAX_PAGES)
    
    # Ограничиваем номер страницы
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    
    # Получаем сделки для текущей страницы
    offset = (page - 1) * DEALS_PER_PAGE
    deals = await get_user_history_deals(callback.from_user.id, limit=DEALS_PER_PAGE, offset=offset, exclude_garant=True)
    
    # Создаем клавиатуру с пагинацией
    kb = history_list_keyboard(deals, page=page, total_pages=total_pages)
    
    # Добавляем кнопку "Назад" в главное меню
    kb.inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:back")])
    
    # Формируем текст с информацией о странице
    text = "✨ <b>История ваших сделок</b> ✨"
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=kb,
            parse_mode="HTML"
        )


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery, state: FSMContext):
    """Меню: Помощь"""
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Комиссии", callback_data="help:fees")],
        [InlineKeyboardButton(text="🔒 Безопасность", callback_data="help:safety")],
        [InlineKeyboardButton(text="⚖️ Советы", callback_data="help:tips")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="help:contacts")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="help:faq")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])
    try:
        await callback.message.edit_text(
            "✨ <b>Справка</b> ✨\n\n"
            "📋 <b>Выберите раздел, который вас интересует:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Справка</b> ✨\n\n"
            "📋 <b>Выберите раздел, который вас интересует:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery, state: FSMContext):
    """Меню: Профиль"""
    await state.clear()
    await upsert_user(callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
    
    user = await get_user(callback.from_user.id)
    stats = await get_user_statistics(callback.from_user.id)
    
    if not user or not stats:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        try:
            await callback.message.edit_text(
                "❌ Ошибка загрузки профиля",
                reply_markup=kb
            )
        except Exception:
            await callback.message.answer(
                "❌ Ошибка загрузки профиля",
                reply_markup=kb
            )
        await callback.answer()
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


@router.callback_query(F.data == "profile:btc_wallet")
async def profile_btc_wallet(callback: CallbackQuery, state: FSMContext):
    """Просмотр и изменение BTC кошелька"""
    user = await get_user(callback.from_user.id)
    btc_wallet = user.get("btc_wallet") if user else None
    
    if btc_wallet:
        text = (
            f"💼 <b>BTC-Кошелек</b>\n\n"
            f"📋 <b>Текущий кошелек:</b>\n"
            f"<code>{btc_wallet}</code>\n\n"
            f"💡 Вы можете изменить кошелек, нажав кнопку ниже."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить кошелек", callback_data="profile:btc_wallet:edit")],
            [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
        ])
    else:
        text = (
            f"💼 <b>BTC-Кошелек</b>\n\n"
            f"⚠️ <b>BTC кошелек не указан</b>\n\n"
            f"💡 Укажите ваш BTC кошелек для использования в сделках."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить кошелек", callback_data="profile:btc_wallet:edit")],
            [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
        ])
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "profile:btc_wallet:edit")
async def profile_btc_wallet_edit(callback: CallbackQuery, state: FSMContext):
    """Начало изменения BTC кошелька"""
    await state.set_state(ProfileStates.WAIT_BTC_WALLET)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="profile:btc_wallet")]
    ])
    
    text = (
        "💼 <b>Изменение BTC кошелька</b>\n\n"
        "📋 <b>Укажите ваш BTC кошелек:</b>\n\n"
        "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
        "💡 <b>Пример:</b> <code>bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh</code>\n\n"
        "⚠️ <b>Важно:</b> Этот кошелек будет использоваться во всех сделках, где требуется BTC адрес"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(ProfileStates.WAIT_BTC_WALLET)
async def profile_btc_wallet_save(message: Message, state: FSMContext):
    """Сохранение нового BTC кошелька"""
    if not message.text:
        await message.answer(
            "💼 <b>Укажите ваш BTC кошелек текстом:</b>",
            parse_mode="HTML"
        )
        return
    wallet = message.text.strip()
    
    # Валидация BTC адреса
    if not is_valid_btc_bech32(wallet) and not (wallet.startswith("1") or wallet.startswith("3") or wallet.startswith("bc1")):
        await message.answer(
            "❌ <b>Неверный формат BTC адреса</b>\n\n"
            "💼 <b>Укажите ваш BTC кошелек:</b>\n\n"
            "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
            "💡 <b>Пример:</b> <code>bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh</code>",
            parse_mode="HTML"
        )
        return
    
    # Сохраняем BTC кошелек
    await update_user_btc_wallet(message.from_user.id, wallet)
    
    # Очищаем состояние
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
    ])
    
    await message.answer(
        f"✅ <b>BTC кошелек успешно изменен!</b>\n\n"
        f"💼 <b>Новый кошелек:</b> <code>{wallet}</code>\n\n"
        f"💡 Кошелек будет использоваться во всех новых сделках.",
        parse_mode="HTML",
        reply_markup=kb
    )


@router.callback_query(F.data == "mydeals:history")
async def mydeals_history(callback: CallbackQuery, state: FSMContext):
    """История сделок пользователя (первая страница)"""
    await state.clear()
    await callback.answer()
    
    # Переходим на первую страницу
    await _show_history_page(callback, 1)


@router.callback_query(F.data.startswith("mydeals:history:page:"))
async def mydeals_history_page(callback: CallbackQuery, state: FSMContext):
    """История сделок пользователя (конкретная страница)"""
    try:
        page = int(callback.data.split(":")[-1])
        if page < 1:
            page = 1
        if page > 100:
            page = 100
    except (ValueError, IndexError):
        page = 1
    
    await callback.answer()
    await _show_history_page(callback, page)


@router.callback_query(F.data == "mydeals:history:info")
async def mydeals_history_info(callback: CallbackQuery):
    """Информация о странице (не делает ничего, просто показывает текущую страницу)"""
    await callback.answer()


async def _show_history_page(callback: CallbackQuery, page: int):
    """Показать страницу истории сделок"""
    from database import get_user_history_deals, get_user_history_deals_count
    from keyboards import history_list_keyboard
    
    DEALS_PER_PAGE = 8
    MAX_PAGES = 100
    
    # Получаем общее количество сделок
    total_deals = await get_user_history_deals_count(callback.from_user.id)
    
    if total_deals == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:back")]
        ])
        try:
            await callback.message.edit_text(
                "📭 <b>У вас нет завершённых сделок</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                "📭 <b>У вас нет завершённых сделок</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )
        return
    
    # Рассчитываем количество страниц
    total_pages = min((total_deals + DEALS_PER_PAGE - 1) // DEALS_PER_PAGE, MAX_PAGES)
    
    # Ограничиваем номер страницы
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    
    # Получаем сделки для текущей страницы
    offset = (page - 1) * DEALS_PER_PAGE
    deals = await get_user_history_deals(callback.from_user.id, limit=DEALS_PER_PAGE, offset=offset)
    
    # Создаем клавиатуру с пагинацией
    kb = history_list_keyboard(deals, page=page, total_pages=total_pages)
    
    # Добавляем кнопку "Назад" в главное меню
    kb.inline_keyboard.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mydeals:back")])
    
    # Формируем текст с информацией о странице
    text = "✨ <b>История ваших сделок</b> ✨"
    
    try:
        await callback.message.edit_text(
            text,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=kb,
            parse_mode="HTML"
        )


@router.callback_query(F.data == "menu:help")
async def menu_help(callback: CallbackQuery, state: FSMContext):
    """Меню: Помощь"""
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Комиссии", callback_data="help:fees")],
        [InlineKeyboardButton(text="🔒 Безопасность", callback_data="help:safety")],
        [InlineKeyboardButton(text="⚖️ Советы", callback_data="help:tips")],
        [InlineKeyboardButton(text="📞 Контакты", callback_data="help:contacts")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="help:faq")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])
    try:
        await callback.message.edit_text(
            "✨ <b>Справка</b> ✨\n\n"
            "📋 <b>Выберите раздел, который вас интересует:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "✨ <b>Справка</b> ✨\n\n"
            "📋 <b>Выберите раздел, который вас интересует:</b>",
            reply_markup=kb,
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def menu_profile(callback: CallbackQuery, state: FSMContext):
    """Меню: Профиль"""
    await state.clear()
    await upsert_user(callback.from_user.id, callback.from_user.username, callback.from_user.full_name)
    
    user = await get_user(callback.from_user.id)
    stats = await get_user_statistics(callback.from_user.id)
    
    if not user or not stats:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
        ])
        try:
            await callback.message.edit_text(
                "❌ Ошибка загрузки профиля",
                reply_markup=kb
            )
        except Exception:
            await callback.message.answer(
                "❌ Ошибка загрузки профиля",
                reply_markup=kb
            )
        await callback.answer()
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


@router.callback_query(F.data == "profile:btc_wallet")
async def profile_btc_wallet(callback: CallbackQuery, state: FSMContext):
    """Просмотр и изменение BTC кошелька"""
    user = await get_user(callback.from_user.id)
    btc_wallet = user.get("btc_wallet") if user else None
    
    if btc_wallet:
        text = (
            f"💼 <b>BTC-Кошелек</b>\n\n"
            f"📋 <b>Текущий кошелек:</b>\n"
            f"<code>{btc_wallet}</code>\n\n"
            f"💡 Вы можете изменить кошелек, нажав кнопку ниже."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить кошелек", callback_data="profile:btc_wallet:edit")],
            [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
        ])
    else:
        text = (
            f"💼 <b>BTC-Кошелек</b>\n\n"
            f"⚠️ <b>BTC кошелек не указан</b>\n\n"
            f"💡 Укажите ваш BTC кошелек для использования в сделках."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить кошелек", callback_data="profile:btc_wallet:edit")],
            [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
        ])
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "profile:btc_wallet:edit")
async def profile_btc_wallet_edit(callback: CallbackQuery, state: FSMContext):
    """Начало изменения BTC кошелька"""
    await state.set_state(ProfileStates.WAIT_BTC_WALLET)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="profile:btc_wallet")]
    ])
    
    text = (
        "💼 <b>Изменение BTC кошелька</b>\n\n"
        "📋 <b>Укажите ваш BTC кошелек:</b>\n\n"
        "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
        "💡 <b>Пример:</b> <code>bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh</code>\n\n"
        "⚠️ <b>Важно:</b> Этот кошелек будет использоваться во всех сделках, где требуется BTC адрес"
    )
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.message(ProfileStates.WAIT_BTC_WALLET)
async def profile_btc_wallet_save(message: Message, state: FSMContext):
    """Сохранение нового BTC кошелька"""
    if not message.text:
        await message.answer(
            "💼 <b>Укажите ваш BTC кошелек текстом:</b>",
            parse_mode="HTML"
        )
        return
    wallet = message.text.strip()
    
    # Валидация BTC адреса
    if not is_valid_btc_bech32(wallet) and not (wallet.startswith("1") or wallet.startswith("3") or wallet.startswith("bc1")):
        await message.answer(
            "❌ <b>Неверный формат BTC адреса</b>\n\n"
            "💼 <b>Укажите ваш BTC кошелек:</b>\n\n"
            "📌 <b>Формат:</b> <code>bc1...</code> или <code>1...</code> или <code>3...</code>\n\n"
            "💡 <b>Пример:</b> <code>bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh</code>",
            parse_mode="HTML"
        )
        return
    
    # Сохраняем BTC кошелек
    await update_user_btc_wallet(message.from_user.id, wallet)
    
    # Очищаем состояние
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к профилю", callback_data="profile:back")]
    ])
    
    await message.answer(
        f"✅ <b>BTC кошелек успешно изменен!</b>\n\n"
        f"💼 <b>Новый кошелек:</b> <code>{wallet}</code>\n\n"
        f"💡 Кошелек будет использоваться во всех новых сделках.",
        parse_mode="HTML",
        reply_markup=kb
    )
