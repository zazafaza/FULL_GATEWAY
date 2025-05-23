import requests
import time
import datetime
import threading
import pandas as pd
import ta
import json
from flask import Flask, request

# إعدادات Telegram
TELEGRAM_TOKEN = "7023458497:AAEfNAvT1xqweDtQSPhsG4tsWAmI0bAPTjI"
CHAT_ID = "2087772257"

app = Flask(__name__)
interval = 60
report_interval = 3600
trailing_percentage = 0.02

# رأس المال
initial_balance = 1000.0
unit_size = 250.0
units = [unit_size] * 4
open_positions = []
last_report_time = time.time()
pending_confirmations = {}

# العملات المحظورة
BANNED_TOKENS = ["1000PEPE", "CATE", "SEX", "XXX", "RAYDALIO"]

# دالة لإرسال رسالة عبر Telegram
def send_telegram_message(message, keyboard=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {'chat_id': CHAT_ID, 'text': message}
    if keyboard:
        data['reply_markup'] = json.dumps(keyboard)
    try:
        requests.post(url, json=data)
    except Exception as e:
        print("Telegram Error:", e)

# دالة لجلب جميع الرموز المتاحة
def get_all_usdt_symbols():
    try:
        response = requests.get("https://api.binance.com/api/v3/exchangeInfo")
        data = response.json()
        return [s['symbol'] for s in data['symbols'] if s['symbol'].endswith("USDT") and s['status'] == 'TRADING']
    except:
        return []

# دالة لجلب سعر العملة
def get_price(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    try:
        response = requests.get(url)
        return float(response.json()['price'])
    except:
        return None

# دالة لجلب بيانات OHLCV
def get_ohlcv(symbol, interval="15m", limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json()
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "qav", "trades", "taker_base_vol", "taker_quote_vol", "ignore"])
        df["close"] = pd.to_numeric(df["close"])
        df["volume"] = pd.to_numeric(df["volume"])
        return df
    except:
        return None

# دالة لتقييم الصفقة
def get_strategy_score(symbol):
    if any(token in symbol for token in BANNED_TOKENS):
        return 0.0
    df = get_ohlcv(symbol)
    if df is None or df.empty:
        return 0.0
    score = 0
    close = df["close"]
    volume = df["volume"]
    rsi = ta.momentum.RSIIndicator(close).rsi()
    if 45 < rsi.iloc[-1] < 60:
        score += 1
    ema9 = ta.trend.EMAIndicator(close, window=9).ema_indicator()
    ema21 = ta.trend.EMAIndicator(close, window=21).ema_indicator()
    if ema9.iloc[-1] > ema21.iloc[-1]:
        score += 1
    avg_vol = volume.mean()
    if volume.iloc[-1] > avg_vol * 1.3:
        score += 1
    if close.iloc[-1] > close.iloc[-2] and close.iloc[-2] > close.iloc[-3]:
        score += 1
    if close.iloc[-1] > close.iloc[-5]:
        score += 1
    return round(score, 2)

# دالة لدخول الصفقة
def enter_trade(symbol, amount, price, unit_id):
    tp = price * 1.03
    sl = price * 0.97
    trade = {
        'symbol': symbol,
        'entry_price': price,
        'tp_price': tp,
        'sl_price': sl,
        'amount': amount,
        'entry_time': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'unit_id': unit_id,
        'active': True,
        'highest_price': price
    }
    open_positions.append(trade)
    keyboard = {
        "inline_keyboard": [[
            {"text": f"❌ إغلاق {symbol} الآن", "callback_data": f"close_{unit_id}"}
        ]]
    }
    send_telegram_message(f"✅ صفقة: {symbol} @ {price}\n🎯 TP: {tp:.2f}, 🛑 SL: {sl:.2f}", keyboard)

# دالة لتحديث الصفقات
def update_trades():
    global open_positions
    for trade in open_positions:
        if not trade['active']:
            continue
        current_price = get_price(trade['symbol'])
        if not current_price:
            continue
        if current_price > trade['highest_price']:
            trade['highest_price'] = current_price
            trade['tp_price'] = current_price * (1 - trailing_percentage)
        if current_price >= trade['tp_price']:
            trade['active'] = False
            units.append(trade['amount'])
            send_telegram_message(f"🎯 جني ربح {trade['symbol']} @ {current_price:.2f}")
        elif current_price <= trade['sl_price']:
            trade['active'] = False
            units.append(trade['amount'])
            send_telegram_message(f"🛑 وقف خسارة {trade['symbol']} @ {current_price:.2f}")

# دالة لتقرير الصفقات المفتوحة
def report_open_positions():
    active = [t for t in open_positions if t['active']]
    if not active:
        send_telegram_message("📭 لا توجد صفقات مفتوحة")
        return
    for pos in active:
        keyboard = {
            "inline_keyboard": [[
                {"text": f"❌ إغلاق {pos['symbol']}", "callback_data": f"close_{pos['unit_id']}"}
            ]]
        }
        send_telegram_message(f"- {pos['symbol']} @ {pos['entry_price']:.2f}\nTP: {pos['tp_price']:.2f}, SL: {pos['sl_price']:.2f}", keyboard)

@app.route(f"/bot{TELEGRAM_TOKEN}", methods=['POST'])
def telegram_webhook():
    data = request.json
    if "callback_query" in data:
        query = data["callback_query"]
        chat_id = query["message"]["chat"].get("id")
        action = query["data"]

        if action.startswith("close_"):
            unit_id = int(action.split("_")[1])
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            confirm = {
                "chat_id": chat_id,
                "text": f"هل أنت متأكد من إغلاق الوحدة {unit_id}؟",
                "reply_markup": json.dumps({
                    "inline_keyboard": [[
                        {"text": "نعم، أغلق الصفقة", "callback_data": f"confirm_close_{unit_id}"},
                        {"text": "إلغاء", "callback_data": "cancel"}
                    ]]
                })
            }
            requests.post(url, json=confirm)

        elif action.startswith("confirm_close_"):
            unit_id = int(action.split("_")[2])
            for pos in open_positions:
                if pos['unit_id'] == unit_id and pos['active']:
                    pos['active'] = False
                    units.append(pos['amount'])
                    send_telegram_message(f"⛔️ تم إغلاق صفقة {pos['symbol']} يدويًا")

        elif action == "cancel":
            send_telegram_message("❌ تم إلغاء الإجراء")

    elif "message" in data:
        message = data["message"].get("text", "")
        if message == "/start":
            send_telegram_message("✅ تم تشغيل البوت بنجاح!")
            report_open_positions()
        elif message == "Positions":
            report_open_positions()

    return "ok"

# دالة لتشغيل البوت
def run_bot():
    global last_report_time
    send_telegram_message("🚀 البوت يعمل الآن...")
    symbols = get_all_usdt_symbols()
    ...


    while True:
        try:
            update_trades()
            for symbol in symbols:
                if not units:
                    break
                score = get_strategy_score(symbol)
                if score >= 4.5:
                    price = get_price(symbol)
                    if not price:
                        continue
                    unit_id = len(open_positions) + 1
                    amount = units.pop(0)
                    enter_trade(symbol, amount, price, unit_id)
            if time.time() - last_report_time >= report_interval:
                report_open_positions()
                last_report_time = time.time()
        except Exception as e:
            send_telegram_message(f"❌ خطأ في البوت: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host='0.0.0.0', port=8080)
