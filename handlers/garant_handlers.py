"""
Автогарант: сделка между двумя пользователями через обычный депозит.
"""
import logging
import re
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ESCROW_BTC_ADDRESS, TTL_HOURS, DealStatus
from database import (
    NUM_RE,
    create_deal,
    db,
    get_deal,
    get_user,
    is_user_registered,
    send_notification,
    update_deal_status,
    upsert_user,
)
from utils import btc_to_rub, get_btc_rub_rate, rub_to_btc

logger = logging.getLogger(__name__)
router = Router()

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")


class GarantStates(StatesGroup):
    WAIT_USER = State()
    WAIT_AMOUNT = State()


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="garant:cancel")]
    ])


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu:back")]
    ])


def _owner_decision_kb(deal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить получение фиата", callback_data=f"garant:fiat_ok:{deal_id}")],
        [InlineKeyboardButton(text="🔴 Отменить сделку", callback_data=f"garant_auto:request_cancel:{deal_id}")],
        [InlineKeyboardButton(text="⚠️ Оспорить", callback_data=f"deal:dispute:{deal_id}")],
    ])


async def _find_user(username: str) -> dict | None:
    async with db() as conn:
        cur = await conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


def _fmt_btc(amount: Decimal) -> str:
    return f"{amount:.8f}".rstrip("0").rstrip(".")


