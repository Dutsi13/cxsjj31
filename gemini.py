import asyncio
import sqlite3
import os
import time
import logging
import sys
import aiohttp
from aiogram import Bot, Dispatcher, types, F, LabeledPrice
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, RPCError
from aiocryptopay import CryptoPay

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8648072212:AAE-hC9VtVpHpAgdY3tgj8GNNEucu1QfRXc'
API_ID = 20652575
API_HASH = 'c0d5c94ec3c668444dca9525940d876d'
ADMIN_ID = 7785932103
CRYPTO_PAY_TOKEN = 'ВАШ_ТОКЕН_ИЗ_@CryptoBot'  # Токен из @CryptoBot или @CryptoTestBot

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
# Если используете реальные деньги, замените testnet=True на testnet=False
crypto = CryptoPay(token=CRYPTO_PAY_TOKEN, testnet=True)

# --- БАЗА ДАННЫХ ---
db = sqlite3.connect('bot_data.db', check_same_thread=False, timeout=30)
cur = db.cursor()


def init_db():
    cur.execute('''CREATE TABLE IF NOT EXISTS accounts 
                   (phone TEXT PRIMARY KEY, owner_id INTEGER, expires INTEGER, 
                    text TEXT DEFAULT 'Привет!', photo_id TEXT, 
                    interval INTEGER DEFAULT 5, chats TEXT DEFAULT '',
                    is_running INTEGER DEFAULT 0, price_per_min INTEGER DEFAULT 10)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS users 
                   (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0)''')
    db.commit()


init_db()


