import os
import time
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error

# ==============================================================================
# HỆ THỐNG BOT VÀNG CLOUD 24/7 TÍCH HỢP ĐẦY ĐỦ 5 KHO KỸ NĂNG THỰC CHIẾN (RENDER)
# Tận dụng tài nguyên Render (512MB RAM) chạy thuật toán AI phân tích đa khung thời gian:
# 1. Lý Thuyết Dow (Thủy triều H1, Sóng M15, BOS / CHoCH)
# 2. VWAP 6 Dải Băng Chuẩn (Dây thun đàn hồi ±1, ±2, ±3 SD)
# 3. Smart Money Concepts (SMC): FVG (Mất cân bằng) & Quét Thanh Khoản (Liquidity Sweep)
# 4. Wick Trap (Nến từ chối giá chân dài tại vùng then chốt)
# 5. Ma Trận Thang Điểm Hợp Lực 10 Điểm (Chỉ bắn lệnh khi Confluence >= 8.5)
# ==============================================================================

TELEGRAM_BOT_TOKEN = "9020322397:AAH_D4qUOGejUjONLqWVPv8EiUm5RK777lQ"
TELEGRAM_CHAT_ID = "1354734957"
SCAN_INTERVAL_SECONDS = 1800  # Quét định kỳ 30 phút / lần
EXNESS_OFFSET = -10.73         # Độ lệch chuẩn giữa sàn thế giới và Exness Cent
MIN_SCORE_A_PLUS = 8.5        # Điểm hợp lực tối thiểu để ra lệnh (Kèo A+)
COOLDOWN_HOURS = 1.0           # Khóa chống đổi chiều ngược liên tục trong 1 tiếng

# Trạng thái theo dõi thị trường
state = {
    "last_signal_dir": None,
    "last_signal_time": 0,
    "last_alert_price": 0.0,
    "scan_count": 0
}

def send_telegram(text):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as response:
            pass
    except Exception as e:
        print(f"Lỗi gửi Telegram: {e}")

def fetch_candles(interval="15m", limit=50):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval={interval}&limit={limit}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode('utf-8'))
        candles = []
        for c in data:
            candles.append({
                "time": int(c[0]),
                "open": float(c[1]) + EXNESS_OFFSET,
                "high": float(c[2]) + EXNESS_OFFSET,
                "low": float(c[3]) + EXNESS_OFFSET,
                "close": float(c[4]) + EXNESS_OFFSET,
                "volume": float(c[5])
            })
        return candles
    except Exception as e:
        print(f"Lỗi nạp dữ liệu nến {interval}: {e}")
        return None

# ----------------- 1. THUẬT TOÁN VWAP & 6 DẢI BĂNG (RUBBER BAND) -----------------
def calc_vwap_6bands(candles, period=24):
    subset = candles[-period:]
    cum_pv = sum([(c["high"] + c["low"] + c["close"]) / 3.0 * c["volume"] for c in subset])
    cum_vol = sum([c["volume"] for c in subset])
    vwap = cum_pv / (cum_vol + 1e-6)
    variance = sum([((c["close"] - vwap) ** 2) * c["volume"] for c in subset]) / (cum_vol + 1e-6)
    sd = variance ** 0.5
    return {
        "vwap": vwap,
        "upper1": vwap + 1.0 * sd,
        "upper2": vwap + 2.0 * sd,
        "upper3": vwap + 3.0 * sd,
        "lower1": vwap - 1.0 * sd,
        "lower2": vwap - 2.0 * sd,
        "lower3": vwap - 3.0 * sd,
        "sd": sd
    }

