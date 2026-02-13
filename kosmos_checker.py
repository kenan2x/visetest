#!/usr/bin/env python3
"""
Kosmos Vize (Yunanistan) Randevu Müsaitlik Checker
Playwright ile Cloudflare bypass yaparak API'den slot kontrolü yapar.
"""

import asyncio
import json
import os
import sys
import signal
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

# ── Ayarlar ──────────────────────────────────────────────────────────
CHECK_INTERVAL_SECONDS = 300  # 5 dakika
DAYS_AHEAD = 60
ORIGIN = "https://basvuru.kosmosvize.com.tr"
API_URL = "https://api.kosmosvize.com.tr/api/AppointmentLayouts/GetAppointmentHourQoutaInfo"

DEALERS = {
    1: "İstanbul",
    2: "Bursa",
    3: "Trabzon",
}

APPOINTMENT_TYPES = {
    16: "Standart",
    18: "VIP",
}

# Varsayılan parametreler (env ile override edilebilir)
NATIONALITY_NUMBER = os.environ.get("NATIONALITY_NUMBER", "")
APPOINTMENT_TYPE_ID = int(os.environ.get("APPOINTMENT_TYPE_ID", "16"))
APPLICATION_TYPE = int(os.environ.get("APPLICATION_TYPE", "1"))

# Telegram bildirimi (opsiyonel)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# WhatsApp bildirimi - CallMeBot (opsiyonel)
WHATSAPP_PHONE = os.environ.get("WHATSAPP_PHONE", "")  # ör: 905551234567
WHATSAPP_APIKEY = os.environ.get("WHATSAPP_APIKEY", "")

# ntfy.sh push notification
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "vizexkk-test")

# ── Renk kodları ─────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log(msg, color=""):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{CYAN}[{ts}]{RESET} {color}{msg}{RESET}", flush=True)


async def send_ntfy(message: str, title="Kosmos Vize"):
    """ntfy.sh push notification gönder."""
    if not NTFY_TOPIC:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": "passport"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            log("ntfy bildirimi gönderildi.", GREEN)
        else:
            log(f"ntfy yanıt: {resp.status}", RED)
    except Exception as e:
        log(f"ntfy hatası: {e}", RED)


async def send_whatsapp(message: str, page=None):
    """WhatsApp bildirimi gönder (CallMeBot API)."""
    if not WHATSAPP_PHONE or not WHATSAPP_APIKEY:
        return
    try:
        import urllib.parse
        encoded = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={WHATSAPP_PHONE}&text={encoded}&apikey={WHATSAPP_APIKEY}"
        if page:
            result = await page.evaluate(
                """async (url) => {
                    try {
                        const r = await fetch(url);
                        return {ok: r.ok, status: r.status};
                    } catch(e) { return {error: e.message}; }
                }""",
                url,
            )
        else:
            # Tarayıcı olmadan (test modu için)
            import urllib.request
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=15)
            result = {"ok": resp.status == 200, "status": resp.status}
        if result.get("ok"):
            log("WhatsApp bildirimi gönderildi.", GREEN)
        else:
            log(f"WhatsApp yanıt: {result}", RED)
    except Exception as e:
        log(f"WhatsApp hatası: {e}", RED)


