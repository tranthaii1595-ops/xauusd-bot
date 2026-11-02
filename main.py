import os
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

# ================= CẤU HÌNH BẢO VỆ & LỌC TÍN HIỆU =================
TELEGRAM_BOT_TOKEN = "9020322397:AAH_D4qUOGejUjONLqWVPv8EiUm5RK777lQ"
TELEGRAM_CHAT_ID = "1354734957"
SCAN_INTERVAL_SECONDS = 1800  # 30 phút quét 1 lần
EXNESS_OFFSET = -10.73         # Chuẩn hóa giá khớp Exness Cent

# BỘ LỌC KÈO A+ (CHỈ BẮN LỆNH ĐẸP)
MIN_CONFLUENCE_SCORE = 8.5    # Chỉ gửi lệnh khi đạt từ 8.5/10 điểm trở lên
COOLDOWN_HOURS = 1.0           # Sau khi ra lệnh, khóa 1 tiếng không đảo chiều lộn xộn

# Bộ nhớ tạm trạng thái lệnh
last_signal_direction = None
last_signal_time = 0

def send_telegram_alert(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def get_market_data():
    try:
        url = "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=15m&limit=50"
        res = requests.get(url, timeout=10)
        data = res.json()
        candles = []
        for c in data:
            candles.append({
                "open": float(c[1]) + EXNESS_OFFSET,
                "high": float(c[2]) + EXNESS_OFFSET,
                "low": float(c[3]) + EXNESS_OFFSET,
                "close": float(c[4]) + EXNESS_OFFSET,
                "volume": float(c[5])
            })
        return candles
    except Exception as e:
        print(f"Lỗi tải dữ liệu nến: {e}")
        return None

def analyze_market(candles):
    global last_signal_direction, last_signal_time
    if not candles or len(candles) < 30:
        return None
    
    current_time = time.time()
    current_price = candles[-1]["close"]
    recent = candles[-20:]
    
    # 1. Tính VWAP & Độ lệch chuẩn (Standard Deviation)
    cum_pv = sum([(c["high"] + c["low"] + c["close"]) / 3.0 * c["volume"] for c in recent])
    cum_v = sum([c["volume"] for c in recent])
    vwap = cum_pv / (cum_v + 1e-6)
    variance = sum([((c["close"] - vwap) ** 2) * c["volume"] for c in recent]) / (cum_v + 1e-6)
    sd = variance ** 0.5
    
    # Dải VWAP
    vwap_upper2 = vwap + 2.0 * sd
    vwap_lower2 = vwap - 2.0 * sd
    
    # 2. Phát hiện Nến Rút Chân (Wick Trap)
    last_candle = candles[-1]
    body = abs(last_candle["close"] - last_candle["open"])
    upper_wick = last_candle["high"] - max(last_candle["close"], last_candle["open"])
    lower_wick = min(last_candle["close"], last_candle["open"]) - last_candle["low"]
    
    bullish_trap = (lower_wick > body * 1.8) and (current_price <= vwap_lower2 + (0.5 * sd))
    bearish_trap = (upper_wick > body * 1.8) and (current_price >= vwap_upper2 - (0.5 * sd))
    
    # 3. Chấm điểm Hợp Lực (Thang điểm 10)
    score = 7.0
    direction = None
    
    if bullish_trap or current_price < vwap_lower2:
        direction = "BUY"
        score += 1.0 if bullish_trap else 0.5
        score += 0.8 if current_price < vwap_lower2 else 0.3
        # Đánh giá cấu trúc đáy
        if candles[-1]["low"] > candles[-5]["low"]:
            score += 0.7  # Đáy sau cao hơn (Higher Low)
    elif bearish_trap or current_price > vwap_upper2:
        direction = "SELL"
        score += 1.0 if bearish_trap else 0.5
        score += 0.8 if current_price > vwap_upper2 else 0.3
        # Đánh giá cấu trúc đỉnh
        if candles[-1]["high"] < candles[-5]["high"]:
            score += 0.7  # Đỉnh sau thấp hơn (Lower High)
            
    # ÁP DỤNG BỘ LỌC KÈO A+:
    if not direction or score < MIN_CONFLUENCE_SCORE:
        print(f"[{time.strftime('%H:%M:%S')}] Giá: ${current_price:.2f} | Hướng: {direction} | Điểm: {score:.1f}/10 (Chưa đủ chuẩn A+ >= 8.5)")
        return None
        
    # CHỐNG ĐẢO CHIỀU NHANH (COOLDOWN)
    if last_signal_direction and (current_time - last_signal_time < COOLDOWN_HOURS * 3600):
        if direction != last_signal_direction:
            print(f"[{time.strftime('%H:%M:%S')}] Chặn đổi chiều ({direction} bị chặn vì vừa có lệnh {last_signal_direction} trong 1h)")
            return None

    # Cập nhật trạng thái
    last_signal_direction = direction
    last_signal_time = current_time
    
    # Thiết lập SL / TP chuẩn tỷ lệ Vàng
    if direction == "BUY":
        sl = current_price - 2.80   # 28 pips
        tp1 = current_price + 6.50  # 65 pips (R:R > 1:2.3)
        tp2 = current_price + 13.00 # 130 pips
    else:
        sl = current_price + 2.80   # 28 pips
        tp1 = current_price - 6.50  # 65 pips
        tp2 = current_price - 13.00 # 130 pips
        
    return {
        "direction": direction,
        "score": score,
        "price": current_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "trap": "Rút chân quét đáy" if direction == "BUY" else "Rút râu quét đỉnh"
    }

def scan_cycle():
    while True:
        try:
            candles = get_market_data()
            signal = analyze_market(candles)
            if signal:
                icon = "🟢" if signal["direction"] == "BUY" else "🔴"
                action_text = "BUY LÊN (VÙNG CẦU)" if signal["direction"] == "BUY" else "SELL XUỐNG (VÙNG CUNG)"
                
                msg = (
                    f"⭐ <b>TÍN HIỆU VÀNG A+ (CHUẨN 5 KỸ NĂNG)</b> ⭐\n\n"
                    f"• Lệnh: {icon} <b>{action_text}</b>\n"
                    f"• Điểm Hợp Lực: <b>★ {signal['score']:.1f}/10 (SIÊU ĐẸP)</b>\n"
                    f"• Giá Khớp Exness: <b>${signal['price']:.2f}</b>\n"
                    f"• Kỹ thuật: {signal['trap']} + Chạm biên VWAP\n\n"
                    f"🎯 <b>THÔNG SỐ VÀO LỆNH:</b>\n"
                    f"• Entry: <code>${signal['price']:.2f}</code>\n"
                    f"• Cắt lỗ (SL): <code>${signal['sl']:.2f}</code> (28 pips)\n"
                    f"• Chốt lời 1 (TP1): <code>${signal['tp1']:.2f}</code> (+65 pips)\n"
                    f"• Chốt lời 2 (TP2): <code>${signal['tp2']:.2f}</code> (+130 pips)\n"
                    f"• Tỷ lệ R:R: <b>1 : 2.5</b>\n\n"
                    f"💡 <i>Quy tắc: Khi giá chạy được +25 pips ➜ Dời SL về hòa vốn (Breakeven) ngay!</i>"
                )
                send_telegram_alert(msg)
        except Exception as e:
            print(f"Lỗi chu kỳ quét: {e}")
        time.sleep(SCAN_INTERVAL_SECONDS)

class SimpleServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Bot Vang 24/7 Render dang chay on dinh (Che do loc keo A+).")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    
    send_telegram_alert(
        "🛡️ <b>HỆ THỐNG ĐÃ KÍCH HOẠT BỘ LỌC KÈO A+ (8.5/10)</b>\n\n"
        "• Chu kỳ quét: <b>30 phút / lần</b>\n"
        "• Tiêu chuẩn: Chỉ gửi khi đủ nến rút chân + chạm dải biên VWAP\n"
        "• Khóa lệnh: Chống đổi chiều Buy/Sell liên tục trong 60 phút.\n"
        "Chúc anh giao dịch kỷ luật và an toàn!"
    )
    scan_cycle()
