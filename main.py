import os
import time
import requests
import math
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# CẤU HÌNH BOT TELEGRAM XAU/USD EXNESS 24/7
BOT_TOKEN = "9020322397:AAH_D4qUOGejUjONLqWVPv8EiUm5RK777lQ"
CHAT_ID = "1354734957"
SPREAD_OFFSET = -10.73   # Chuẩn hoá giá Exness Cent XAUUSDc
SCAN_INTERVAL_SEC = 1800  # QUÉT ĐÚNG 30 PHÚT 1 LẦN THEO YÊU CẦU

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot XAUUSD Exness 24/7 Online")

def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()

def send_telegram(message: str):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")
        return False

def get_live_klines():
    try:
        url = "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=15m&limit=60"
        data = requests.get(url, timeout=10).json()
        closes = [float(k[4]) + SPREAD_OFFSET for k in data]
        return closes
    except Exception as e:
        print(f"Lỗi nến: {e}")
        return None

def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = prices[-i] - prices[-i - 1]
        if diff >= 0:
            gains.append(diff); losses.append(0)
        else:
            gains.append(0); losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def run_scanner_tick():
    closes = get_live_klines()
    if not closes: return
    current_price = closes[-1]
    rsi = calculate_rsi(closes, 14)

    recent = closes[-20:]
    sma20 = sum(recent) / len(recent)
    variance = sum((x - sma20) ** 2 for x in recent) / len(recent)
    std_dev = math.sqrt(variance)
    bb_upper = sma20 + 2 * std_dev
    bb_lower = sma20 - 2 * std_dev

    signal = None
    reason = ""
    score = 7

    if current_price > bb_upper and rsi > 68:
        signal = "SELL"
        score = 9
        reason = f"Dây thun kéo căng đỉnh BB Upper ({bb_upper:.2f}) + RSI quá mua ({rsi:.1f})"
    elif current_price < bb_lower and rsi < 32:
        signal = "BUY"
        score = 9
        reason = f"Dây thun kéo căng đáy BB Lower ({bb_lower:.2f}) + RSI quá bán ({rsi:.1f})"
    elif current_price < sma20 and rsi < 45:
        signal = "SELL"
        score = 8
        reason = f"Thuận xu hướng giảm dưới SMA20 + RSI {rsi:.1f}"
    elif current_price > sma20 and rsi > 55:
        signal = "BUY"
        score = 8
        reason = f"Thuận xu hướng tăng trên SMA20 + RSI {rsi:.1f}"

    if signal and score >= 8:
        sl = current_price + 3.5 if signal == "SELL" else current_price - 3.5
        tp1 = current_price - 4.0 if signal == "SELL" else current_price + 4.0
        tp2 = current_price - 8.0 if signal == "SELL" else current_price + 8.0

        msg = (
            f"🚨 *TÍN HIỆU VÀNG 24/7 (EXNESS XAUUSDC)* 🚨\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 Lệnh: *{signal} XAU/USD*\n"
            f"⭐ Điểm hội tụ: *{score}/10*\n"
            f"💵 Giá Exness: *${current_price:.2f}*\n"
            f"🛑 Stop Loss: *${sl:.2f}*\n"
            f"🎯 Take Profit 1: *${tp1:.2f}*\n"
            f"🎯 Take Profit 2: *${tp2:.2f}*\n"
            f"💡 Lý do: {reason}\n"
            f"🕒 Quét lúc: {time.strftime('%H:%M:%S %d/%m/%Y')} (Mỗi 30 phút)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 _Bot AI Server Render 24/7 (Tắt máy vẫn nhận lệnh)_"
        )
        send_telegram(msg)
        print(f"[{time.strftime('%H:%M:%S')}] Đã gửi Telegram {signal} tại {current_price:.2f}")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Quét xong: Giá ${current_price:.2f} | RSI: {rsi:.1f} | Chưa có kèo đủ chuẩn 8/10.")

if __name__ == "__main__":
    print("=== BOT QUÉT XAU/USD 24/7 BẮT ĐẦU (CHU KỲ 30 PHÚT) ===")
    threading.Thread(target=start_health_server, daemon=True).start()
    send_telegram("🚀 *Hệ thống AI Quét Vàng 24/7 Đã Khởi Động!*\nBot sẽ tự động quét nến M15 mỗi 30 phút và gửi tín hiệu vào Telegram này khi có kèo chuẩn.")
    while True:
        try:
            run_scanner_tick()
        except Exception as e:
            print(f"Lỗi: {e}")
        time.sleep(SCAN_INTERVAL_SEC)