# ----------------- 2. THUẬT TOÁN LÝ THUYẾT DOW & CẤU TRÚC SÓNG -----------------
def analyze_dow_structure(candles):
    pivots_high = []
    pivots_low = []
    for i in range(2, len(candles) - 2):
        if candles[i]["high"] > candles[i-1]["high"] and candles[i]["high"] > candles[i-2]["high"] and \
           candles[i]["high"] > candles[i+1]["high"] and candles[i]["high"] > candles[i+2]["high"]:
            pivots_high.append(candles[i]["high"])
        if candles[i]["low"] < candles[i-1]["low"] and candles[i]["low"] < candles[i-2]["low"] and \
           candles[i]["low"] < candles[i+1]["low"] and candles[i]["low"] < candles[i+2]["low"]:
            pivots_low.append(candles[i]["low"])

    trend = "NEUTRAL"
    bos_event = None
    if len(pivots_high) >= 2 and len(pivots_low) >= 2:
        is_hh = pivots_high[-1] > pivots_high[-2]
        is_hl = pivots_low[-1] > pivots_low[-2]
        is_lh = pivots_high[-1] < pivots_high[-2]
        is_ll = pivots_low[-1] < pivots_low[-2]
        
        if is_hh and is_hl:
            trend = "BULLISH"
        elif is_lh and is_ll:
            trend = "BEARISH"
            
        cur_price = candles[-1]["close"]
        if cur_price > pivots_high[-1]:
            bos_event = "BOS_BULLISH"
        elif cur_price < pivots_low[-1]:
            bos_event = "BOS_BEARISH"

    return {"trend": trend, "bos": bos_event, "last_high": pivots_high[-1] if pivots_high else 0, "last_low": pivots_low[-1] if pivots_low else 0}

# ----------------- 3. THUẬT TOÁN SMC (FVG & LIQUIDITY SWEEP) -----------------
def detect_smc_fvg_and_traps(candles):
    fvg_bullish = False
    fvg_bearish = False
    if len(candles) >= 3:
        if candles[-1]["low"] > candles[-3]["high"]:
            fvg_bullish = True
        if candles[-1]["high"] < candles[-3]["low"]:
            fvg_bearish = True

    c = candles[-1]
    body = abs(c["close"] - c["open"])
    upper_wick = c["high"] - max(c["close"], c["open"])
    lower_wick = min(c["close"], c["open"]) - c["low"]
    
    bullish_wick_trap = lower_wick > (body * 1.8) and lower_wick > 1.2
    bearish_wick_trap = upper_wick > (body * 1.8) and upper_wick > 1.2

    return {
        "fvg_bullish": fvg_bullish,
        "fvg_bearish": fvg_bearish,
        "bullish_wick": bullish_wick_trap,
        "bearish_wick": bearish_wick_trap
    }