async def send_telegram(message: str, page):
    """Telegram bildirimi gönder (page.evaluate ile fetch kullanarak)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        await page.evaluate(
            """async ([url, chatId, text]) => {
                await fetch(url, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({chat_id: chatId, text: text, parse_mode: 'HTML'})
                });
            }""",
            [tg_url, TELEGRAM_CHAT_ID, message],
        )
        log("Telegram bildirimi gönderildi.", GREEN)
    except Exception as e:
        log(f"Telegram hatası: {e}", RED)


async def send_notification(message: str, page=None):
    """Tüm aktif kanallara bildirim gönder."""
    await send_ntfy(message)
    await send_whatsapp(message, page)
    if page:
        await send_telegram(message, page)


async def check_date(page, dealer_id: int, date_str: str) -> list:
    """Tek bir tarih için API'yi çağır, müsait slotları döndür."""
    params = {
        "nationalityNumber": NATIONALITY_NUMBER,
        "dealerId": dealer_id,
        "date": date_str,
        "appointmentTypeId": APPOINTMENT_TYPE_ID,
        "onlyAvailable": True,
        "applicationType": APPLICATION_TYPE,
    }

    result = await page.evaluate(
        """async ([url, params]) => {
            const qs = new URLSearchParams();
            for (const [k, v] of Object.entries(params)) {
                qs.append(k, String(v));
            }
            try {
                const resp = await fetch(url + '?' + qs.toString(), {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'Origin': '""" + ORIGIN + """'
                    },
                    credentials: 'include'
                });
                if (!resp.ok) return {error: resp.status, text: await resp.text()};
                return await resp.json();
            } catch(e) {
                return {error: e.message};
            }
        }""",
        [API_URL, params],
    )

    if isinstance(result, dict) and "error" in result:
        return []

    # API yanıtı: liste veya obje içinde liste olabilir
    slots = []
    items = result if isinstance(result, list) else result.get("data", result.get("items", []))
    if isinstance(items, list):
        for item in items:
            if item.get("isAvailable") or item.get("quotaCount", 0) > 0:
                slots.append(item)

    return slots


async def run_check_cycle(page):
    """Tüm dealer'lar ve tarihler için tek bir tarama döngüsü."""
    today = datetime.now().date()
    found_any = False
    total_checks = 0

    for dealer_id, dealer_name in DEALERS.items():
        log(f"Taranıyor: {BOLD}{dealer_name}{RESET} (dealerId={dealer_id})")

        for day_offset in range(DAYS_AHEAD):
            check_date_obj = today + timedelta(days=day_offset)
            date_str = check_date_obj.strftime("%Y/%m/%d")
            total_checks += 1

            try:
                slots = await check_date(page, dealer_id, date_str)
            except Exception as e:
                log(f"  Hata ({dealer_name} {date_str}): {e}", RED)
                continue

            if slots:
                found_any = True
                appt_type = APPOINTMENT_TYPES.get(APPOINTMENT_TYPE_ID, str(APPOINTMENT_TYPE_ID))
                msg = (
                    f"{GREEN}{BOLD}★ SLOT BULUNDU! ★{RESET}\n"
                    f"  {BOLD}Ofis:{RESET} {dealer_name}\n"
                    f"  {BOLD}Tarih:{RESET} {date_str}\n"
                    f"  {BOLD}Tip:{RESET} {appt_type}\n"
                    f"  {BOLD}Slot sayısı:{RESET} {len(slots)}"
                )
                log(msg, GREEN)

                for s in slots:
                    hour = s.get("hour") or s.get("appointmentHour") or s.get("time", "?")
                    quota = s.get("quotaCount") or s.get("availableCount", "?")
                    log(f"    Saat: {hour} | Kota: {quota}", GREEN)

                # Bildirim gönder
                notify_msg = (
                    f"🟢 Kosmos Vize - Slot Bulundu!\n"
                    f"📍 Ofis: {dealer_name}\n"
                    f"📅 Tarih: {date_str}\n"
                    f"📋 Tip: {appt_type}\n"
                    f"🔢 Slot: {len(slots)}"
                )
                await send_notification(notify_msg, page)

        log(f"  {dealer_name} tamamlandı.", YELLOW)

    if not found_any:
        log(f"Hiç müsait slot bulunamadı. (Toplam {total_checks} tarih kontrol edildi)", RED)
        await send_ntfy(
            f"❌ Müsait slot bulunamadı.\n3 ofis, {total_checks} tarih tarandı.\nSonraki kontrol {CHECK_INTERVAL_SECONDS // 60} dk sonra.",
            title="Kosmos Vize - Slot Yok",
        )

    return found_any