class States(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
    waiting_for_rent_time = State()
    edit_text = State()
    edit_interval = State()
    edit_chats = State()
    add_photo = State()
    top_up_amount = State()


def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📂 Каталог аккаунтов")
    kb.button(text="🔑 Моя аренда")
    kb.button(text="💰 Баланс")
    return kb.as_markup(resize_keyboard=True)


# --- ФУНКЦИИ БАЛАНСА ---
def get_balance(user_id):
    cur.execute('INSERT OR IGNORE INTO users (user_id) VALUES (?)', (user_id,))
    cur.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    return cur.fetchone()[0]


def add_balance(user_id, amount):
    cur.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    db.commit()


# --- СТАРТ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("🤖 Бот запущен и готов к работе.", reply_markup=main_menu())


# --- АДМИН КОМАНДЫ (УПРАВЛЕНИЕ АККАУНТАМИ И БАЛАНСОМ) ---
@dp.message(Command("givebal"))
async def adm_give(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        uid, amount = int(command.args.split()[0]), int(command.args.split()[1])
        add_balance(uid, amount)
        await message.answer(f"✅ Баланс пользователя {uid} пополнен на {amount} руб.")
    except Exception:
        await message.answer("⚠️ Ошибка. Пример: `/givebal 12345678 100`", parse_mode="Markdown")


@dp.message(Command("delbal"))
async def adm_del(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        uid, amount = int(command.args.split()[0]), int(command.args.split()[1])
        add_balance(uid, -amount)
        await message.answer(f"❌ Со счета {uid} списано {amount} руб.")
    except Exception:
        await message.answer("⚠️ Ошибка. Пример: `/delbal 12345678 100`", parse_mode="Markdown")


@dp.message(Command("setprice"))
async def adm_price(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        phone, price = command.args.split()[0], int(command.args.split()[1])
        cur.execute('UPDATE accounts SET price_per_min = ? WHERE phone = ?', (price, phone))
        db.commit()
        await message.answer(f"🏷 Цена для {phone} установлена: {price} руб/мин")
    except Exception:
        await message.answer("⚠️ Ошибка. Пример: `/setprice +79991234567 15`", parse_mode="Markdown")


@dp.message(Command("addacc"))
async def add_acc_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("Введите номер телефона (+7...):")
    await state.set_state(States.waiting_for_phone)


@dp.message(States.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip().replace(" ", "")
    client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
    await client.connect()
    try:
        sent_code = await client.send_code_request(phone)
        await state.update_data(phone=phone, hash=sent_code.phone_code_hash)
        await message.answer(f"📩 Код отправлен на {phone}. Введите его:")
        await state.set_state(States.waiting_for_code)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()
    finally:
        await client.disconnect()


@dp.message(States.waiting_for_code)
async def process_code(message: types.Message, state: FSMContext):
    data = await state.get_data()
    phone, code = data['phone'], message.text.strip()
    client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(phone, code, phone_code_hash=data['hash'])
        cur.execute(
            'INSERT OR REPLACE INTO accounts (phone, owner_id, expires, is_running, price_per_min) VALUES (?, NULL, 0, 0, 10)',
            (phone,))
        db.commit()
        await message.answer(f"✅ Аккаунт {phone} успешно добавлен.")
        await state.clear()
    except SessionPasswordNeededError:
        await message.answer("🔐 На аккаунте 2FA. Введите пароль:")
        await state.set_state(States.waiting_for_password)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()
    finally:
        await client.disconnect()


@dp.message(States.waiting_for_password)
async def process_password(message: types.Message, state: FSMContext):
    data = await state.get_data()
    phone, password = data['phone'], message.text.strip()
    client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
    await client.connect()
    try:
        await client.sign_in(password=password)
        cur.execute(
            'INSERT OR REPLACE INTO accounts (phone, owner_id, expires, is_running, price_per_min) VALUES (?, NULL, 0, 0, 10)',
            (phone,))
        db.commit()
        await message.answer(f"✅ Аккаунт {phone} добавлен (2FA пройдена).")
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Ошибка пароля: {e}")
        await state.clear()
    finally:
        await client.disconnect()


@dp.message(Command("delacc"))
async def del_acc_cmd(message: types.Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        return await message.answer("⚠️ Укажите номер. Пример: `/delacc +79991234567`", parse_mode="Markdown")

    phone = command.args.strip().replace(" ", "")
    cur.execute('DELETE FROM accounts WHERE phone = ?', (phone,))
    db.commit()

    session_path = f"sessions/{phone}.session"
    if os.path.exists(session_path):
        try:
            os.remove(session_path)
        except PermissionError:
            pass
    await message.answer(f"🗑 Аккаунт {phone} успешно удален.")


# --- ПОПОЛНЕНИЕ БАЛАНСА ---
@dp.message(F.text == "💰 Баланс")
async def show_balance(message: types.Message):
    bal = get_balance(message.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Пополнить Звездами", callback_data="topup_stars")
    kb.button(text="🔌 Пополнить CryptoPay", callback_data="topup_crypto")
    await message.answer(f"💳 Ваш баланс: **{bal} руб.**", reply_markup=kb.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("topup_"))
async def start_topup(call: types.CallbackQuery, state: FSMContext):
    method = call.data.split("_")[1]
    await state.update_data(method=method)
    await call.message.answer("Введите сумму пополнения в рублях (целое число):")
    await state.set_state(States.top_up_amount)


@dp.message(States.top_up_amount)
async def process_pay(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Пожалуйста, введите только число.")
    amount = int(message.text)
    data = await state.get_data()

    if data['method'] == 'stars':
        # Telegram Stars. 1 звезда = 2 рубля (установите свой курс)
        stars_amount = max(1, amount // 2)
        await message.answer_invoice(
            title="Пополнение баланса",
            description=f"Пополнение счета на {amount} руб.",
            payload=f"stars_{amount}",
            currency="XTR",
            prices=[LabeledPrice(label="Пополнение рублями", amount=stars_amount)]
        )
    else:
        # CryptoPay (считаем USDT ~ 100 руб для примера)
        usdt_amount = round(amount / 100.0, 2)
        if usdt_amount < 0.1: usdt_amount = 0.1  # Минималка

        invoice = await crypto.create_invoice(asset='USDT', amount=usdt_amount)
        kb = InlineKeyboardBuilder()
        kb.button(text="Оплатить", url=invoice.bot_invoice_url)
        kb.button(text="Проверить оплату", callback_data=f"check_{invoice.invoice_id}_{amount}")
        await message.answer(f"Счет на {amount} руб. Создан инвойс на {usdt_amount} USDT.\nОплатите по кнопке ниже:",
                             reply_markup=kb.as_markup())

    await state.clear()


@dp.callback_query(F.data.startswith("check_"))
async def check_crypto(call: types.CallbackQuery):
    try:
        _, inv_id, amount = call.data.split("_")
        invoices = await crypto.get_invoices(invoice_ids=int(inv_id))

        if invoices and invoices[0].status == 'paid':
            add_balance(call.from_user.id, int(amount))
            await call.message.edit_text(f"✅ Оплата подтверждена! Баланс пополнен на {amount} руб.")
        else:
            await call.answer("❌ Счет еще не оплачен!", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка проверки платежа: {e}")
        await call.answer("Произошла ошибка проверки.", show_alert=True)


@dp.pre_checkout_query()
async def pre_checkout(query: types.PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def success_pay(message: types.Message):
    amount = int(message.successful_payment.invoice_payload.split("_")[1])
    add_balance(message.from_user.id, amount)
    await message.answer(f"✅ Баланс пополнен на {amount} руб. через Telegram Stars!")


# --- КАТАЛОГ И АРЕНДА ---
@dp.message(F.text == "📂 Каталог аккаунтов")
async def show_catalog(message: types.Message):
    now = int(time.time())
    cur.execute('SELECT phone, price_per_min FROM accounts WHERE owner_id IS NULL OR expires < ?', (now,))
    free_accs = cur.fetchall()
    if not free_accs: return await message.answer("📭 Свободных аккаунтов нет.")
    kb = InlineKeyboardBuilder()
    for (phone, price) in free_accs:
        kb.button(text=f"📱 {phone} - {price}₽/мин", callback_data=f"rent_init_{phone}")
    kb.adjust(1)
    await message.answer("Выберите аккаунт для аренды:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("rent_init_"))
async def rent_input_time(call: types.CallbackQuery, state: FSMContext):
    phone = call.data.replace("rent_init_", "").strip()
    cur.execute('SELECT price_per_min FROM accounts WHERE phone = ?', (phone,))
    price = cur.fetchone()[0]
    await state.update_data(rent_phone=phone, price=price)
    await call.message.answer(f"Стоимость аренды: **{price} руб/мин**.\nНа сколько минут арендовать {phone}?",
                              parse_mode="Markdown")
    await state.set_state(States.waiting_for_rent_time)


@dp.message(States.waiting_for_rent_time)
async def process_rent_finish(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Введите число минут.")
    mins = int(message.text)
    data = await state.get_data()
    total_cost = mins * data['price']

    if get_balance(message.from_user.id) < total_cost:
        return await message.answer(f"❌ Недостаточно средств. Для аренды на {mins} мин нужно {total_cost} руб.")

    add_balance(message.from_user.id, -total_cost)
    expires = int(time.time()) + (mins * 60)
    cur.execute('UPDATE accounts SET owner_id = ?, expires = ?, is_running = 0 WHERE phone = ?',
                (message.from_user.id, expires, data['rent_phone']))
    db.commit()
    await message.answer(f"✅ Аккаунт {data['rent_phone']} арендован на {mins} мин.\nСписано {total_cost} руб.",
                         reply_markup=main_menu())
    await state.clear()


# --- УПРАВЛЕНИЕ АРЕНДОЙ ---
@dp.message(F.text == "🔑 Моя аренда")
async def my_rent(message: types.Message):
    now = int(time.time())
    cur.execute('SELECT phone, expires FROM accounts WHERE owner_id = ? AND expires > ?', (message.from_user.id, now))
    rented = cur.fetchall()
    if not rented: return await message.answer("У вас нет активных аренд.")
    kb = InlineKeyboardBuilder()
    for (phone, exp) in rented:
        kb.button(text=f"⚙️ {phone}", callback_data=f"manage_{phone}")
    kb.adjust(1)
    await message.answer("Ваши арендованные аккаунты:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("manage_"))
async def manage_acc(call: types.CallbackQuery):
    phone = call.data.replace("manage_", "").strip()
    cur.execute('SELECT text, photo_id, interval, chats, is_running FROM accounts WHERE phone = ?', (phone,))
    res = cur.fetchone()
    if not res: return
    text, photo, interval, chats, is_running = res
    status = "🟢 ЗАПУЩЕНА" if is_running else "⚪️ ОСТАНОВЛЕНА"

    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Текст", callback_data=f"edit_text_{phone}")
    kb.button(text="⏱ Инт.", callback_data=f"edit_int_{phone}")
    kb.button(text="👥 Чаты", callback_data=f"edit_chats_{phone}")
    kb.button(text="🖼 Фото", callback_data=f"edit_photo_{phone}")
    kb.button(text="🛑 СТОП" if is_running else "🚀 ПУСК", callback_data=f"{'stop' if is_running else 'run'}_{phone}")
    kb.adjust(2, 2, 1)

    try:
        await call.message.edit_text(
            f"🛠 **Настройки {phone}**\n\nСтатус: {status}\nИнтервал: {interval} сек.\nТекст: {text[:50]}...",
            reply_markup=kb.as_markup(), parse_mode="Markdown"
        )
    except Exception:
        pass


# --- ОБРАБОТЧИКИ КНОПОК РЕДАКТИРОВАНИЯ ---
@dp.callback_query(F.data.startswith("edit_text_"))
async def edit_text_call(call: types.CallbackQuery, state: FSMContext):
    phone = call.data.replace("edit_text_", "").strip()
    await state.update_data(edit_phone=phone)
    await call.message.answer(f"Введите новый текст для {phone}:")
    await state.set_state(States.edit_text)


@dp.message(States.edit_text)
async def save_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cur.execute('UPDATE accounts SET text = ? WHERE phone = ?', (message.text, data['edit_phone']))
    db.commit()
    await message.answer("✅ Текст обновлен.")
    await state.clear()


@dp.callback_query(F.data.startswith("edit_int_"))
async def edit_int_call(call: types.CallbackQuery, state: FSMContext):
    phone = call.data.replace("edit_int_", "").strip()
    await state.update_data(edit_phone=phone)
    await call.message.answer("Введите интервал в секундах (число):")
    await state.set_state(States.edit_interval)


@dp.message(States.edit_interval)
async def save_interval(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Нужно число.")
    data = await state.get_data()
    cur.execute('UPDATE accounts SET interval = ? WHERE phone = ?', (int(message.text), data['edit_phone']))
    db.commit()
    await message.answer("✅ Интервал обновлен.")
    await state.clear()


@dp.callback_query(F.data.startswith("edit_chats_"))
async def edit_chats_call(call: types.CallbackQuery, state: FSMContext):
    phone = call.data.replace("edit_chats_", "").strip()
    await state.update_data(edit_phone=phone)
    await call.message.answer("Пришлите список чатов через запятую или с новой строки:")
    await state.set_state(States.edit_chats)


@dp.message(States.edit_chats)
async def save_chats(message: types.Message, state: FSMContext):
    data = await state.get_data()
    cur.execute('UPDATE accounts SET chats = ? WHERE phone = ?', (message.text, data['edit_phone']))
    db.commit()
    await message.answer("✅ Список чатов обновлен.")
    await state.clear()


@dp.callback_query(F.data.startswith("edit_photo_"))
async def edit_photo_call(call: types.CallbackQuery, state: FSMContext):
    phone = call.data.replace("edit_photo_", "").strip()
    await state.update_data(edit_phone=phone)
    await call.message.answer("Отправьте фото (как файл или картинку) или напишите 'нет' для удаления:")
    await state.set_state(States.add_photo)


@dp.message(States.add_photo)
async def save_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photo_id = message.photo[-1].file_id if message.photo else None
    if message.text and message.text.lower() == 'нет': photo_id = None
    cur.execute('UPDATE accounts SET photo_id = ? WHERE phone = ?', (photo_id, data['edit_phone']))
    db.commit()
    await message.answer("✅ Фото обновлено.")
    await state.clear()


# --- ЛОГИКА ПУСКА / СТОПА ---
@dp.callback_query(F.data.startswith("run_"))
async def run_cmd(call: types.CallbackQuery):
    phone = call.data.replace("run_", "").strip()
    cur.execute('UPDATE accounts SET is_running = 1 WHERE phone = ?', (phone,))
    db.commit()
    asyncio.create_task(broadcast_loop(phone, call.from_user.id))
    await manage_acc(call)


@dp.callback_query(F.data.startswith("stop_"))
async def stop_cmd(call: types.CallbackQuery):
    phone = call.data.replace("stop_", "").strip()
    cur.execute('UPDATE accounts SET is_running = 0 WHERE phone = ?', (phone,))
    db.commit()
    await manage_acc(call)


# --- ЦИКЛ РАССЫЛКИ ---
async def broadcast_loop(phone, user_id):
    logger.info(f"--- [START] Рассылка {phone} ---")
    client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
    try:
        await client.connect()
        while True:
            cur.execute('SELECT is_running, expires, text, photo_id, interval, chats FROM accounts WHERE phone = ?',
                        (phone,))
            res = cur.fetchone()
            if not res or res[0] == 0: break

            is_run, expires, text, photo_id, interval, chats_str = res
            if int(time.time()) > expires:
                cur.execute('UPDATE accounts SET is_running = 0, owner_id = NULL WHERE phone = ?', (phone,))
                db.commit()
                await bot.send_message(user_id, f"⏰ Аренда {phone} истекла.")
                break

            if not client.is_connected():
                try:
                    await client.connect()
                except:
                    await asyncio.sleep(10)
                    continue

            chats = [c.strip() for c in chats_str.replace('\n', ',').split(',') if c.strip()]
            if not chats:
                await bot.send_message(user_id, f"⚠️ У аккаунта {phone} не заданы чаты. Остановка.")
                cur.execute('UPDATE accounts SET is_running = 0 WHERE phone = ?', (phone,))
                db.commit()
                break

            for chat in chats:
                # Мгновенная проверка кнопки "СТОП" внутри перебора чатов
                cur.execute('SELECT is_running FROM accounts WHERE phone = ?', (phone,))
                if cur.fetchone()[0] == 0: break

                try:
                    if photo_id:
                        file = await bot.get_file(photo_id)
                        path = f"temp_{phone}.jpg"
                        await bot.download_file(file.file_path, path)
                        await client.send_file(chat, path, caption=text)
                        if os.path.exists(path): os.remove(path)
                    else:
                        await client.send_message(chat, text)
                    logger.info(f"[{phone}] -> {chat} ✅")
                except RPCError as e:
                    err = str(e).upper()
                    if "CHAT_WRITE_FORBIDDEN" in err:
                        logger.error(f"[{phone}] Пропуск {chat}: Нет прав писать сообщения.")
                    elif "PEER_ID_INVALID" in err:
                        logger.error(f"[{phone}] Пропуск {chat}: Аккаунт не состоит в чате.")
                    else:
                        logger.error(f"[{phone}] ❌ Ошибка TG в {chat}: {e}")
                except Exception as e:
                    logger.error(f"[{phone}] ❌ Ошибка в {chat}: {e}")

                await asyncio.sleep(interval)
            await asyncio.sleep(5)
    except Exception as e:
        logger.error(f"Критическая ошибка рассылки {phone}: {e}")
    finally:
        if client.is_connected(): await client.disconnect()


# --- БЕЗОПАСНЫЙ ЦИКЛ ПОЛЛИНГА ---
async def start_polling_safe():
    while True:
        try:
            logger.info("Запуск Polling бота...")
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
            logger.error("Сеть api.telegram.org недоступна. Жду 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.critical(f"Глобальная ошибка: {e}")
            await asyncio.sleep(5)


# --- ТОЧКА ВХОДА ---
async def main():
    if not os.path.exists('sessions'): os.makedirs('sessions')
    await start_polling_safe()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот выключен.")