# ----------------- 4. BỘ NÃO TỔNG HỢP & CHẤM ĐIỂM HỢP LỰC -----------------
def run_full_confluence_scan():
    state["scan_count"] += 1
    m15_candles = fetch_candles("15m", 50)
    h1_candles = fetch_candles("1h", 30)
    
    if not m15_candles or not h1_candles:
        print("Không đủ dữ liệu thị trường để phân tích.")
        return None

    current_price = m15_candles[-1]["close"]
    cur_time = time.time()
    
    vwap_data = calc_vwap_6bands(m15_candles)
    dow_m15 = analyze_dow_structure(m15_candles)
    dow_h1 = analyze_dow_structure(h1_candles)
    smc = detect_smc_fvg_and_traps(m15_candles)

    # Đánh giá cơ hội BUY
    buy_score = 0.0
    buy_reasons = []
    
    if dow_h1["trend"] == "BULLISH":
        buy_score += 2.0
        buy_reasons.append("Thủy triều H1 tăng dốc vững chắc (Lý thuyết Dow)")
    elif dow_h1["trend"] == "NEUTRAL":
        buy_score += 1.0

    if dow_m15["bos"] == "BOS_BULLISH":
        buy_score += 2.0
        buy_reasons.append("BOS phá vỡ đỉnh M15 thành công")
    elif dow_m15["trend"] == "BULLISH":
        buy_score += 1.5
        buy_reasons.append("Cấu trúc M15 duy trì Đáy cao dần (Higher Low)")

    if current_price <= vwap_data["lower2"]:
        buy_score += 2.5
        buy_reasons.append(f"Dây thun kéo căng Band -2 (Vùng Rẻ ${vwap_data['lower2']:.2f})")
    elif current_price <= vwap_data["lower1"]:
        buy_score += 1.5
        buy_reasons.append("Hồi quy chuẩn về Band -1 quanh VWAP")

    if smc["bullish_wick"]:
        buy_score += 2.0
        buy_reasons.append("Nến Rút Chân Quét Đáy (Wick Trap săn thanh khoản phe bán)")
    if smc["fvg_bullish"]:
        buy_score += 1.0
        buy_reasons.append("Xuất hiện khối FVG Mất cân bằng mua")

    # Đánh giá cơ hội SELL
    sell_score = 0.0
    sell_reasons = []

    if dow_h1["trend"] == "BEARISH":
        sell_score += 2.0
        sell_reasons.append("Thủy triều H1 giảm chủ đạo (Lý thuyết Dow)")
    elif dow_h1["trend"] == "NEUTRAL":
        sell_score += 1.0

    if dow_m15["bos"] == "BOS_BEARISH":
        sell_score += 2.0
        sell_reasons.append("BOS thủng đáy M15 kích hoạt sóng bán")
    elif dow_m15["trend"] == "BEARISH":
        sell_score += 1.5
        sell_reasons.append("Cấu trúc M15 duy trì Đỉnh thấp dần (Lower High)")

    if current_price >= vwap_data["upper2"]:
        sell_score += 2.5
        sell_reasons.append(f"Dây thun căng cực đại Band +2 (Vùng Đắt ${vwap_data['upper2']:.2f})")
    elif current_price >= vwap_data["upper1"]:
        sell_score += 1.5
        sell_reasons.append("Chạm dải Upper Band +1 cản trên")

    if smc["bearish_wick"]:
        sell_score += 2.0
        sell_reasons.append("Nến Rút Râu Quét Đỉnh (Wick Trap săn thanh khoản phe mua)")
    if smc["fvg_bearish"]:
        sell_score += 1.0
        sell_reasons.append("Xuất hiện khối FVG Mất cân bằng bán")

    direction = None
    final_score = 0.0
    active_reasons = []

    if buy_score >= sell_score and buy_score >= MIN_SCORE_A_PLUS:
        direction = "BUY"
        final_score = min(buy_score, 10.0)
        active_reasons = buy_reasons
    elif sell_score > buy_score and sell_score >= MIN_SCORE_A_PLUS:
        direction = "SELL"
        final_score = min(sell_score, 10.0)
        active_reasons = sell_reasons

    print(f"[{time.strftime('%H:%M:%S')}] Quét #{state['scan_count']} | Giá: ${current_price:.2f} | BUY: {buy_score:.1f} | SELL: {sell_score:.1f} | Kết quả: {direction or 'ĐỨNG NGOÀI (Chưa đủ chuẩn A+ >= 8.5)'}")

    if not direction:
        return None

    # Khóa chống lật chiều trong 1 tiếng
    if state["last_signal_dir"] and (cur_time - state["last_signal_time"] < COOLDOWN_HOURS * 3600):
        if direction != state["last_signal_dir"]:
            print(f"[{time.strftime('%H:%M:%S')}] Khóa Cooldown: Bỏ qua lệnh {direction} do vừa có lệnh {state['last_signal_dir']}.")
            return None

    state["last_signal_dir"] = direction
    state["last_signal_time"] = cur_time
    state["last_alert_price"] = current_price

    if direction == "BUY":
        sl = current_price - 2.80   # 28 pips
        tp1 = current_price + 6.50  # 65 pips (R:R 1:2.3)
        tp2 = current_price + 13.50 # 135 pips (R:R 1:4.8)
        tp3 = current_price + 22.00 # 220 pips
    else:
        sl = current_price + 2.80   # 28 pips
        tp1 = current_price - 6.50  # 65 pips
        tp2 = current_price - 13.50 # 135 pips
        tp3 = current_price - 22.00 # 220 pips

    return {
        "direction": direction,
        "score": final_score,
        "price": current_price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "reasons": active_reasons,
        "vwap": vwap_data["vwap"]
    }