async def main():
    log(f"{BOLD}Kosmos Vize Randevu Checker başlatılıyor...{RESET}", CYAN)
    log(f"Randevu tipi: {APPOINTMENT_TYPES.get(APPOINTMENT_TYPE_ID, APPOINTMENT_TYPE_ID)}", CYAN)
    log(f"Başvuru tipi: {'Bireysel' if APPLICATION_TYPE == 1 else 'Aile'}", CYAN)
    log(f"Kontrol aralığı: {CHECK_INTERVAL_SECONDS}s | Gün aralığı: {DAYS_AHEAD}", CYAN)
    if NATIONALITY_NUMBER:
        log(f"TC: {'*' * (len(NATIONALITY_NUMBER) - 3)}{NATIONALITY_NUMBER[-3:]}", CYAN)
    else:
        log("TC numarası belirtilmedi (NATIONALITY_NUMBER env). Boş olarak devam ediliyor.", YELLOW)
    if NTFY_TOPIC:
        log(f"ntfy bildirimleri aktif. Topic: {NTFY_TOPIC}", GREEN)
    if WHATSAPP_PHONE and WHATSAPP_APIKEY:
        log("WhatsApp bildirimleri aktif.", GREEN)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        log("Telegram bildirimleri aktif.", GREEN)

    # --test flag'i: sadece bildirim testi yap, çık
    if "--test" in sys.argv:
        log("Test modu: bildirim gönderiliyor...", CYAN)
        await send_ntfy("✅ Kosmos Vize Checker test mesajı - bildirimler çalışıyor!")
        return

    async with async_playwright() as pw:
        # headless=False gerçek ekran gerektirir; HEADLESS=1 env ile override edilebilir
        use_headless = os.environ.get("HEADLESS", "0") == "1"
        browser = await pw.chromium.launch(
            headless=use_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="tr-TR",
            viewport={"width": 1280, "height": 720},
        )

        # Webdriver flag'ini gizle
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        page = await context.new_page()

        # ── Cloudflare challenge'ı geç ──
        cycle = 0
        while True:
            cycle += 1
            log(f"{BOLD}── Döngü #{cycle} ──{RESET}", CYAN)

            log("Cloudflare challenge geçiliyor...", YELLOW)
            try:
                await page.goto(ORIGIN, wait_until="networkidle", timeout=60000)
            except Exception:
                log("Sayfa yükleme timeout, devam ediliyor...", YELLOW)

            # Cloudflare'in çözülmesi için bekle
            await asyncio.sleep(8)

            # Cloudflare turnstile/checkbox varsa tıklamayı dene
            try:
                cf_frame = page.frame_locator("iframe[src*='challenges.cloudflare.com']")
                checkbox = cf_frame.locator("input[type='checkbox'], .cb-lb")
                if await checkbox.count() > 0:
                    log("Cloudflare checkbox bulundu, tıklanıyor...", YELLOW)
                    await checkbox.first.click()
                    await asyncio.sleep(5)
            except Exception:
                pass

            # Challenge geçti mi kontrol et
            await asyncio.sleep(3)
            current_url = page.url
            title = await page.title()
            log(f"Sayfa: {current_url} | Başlık: {title}", CYAN)

            # Cookie'leri kontrol et
            cookies = await context.cookies()
            cf_cookies = [c for c in cookies if "cf_clearance" in c["name"].lower() or "cf" in c["name"].lower()]
            if cf_cookies:
                log(f"Cloudflare cookie'leri alındı: {[c['name'] for c in cf_cookies]}", GREEN)
            else:
                log("Cloudflare cookie bulunamadı ama devam ediyoruz...", YELLOW)

            # ── API taraması ──
            log(f"{BOLD}API taraması başlıyor...{RESET}", CYAN)
            try:
                await run_check_cycle(page)
            except Exception as e:
                log(f"Tarama hatası: {e}", RED)

            # --once flag'i: tek sefer çalış, çık
            if "--once" in sys.argv:
                log("Tek seferlik çalışma tamamlandı.", GREEN)
                break

            log(f"Sonraki kontrol {CHECK_INTERVAL_SECONDS}s sonra...", YELLOW)
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\nChecker durduruldu.", YELLOW)
        sys.exit(0)