@router.callback_query(F.data == "garant:start")
async def garant_start(callback: CallbackQuery, state: FSMContext):
    if not await is_user_registered(callback.from_user.id):
        await callback.answer("Сначала завершите регистрацию через /start", show_alert=True)
        return
    await upsert_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.full_name,
    )
    await state.set_state(GarantStates.WAIT_USER)
    await state.update_data()
    text = (
        "🛡 <b>Автогарант</b>\n\n"
        "Укажите username второго участника.\n\n"
        "Пример: <code>@username</code>\n\n"
        "Пользователь должен хотя бы раз открыть этого бота."
    )
    try:
        await callback.message.edit_text(text, reply_markup=_cancel_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=_cancel_kb(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "garant:cancel")
async def garant_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = "❌ Автогарант отменён."
    try:
        await callback.message.edit_text(text, reply_markup=_menu_kb(), parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=_menu_kb(), parse_mode="HTML")
    await callback.answer()


@router.message(GarantStates.WAIT_USER)
async def garant_username(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправьте username текстом.", reply_markup=_cancel_kb())
        return
    raw = message.text.strip()
    if raw.lower() in {"отмена", "cancel", "❌ отмена"} or raw.startswith("/"):
        await state.clear()
        await message.answer("❌ Автогарант отменён.", reply_markup=_menu_kb())
        return

    username = raw.lstrip("@").strip()
    if not _USERNAME_RE.match(username):
        await message.answer(
            "❌ Неверный username.\n\nПример: <code>@username</code>",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
        return

    partner = await _find_user(username)
    if not partner:
        await message.answer(
            f"❌ Пользователь @{escape(username)} не найден.\n\n"
            "Он должен открыть бота командой /start.",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
        return
    if partner["tg_id"] == message.from_user.id:
        await message.answer(
            "❌ Нельзя создать автогарант с самим собой.",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
        return

    await state.set_state(GarantStates.WAIT_AMOUNT)
    await state.update_data(
        partner_id=partner["tg_id"],
        partner_username=partner.get("username") or username,
    )
    await message.answer(
        f"👤 Участник: @{escape(partner.get('username') or username)}\n\n"
        "Укажите сумму депозита в BTC или в рублях.\n\n"
        "Примеры:\n"
        "• <code>0.001</code> или <code>0.001 btc</code>\n"
        "• <code>5000</code> или <code>5000 руб</code>",
        reply_markup=_cancel_kb(),
        parse_mode="HTML",
    )


@router.message(GarantStates.WAIT_AMOUNT)
async def garant_amount(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправьте сумму текстом.", reply_markup=_cancel_kb())
        return
    raw = message.text.strip()
    if raw.lower() in {"отмена", "cancel", "❌ отмена"} or raw.startswith("/"):
        await state.clear()
        await message.answer("❌ Автогарант отменён.", reply_markup=_menu_kb())
        return

    lower = raw.lower().replace("ё", "е")
    marked_btc = bool(re.search(r"btc|биткоин|биткойн", lower))
    marked_rub = bool(re.search(r"руб|rub|₽|(?:\d)\s*р\b", lower))
    if marked_btc and marked_rub:
        await message.answer(
            "❌ Укажите одну валюту: BTC или рубли.",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
        return

    text_clean = lower
    for word in ("биткоин", "биткойн", "btc", "рублей", "рубля", "руб", "rub", "₽"):
        text_clean = text_clean.replace(word, "")
    text_clean = text_clean.replace(",", ".").replace(" ", "").replace("\u00A0", "")
    text_clean = re.sub(r"[^0-9.]", "", text_clean)
    nums = NUM_RE.findall(text_clean)
    if len(nums) != 1:
        await message.answer(
            "❌ Нужна одна сумма.\n\n"
            "Примеры: <code>0.001</code> или <code>5000 руб</code>",
            reply_markup=_cancel_kb(),
            parse_mode="HTML",
        )
        return
    try:
        entered = Decimal(nums[0])
    except InvalidOperation:
        await message.answer("❌ Некорректная сумма.", reply_markup=_cancel_kb())
        return
    if entered <= 0:
        await message.answer("❌ Сумма должна быть больше нуля.", reply_markup=_cancel_kb())
        return

    q = Decimal("0.00000001")
    is_rub = marked_rub or (not marked_btc and entered >= 1)
    if is_rub:
        btc_rub_rate = await get_btc_rub_rate()
        if not btc_rub_rate or btc_rub_rate <= 0:
            await message.answer("❌ Не удалось получить курс BTC. Введите сумму в BTC.", reply_markup=_cancel_kb())
            return
        amount = rub_to_btc(entered, btc_rub_rate).quantize(q)
        rub_entered = entered
    else:
        amount = entered.quantize(q)
        rub_entered = None
    if amount <= 0:
        await message.answer("❌ Сумма слишком маленькая.", reply_markup=_cancel_kb())
        return
    data = await state.get_data()
    partner_id = data.get("partner_id")
    partner_username = data.get("partner_username") or "user"
    if not partner_id:
        await state.clear()
        await message.answer("❌ Сессия истекла. Начните автогарант заново.", reply_markup=_menu_kb())
        return

    try:
        deal_id = await create_deal(
            owner_id=message.from_user.id,
            min_btc=amount,
            max_btc=amount,
            escrow_address=ESCROW_BTC_ADDRESS,
            side="GARANT",
            status=DealStatus.AWAITING_DEPOSIT,
            partner_id=int(partner_id),
        )
    except Exception as e:
        logger.error("Ошибка создания автогаранта: %s", e, exc_info=True)
        await message.answer("❌ Не удалось создать сделку. Попробуйте позже.", reply_markup=_menu_kb())
        await state.clear()
        return

    await state.clear()
    amount_str = _fmt_btc(amount)
    if rub_entered is None:
        btc_rub_rate = await get_btc_rub_rate()
        rub = btc_to_rub(amount, btc_rub_rate)
        rub_str = f"{rub:,.0f}".replace(",", " ")
        money_line = f"<b>{amount_str} BTC</b> (~{rub_str} руб)"
    else:
        if rub_entered == rub_entered.to_integral_value():
            rub_str = f"{rub_entered:,.0f}".replace(",", " ")
        else:
            rub_str = f"{rub_entered:,.2f}".replace(",", " ")
        money_line = f"<b>{rub_str} руб</b> ({amount_str} BTC)"
    initiator = message.from_user.username

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Депозит внесён", callback_data=f"garant:deposit_done:{deal_id}")],
        [InlineKeyboardButton(text="❌ Отменить сделку", callback_data=f"deal:cancel:{deal_id}")],
    ])
    await message.answer(
        f"🛡 <b>Автогарант #{deal_id}</b>\n\n"
        f"👤 Участник: @{escape(partner_username)}\n"
        f"💰 Депозит: {money_line}\n\n"
        f"🔗 Адрес: <code>{ESCROW_BTC_ADDRESS}</code>\n\n"
        f"Срок: {TTL_HOURS} ч.\n\n"
        f"⚡️ Скопируй и вставь: <code>/withdraw {ESCROW_BTC_ADDRESS} {amount_str}</code>",
        reply_markup=kb,
        parse_mode="HTML",
    )

    who = f"@{escape(initiator)}" if initiator else escape(message.from_user.full_name or "пользователь")
    note = (
        f"🛡 <b>Создана сделка #{deal_id}</b>\n\n"
        f"💰 Сумма: {money_line}\n"
        f"👤 С пользователем: {who}\n\n"
        "Ожидается депозит. Сделка появится в разделе «Мои сделки»."
    )
    try:
        await send_notification(message.bot, int(partner_id), note)
    except Exception as e:
        logger.error("Не удалось уведомить участника автогаранта %s: %s", partner_id, e)


async def notify_garant_funded(deal_id: int):
    """После депозита: сделка открыта, оба участника получают сообщение."""
    deal = await get_deal(deal_id)
    if not deal or (deal.get("side") or "").upper() != "GARANT":
        return
    owner = await get_user(deal["owner_id"])
    partner = await get_user(deal["partner_id"]) if deal.get("partner_id") else None
    amount = Decimal(str(deal.get("max_btc") or "0"))
    amount_str = _fmt_btc(amount)
    rate = await get_btc_rub_rate()
    rub_str = f"{btc_to_rub(amount, rate):,.0f}".replace(",", " ")
    owner_name = f"@{owner['username']}" if owner and owner.get("username") else "пользователь"
    partner_name = f"@{partner['username']}" if partner and partner.get("username") else "участник"
    from aiogram import Bot
    from config import BOT_TOKEN
    bot = Bot(token=BOT_TOKEN)
    try:
        await send_notification(
            bot,
            deal["owner_id"],
            (
                f"✅ <b>Депозит по автогаранту #{deal_id} принят</b>\n\n"
                f"💰 {amount_str} BTC (~{rub_str} руб)\n"
                f"👤 Сделка с {escape(partner_name)} открыта.\n\n"
                "Когда фиат будет у вас — подтвердите получение.\n"
                "После этого BTC уйдёт второму участнику на кошелёк из его профиля.\n"
                "Если оплаты нет — оспорьте, депозит останется заблокирован."
            ),
            reply_markup=_owner_decision_kb(deal_id),
        )
        if deal.get("partner_id"):
            await send_notification(
                bot,
                int(deal["partner_id"]),
                (
                    f"🛡 <b>Автогарант #{deal_id}</b>\n\n"
                    f"Депозит принят и заблокирован.\n"
                    f"💰 {amount_str} BTC (~{rub_str} руб)\n"
                    f"👤 С пользователем {escape(owner_name)}\n\n"
                    "Отправьте фиат инициатору.\n"
                    "Когда он подтвердит получение, BTC придёт на кошелёк из вашего профиля."
                ),
            )
    finally:
        await bot.session.close()


@router.callback_query(F.data.startswith("garant:deposit_done:"))
async def garant_deposit_done(callback: CallbackQuery):
    try:
        deal_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return

    deal = await get_deal(deal_id)
    if not deal or (deal.get("side") or "").upper() != "GARANT":
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return
    if deal["owner_id"] != callback.from_user.id:
        await callback.answer("❌ Это не ваша сделка", show_alert=True)
        return
    if deal["status"] not in (DealStatus.AWAITING_DEPOSIT, DealStatus.CONFIRMING, DealStatus.ESCROWED):
        await callback.answer("❌ Неактуальная сделка", show_alert=True)
        return

    if deal["status"] == DealStatus.AWAITING_DEPOSIT:
        await update_deal_status(deal_id, DealStatus.CONFIRMING)

    updated = await get_deal(deal_id)
    if updated and updated.get("status") in (DealStatus.ESCROWED, DealStatus.ACTIVE):
        await callback.answer("✅ Депозит уже подтверждён", show_alert=True)
        return

    await callback.answer("Ждём подтверждение депозита", show_alert=True)


@router.callback_query(F.data.startswith("garant:fiat_ok:"))
async def garant_fiat_ok(callback: CallbackQuery):
    """Инициатор подтвердил фиат — BTC уходит второму участнику."""
    try:
        deal_id = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("❌ Неверный ID сделки", show_alert=True)
        return

    deal = await get_deal(deal_id)
    if not deal or (deal.get("side") or "").upper() != "GARANT":
        await callback.answer("❌ Сделка не найдена", show_alert=True)
        return
    if callback.from_user.id != deal.get("owner_id"):
        await callback.answer("❌ Подтверждает получение фиата тот, кто внёс депозит", show_alert=True)
        return
    if deal.get("status") != DealStatus.MATCHED:
        await callback.answer("❌ Сделка уже не ждёт подтверждения фиата", show_alert=True)
        return
    if not deal.get("partner_id"):
        await callback.answer("❌ В сделке нет второго участника", show_alert=True)
        return

    partner = await get_user(deal["partner_id"])
    wallet = (partner or {}).get("btc_wallet")
    if not wallet:
        await callback.answer("❌ У второго участника не указан BTC-кошелёк в профиле", show_alert=True)
        return

    from database import set_deal_buyer_wallet, auto_release_btc
    await set_deal_buyer_wallet(deal_id, wallet)
    await update_deal_status(deal_id, DealStatus.PAYMENT_PENDING)
    result = await auto_release_btc(deal_id, bot=callback.message.bot)
    if not result.get("success"):
        await update_deal_status(deal_id, DealStatus.MATCHED)
        await callback.answer("❌ Не удалось запустить вывод", show_alert=True)
        return

    await callback.answer("Фиат подтверждён, BTC отправляется")
    try:
        await callback.message.edit_text(
            f"✅ <b>Фиат по автогаранту #{deal_id} подтверждён</b>\n\n"
            "BTC отправляется второму участнику.",
            parse_mode="HTML",
        )
    except Exception:
        pass
    await send_notification(
        callback.message.bot,
        deal["partner_id"],
        f"✅ Инициатор подтвердил фиат по автогаранту #{deal_id}. BTC отправляется на ваш кошелёк.",
    )