# ----------------- 5. CHU KỲ QUÉT TỰ ĐỘNG MỖI 30 PHÚT -----------------
def auto_scan_loop():
    while True:
        try:
            signal = run_full_confluence_scan()
            if signal:
                icon = "🟢" if signal["direction"] == "BUY" else "🔴"
                action_text = "BUY LÊN (VÙNG CẦU)" if signal["direction"] == "BUY" else "SELL XUỐNG (VÙNG CUNG)"
                reasons_str = "\n".join([f"  ✓ {r}" for r in signal["reasons"][:4]])

                alert_msg = (
                    f"🏆 <b>KÈO VÀNG A+ (HỘI TỤ ĐỦ 5 KỸ NĂNG)</b> 🏆\n"
                    f"═══════════════════════\n"
                    f"• Lệnh: {icon} <b>{action_text}</b>\n"
                    f"• Điểm Hợp Lực: <b>★ {signal['score']:.1f}/10 (SIÊU PHẨM)</b>\n"
                    f"• Giá Khớp Exness: <b>${signal['price']:.2f}</b>\n"
                    f"• Trục VWAP Ngày: <b>${signal['vwap']:.2f}</b>\n\n"
                    f"📊 <b>LUẬN ĐIỂM KỸ THUẬT XÁC NHẬN:</b>\n"
                    f"{reasons_str}\n\n"
                    f"🎯 <b>THÔNG SỐ VÀO LỆNH (EXECUTION):</b>\n"
                    f"• Vùng Entry: <code>${signal['price']:.2f}</code>\n"
                    f"• Cắt Lỗ (SL): <code>${signal['sl']:.2f}</code> (-28 pips)\n"
                    f"• Chốt Lời 1 (TP1): <code>${signal['tp1']:.2f}</code> (+65 pips)\n"
                    f"• Chốt Lời 2 (TP2): <code>${signal['tp2']:.2f}</code> (+135 pips)\n"
                    f"• Chốt Lời 3 (TP3): <code>${signal['tp3']:.2f}</code> (Runner)\n"
                    f"• Tỷ Lệ R:R: <b>1 : 2.5 ~ 1 : 4.8</b>\n\n"
                    f"🛡️ <b>QUY TẮC QUẢN TRỊ VỐN:</b>\n"
                    f"• Khối lượng: 0.01 - 0.02 Lot (Tài khoản Cent/Standard)\n"
                    f"• Khi giá chạy được +25 pips ➜ <b>Dời SL về hòa vốn (BE)</b> ngay lập tức!\n"
                    f"• Chốt 70% khối lượng tại TP1, thả trôi 30% còn lại."
                )
                send_telegram(alert_msg)
        except Exception as e:
            print(f"Lỗi trong vòng lặp quét: {e}")
            
        time.sleep(SCAN_INTERVAL_SECONDS)

# ----------------- 6. SERVER HTTP MỞ CỔNG CHO RENDER -----------------
class RenderHealthServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.end_headers()
        res = {
            "status": "ONLINE_24_7",
            "system": "XAU/USD Gold AI Architecture Full 5 Skills",
            "render_ram_available": "512MB",
            "scan_interval": f"{SCAN_INTERVAL_SECONDS}s (30 mins)",
            "min_confluence": f"{MIN_SCORE_A_PLUS}/10",
            "scans_completed": state["scan_count"],
            "last_signal": state["last_signal_dir"] or "NONE",
            "last_price": state["last_alert_price"]
        }
        self.wfile.write(json.dumps(res, indent=2).encode('utf-8'))
        
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    httpd = HTTPServer(('0.0.0.0', port), RenderHealthServer)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    startup_msg = (
        f"👑 <b>HỆ THỐNG BOT VÀNG CLOUD 24/7 (RENDER 512MB) ĐÃ KÍCH HOẠT ĐẦY ĐỦ 5 KỸ NĂNG!</b>\n\n"
        f"• <b>Lý Thuyết Dow:</b> Theo dõi Thủy triều H1 & BOS M15.\n"
        f"• <b>VWAP 6 Bands:</b> Đo độ căng dây thun (Bẫy giá rẻ/đắt).\n"
        f"• <b>SMC & Wick Trap:</b> Bắt trọn nến rút râu quét thanh khoản.\n"
        f"• <b>Thang Điểm Hợp Lực:</b> Chỉ báo khi đạt từ <b>★ 8.5/10</b> trở lên.\n"
        f"• <b>Chống Nhiễu:</b> Khóa đảo chiều trong 60 phút.\n"
        f"• <b>Chu Kỳ Quét:</b> 30 phút/lần 24/7 (Tắt máy tính vẫn hoạt động 100%)."
    )
    send_telegram(startup_msg)
    print("Bot Render Full 5 Skills đang chạy...")
    auto_scan_loop()
