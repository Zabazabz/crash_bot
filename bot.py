
# bot.py
import os
import sqlite3
import secrets
import hashlib
import time
import math
import random
import threading
import telebot
from telebot import types
# =================== Настройки ===================
TOKEN = os.getenv("TOKEN")  # на Railway добавь переменную TOKEN
START_BALANCE = 1000        # стартовый виртуальный баланс для новых пользователей
DB_FILE = "crash_bot.db"
# Настройки вероятностей категорий (сумма должна быть 1.0)
PROB_CATEGORY = {
    "common_1_4": 0.85,   # 85% — мультипликатор из диапазона 1.00 - 4.00
    "rare_5_10": 0.12,    # 12% — 5.00 - 10.00
    "very_10_50": 0.025,  # 2.5% — 10.00 - 50.00
    "extreme_50_plus": 0.005  # 0.5% — 50.00+
}
# Верхний предел (в демонстрации ограничиваем)
MAX_MULTIPLIER = 500.0
# Тайминги / скорость роста (секунд между обновлениями)
TICK_DELAY = 0.7
# =================================================
bot = telebot.TeleBot(TOKEN, threaded=True)
# ========== База данных ==========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rounds (
            chat_id INTEGER PRIMARY KEY,
            secret TEXT,
            secret_hash TEXT,
            crash REAL,
            state TEXT,  -- 'accepting', 'running', 'finished'
            current_multiplier REAL DEFAULT 1.0,
            message_id INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            chat_id INTEGER,
            user_id INTEGER,
            amount INTEGER,
            cashed INTEGER DEFAULT 0, -- 0/1
            cashout_multiplier REAL DEFAULT 0.0
        )
    """)
    conn.commit()
    conn.close()
def ensure_user(user_id, username):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    if not r:
        cur.execute("INSERT INTO users (user_id, username, balance) VALUES (?,?,?)",
                    (user_id, username or "", START_BALANCE))
        conn.commit()
        bal = START_BALANCE
    else:
        bal = r[0]
    conn.close()
    return bal
def get_balance(user_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else 0
def change_balance(user_id, delta):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (delta, user_id))
    conn.commit()
    conn.close()
# Bets & rounds helpers
def add_bet(chat_id, user_id, amount):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("INSERT INTO bets (chat_id, user_id, amount) VALUES (?,?,?)", (chat_id, user_id, amount))
    conn.commit()
    conn.close()
def get_bets(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT user_id, amount, cashed, cashout_multiplier FROM bets WHERE chat_id=?", (chat_id,))
    rows = cur.fetchall()
    conn.close()
    return rows
def clear_bets(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM bets WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()
def save_round(chat_id, secret, secret_hash, crash):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("REPLACE INTO rounds (chat_id, secret, secret_hash, crash, state, current_multiplier, message_id) VALUES (?,?,?,?,?,?,?)",
                (chat_id, secret, secret_hash, crash, "accepting", 1.0, None))
    conn.commit()
    conn.close()
def set_round_running(chat_id, message_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE rounds SET state='running', message_id=? WHERE chat_id=?", (message_id, chat_id))
    conn.commit()
    conn.close()
def update_round_multiplier(chat_id, multiplier):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE rounds SET current_multiplier=? WHERE chat_id=?", (multiplier, chat_id))
    conn.commit()
    conn.close()
def end_round(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("UPDATE rounds SET state='finished' WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()
def get_round(chat_id):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT secret, secret_hash, crash, state, current_multiplier, message_id FROM rounds WHERE chat_id=?", (chat_id,))
    r = cur.fetchone()
    conn.close()
    return r
# ========== Provably fair -> crash from secret ==========
def crash_from_secret(secret_hex):
    # deterministic from secret (hex string)
    h = hashlib.sha256(secret_hex.encode()).hexdigest()
    num = int(h, 16)
    x = num / (2**256)  # in [0,1)
    if x >= 1.0:
        x = 0.999999999999
    crash = 1.0 / (1.0 - x)
    crash = max(1.00, round(crash, 2))
    if crash > MAX_MULTIPLIER:
        crash = MAX_MULTIPLIER
    return crash
# ========== Генерация случайного мультипликатора с заданной редкостью ==========
def generate_random_multiplier():
    # Выбираем категорию по вероятностям
    r = random.random()
    cum = 0.0
    for cat, p in PROB_CATEGORY.items():
        cum += p
        if r <= cum:
            chosen = cat
            break
    else:
        chosen = "common_1_4"
    if chosen == "common_1_4":
        # равномерно внутри [1.00, 4.00), но с небольшым смещением к низким значениям
        m = 1.0 + random.random()**1.3 * 3.0
    elif chosen == "rare_5_10":
        # выбираем в [5,10), более склонны к нижней границе
        m = 5.0 + random.random()**1.5 * 5.0
    elif chosen == "very_10_50":
        # экспоненциальное распределение в диапазоне 10-50
        m = 10.0 + (random.random()**2.0) * 40.0
    else:  # extreme_50_plus
        # хвост: распределение Пирсона/экспоненциальное, до MAX_MULTIPLIER
        u = random.random()
        m = 50.0 + (u**3) * (MAX_MULTIPLIER - 50.0)
    m = round(m, 2)
    if m < 1.0:
        m = 1.0
    return m
# ========== Команды бота ==========
@bot.message_handler(commands=['start'])
def cmd_start(message):
    ensure_user(message.from_user.id, message.from_user.username)
    bot.reply_to(message, f"Привет, {message.from_user.first_name}!\nУ тебя на балансе {get_balance(message.from_user.id)} монет.\nИспользуй /balance, /bet <sum>, /transfer <@user|id> <sum>, /crash start, /crash go")
@bot.message_handler(commands=['balance'])
def cmd_balance(message):
    ensure_user(message.from_user.id, message.from_user.username)
    bot.reply_to(message, f"Твой баланс: {get_balance(message.from_user.id)} монет.")
@bot.message_handler(commands=['transfer'])
def cmd_transfer(message):
    # форматы: /transfer @username 100  или /transfer 12345678 100
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Использование: /transfer <@username|user_id> <сумма>")
        return
    target = args[1]
    try:
        amount = int(args[2])
    except:
        bot.reply_to(message, "Сумма должна быть числом.")
        return
    if amount <= 0:
        bot.reply_to(message, "Сумма должна быть положительной.")
        return
    sender = message.from_user.id
    ensure_user(sender, message.from_user.username)
    bal = get_balance(sender)
    if amount > bal:
        bot.reply_to(message, "Недостаточно средств.")
        return
    # определяем target id — сначала по упоминанию
    if target.startswith('@'):
        # ищем username в БД
        uname = target[1:]
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE username=?", (uname,))
        row = cur.fetchone()
        conn.close()
        if not row:
            bot.reply_to(message, "Пользователь с таким username не найден (он должен был хотя бы раз взаимодействовать с ботом).")
            return
        target_id = row[0]
    else:
        try:
            target_id = int(target)
        except:
            bot.reply_to(message, "Невалидный идентификатор пользователя.")
            return
    ensure_user(target_id, "")  # если нового юзера — зарегистрируем с начальным балансом
    change_balance(sender, -amount)
    change_balance(target_id, amount)
    bot.reply_to(message, f"Перевод выполнен: {amount} монет -> {target}. Твой новый баланс: {get_balance(sender)}")
@bot.message_handler(commands=['give'])
def cmd_give(message):
    # только для себя/админа на локале; можно убрать в продакшн
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "Использование: /give <user_id> <amount>")
        return
    try:
        uid = int(args[1]); amt = int(args[2])
    except:
        bot.reply_to(message, "Неверные параметры.")
        return
    ensure_user(uid, "")
    change_balance(uid, amt)
    bot.reply_to(message, f"Выдал {amt} юзеру {uid}")
@bot.message_handler(commands=['crash'])
def cmd_crash(message):
    args = message.text.split()
    chat_id = message.chat.id
    if len(args) == 1:
        bot.reply_to(message, "Команды: /crash start, /crash go, /crash reveal")
        return
    action = args[1].lower()
    if action == "start":
        # подготовка раунда
        secret = secrets.token_hex(16)
        secret_hash = hashlib.sha256(secret.encode()).hexdigest()
        # ты попросил рандомные иксы — генерируем рандомный crash
        crash_point = generate_random_multiplier()
        save_round(chat_id, secret, secret_hash, crash_point)
        clear_bets(chat_id)
        bot.reply_to(message, f"Раунд подготовлен.\nHash (SHA256): {secret_hash}\nСтавки принимаются: /bet <сумма>\nКогда готовы — /crash go", parse_mode='Markdown')
    elif action == "go":
        r = get_round(chat_id)
        if not r or r[3] != "accepting":
            bot.reply_to(message, "Сначала выполните /crash start")
            return
        secret, secret_hash, crash_point, state, cur_mult, msgid = r
        bets = get_bets(chat_id)
        if not bets:
            bot.reply_to(message, "Нет ставок — раунд отменён.")
            end_round(chat_id)
            return
        # стартуем раунд — отправляем сообщение с кнопкой cashout
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("💸 Cash out", callback_data="cashout"))
        sent = bot.reply_to(message, f"Crash запущен: 1.00x", reply_markup=kb)
        set_round_running(chat_id, sent.message_id)
        # запускаем цикл роста в отдельном потоке (чтобы polling не блокировался)
        threading.Thread(target=run_crash_loop, args=(chat_id, sent.message_id, crash_point), daemon=True).start()
    elif action == "reveal":
        r = get_round(chat_id)
        if not r:
            bot.reply_to(message, "Нет данных о раунде.")
            return
        secret, secret_hash, crash_point, state, cur_mult, msgid = r
        bot.reply_to(message, f"Secret: {secret}\nHash: {secret_hash}\nCrash: {crash_point}", parse_mode='Markdown')
    else:
        bot.reply_to(message, "Неизвестная команда для /crash")
@bot.message_handler(commands=['bet'])
def cmd_bet(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "Использование: /bet <сумма>")
        return
    try:
        amount = int(args[1])
    except:
        bot.reply_to(message, "Сумма должна быть числом.")
        return
    if amount <= 0:
        bot.reply_to(message, "Ставка должна быть положительной.")
        return
    chat_id = message.chat.id
    user_id = message.from_user.id
    ensure_user(user_id, message.from_user.username)
    bal = get_balance(user_id)
    if amount > bal:
        bot.reply_to(message, "Недостаточно средств.")
        return
    r = get_round(chat_id)
    if not r or r[3] != "accepting":
        bot.reply_to(message, "Ставки принимаются только после /crash start и до /crash go.")
        return
    change_balance(user_id, -amount)
    add_bet(chat_id, user_id, amount)
    bot.reply_to(message, f"Ставка {amount} принята. Твой новый баланс: {get_balance(user_id)}")
# ========== Cashout callback ==========
@bot.callback_query_handler(func=lambda call: call.data == "cashout")
def cb_cashout(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    r = get_round(chat_id)
    if not r or r[3] != "running":
        bot.answer_callback_query(call.id, "Нет активного раунда.", show_alert=False)
        return
    secret, secret_hash, crash_point, state, current_multiplier, msgid = r
    # если уже краш — нельзя
    if current_multiplier >= crash_point:
        bot.answer_callback_query(call.id, "Слишком поздно — раунд уже крашен.", show_alert=False)
        return
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT amount, cashed FROM bets WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    row = cur.fetchone()
    if not row:
        conn.close()
        bot.answer_callback_query(call.id, "У тебя нет ставки в этом раунде.", show_alert=False)
        return
    amount, cashed_flag = row
    if cashed_flag:
        conn.close()
        bot.answer_callback_query(call.id, "Ты уже забрал.", show_alert=False)
        return
    # помечаем как забрал и сохраняем мульт при котором забрал
    cur.execute("UPDATE bets SET cashed=1, cashout_multiplier=? WHERE chat_id=? AND user_id=?", (current_multiplier, chat_id, user_id))
    conn.commit()
    conn.close()
    bot.answer_callback_query(call.id, f"Вы забрали на {current_multiplier:.2f}x — выплата произойдёт после краша.", show_alert=False)
# ========== Логика роста в отдельном потоке ==========
def run_crash_loop(chat_id, message_id, crash_point):
    multiplier = 1.00
    tick = 0
    while True:
        time.sleep(TICK_DELAY)
        tick += 1
        # простой рост: умножаем на небольшой коэффициент
        multiplier = round(multiplier * (1.06 + random.uniform(-0.01, 0.01)), 2)
        if multiplier <= 1.0:
            multiplier = 1.0
        update_round_multiplier(chat_id, multiplier)
        # обновить сообщение
        try:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"Crash: {multiplier:.2f}x", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("💸 Cash out", callback_data="cashout")))
        except Exception:
            pass
        # проверка краша
        if multiplier >= crash_point:
            break
        if tick > 400:  # safety cap
            break
    # конец раунда — подсчитываем выплаты
    end_round(chat_id)
    # читаем ставки и платим тем, кто успел забрать
    stored_bets = get_bets(chat_id)
    payouts = []
    for u_id, amount, cashed, cashout_multiplier in stored_bets:
        if cashed and cashout_multiplier > 0.0 and cashout_multiplier < crash_point:
            payout = int(amount * cashout_multiplier)
            change_balance(u_id, payout)
            payouts.append((u_id, payout))
    # сообщение с результатом
    secret_reveal = get_round(chat_id)  # получаем последний (state now finished)
    # secret_reveal содержит secret/hash/crash...
    # для безопасности: достанем secret и hash из БД вручную
    conn = sqlite3.connect(DB_FILE); cur = conn.cursor()
    cur.execute("SELECT secret, secret_hash, crash FROM rounds WHERE chat_id=?", (chat_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        secret, secret_hash, crash_val = row
    else:
        secret = secret_hash = "N/A"; crash_val = crash_point
    text = f"Раунд завершён! Crash был на {crash_val:.2f}x\nSecret: {secret}\nHash: {secret_hash}\n\nВыплаты:\n"
    if payouts:
        for uid, pay in payouts:
            text += f"• {uid}: +{pay}\n"
    else:
        text += "Никто не успел забрать или ставки проиграли.\n"
    try:
        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, parse_mode='Markdown')
    except Exception:
        # просто отправим новое сообщение
        bot.send_message(chat_id, text, parse_mode='Markdown')
    # очистка ставок
    clear_bets(chat_id)
# ========== Запуск ==========
if name == "main":
    init_db()
    print("Bot started...")
    bot.infinity_polling()