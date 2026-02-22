import os
import asyncio
import httpx
import threading
from dotenv import load_dotenv

load_dotenv()

_stop_polling = asyncio.Event()

# In-memory veritabanı: chat_id -> state dict
user_states = {}

# Sabit buton seçenekleri
DATE_PRESETS = [
    [{"text": "Tüm Zamanlar (Filtresiz)", "callback_data": "date|Yok"}],
    [{"text": "Bugün (+1 Gün)", "callback_data": "date|bugun"}]
]

TIME_PRESETS = [
    [{"text": "Tüm Saatler (Filtresiz)", "callback_data": "time|Yok"}],
    [{"text": "Sadece Sabah (08:00-12:00)", "callback_data": "time|08:00-12:00"}],
    [{"text": "Sadece Öğle/Akşam (13:00-17:00)", "callback_data": "time|13:00-17:00"}]
]

ACTION_PRESETS = [
    [{"text": "💬 Sadece Bildirim Gelsin", "callback_data": "action|notify"}],
    [{"text": "⚡ İlk Bulunanı Otomatik Al", "callback_data": "action|auto_book"}],
    [{"text": "🤖 Telegram'dan Saat Seçtir", "callback_data": "action|ask_telegram"}]
]

async def poll_telegram():
    """Arka planda Telegram sunucularına getUpdates isteği atarak buton tıklamalarını dinler."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("[TELEGRAM] Token bulunamadı, poller başlatılmıyor.")
        return
        
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    offset = 0
    timeout = 30
    
    print("[TELEGRAM] Poller başlatıldı. Buton tıklamaları bekleniyor...")
    
    async with httpx.AsyncClient(timeout=timeout + 5) as client:
        while not _stop_polling.is_set():
            try:
                resp = await client.get(url, params={"offset": offset, "timeout": timeout})
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        for result in data["result"]:
                            offset = result["update_id"] + 1
                            await _handle_update(result, token, client)
            except asyncio.CancelledError:
                break
            except httpx.ReadTimeout:
                # Normal for long polling
                continue
            except Exception as e:
                print(f"[TELEGRAM] Poller hatası: {e}")
                await asyncio.sleep(5)
                
    print("[TELEGRAM] Poller durduruldu.")

async def _handle_update(update: dict, token: str, client: httpx.AsyncClient):
    """Gelen mesaj veya buton tıklamalarını ayrıştırır."""
    
    # 1. Metin Mesajları
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()
        
        if text == "/cancel" or text.lower() == "iptal":
            user_states.pop(chat_id, None)
            await _send_text(client, token, chat_id, "❌ İşlem iptal edildi.")
            return

        if text == "/ara":
            await _start_monitor_creation(client, token, chat_id)
            return
            
        # State makinesinde miyiz?
        if chat_id in user_states:
            await _handle_text_input(client, token, chat_id, text)
            return

    # 2. Buton Tıklamaları (Callback Query)
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq["id"]
        chat_id = cq["message"]["chat"]["id"]
        data = cq.get("data", "")
        
        # answer callback query to remove loading state
        await client.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json={"callback_query_id": cq_id})
        
        # Payload formatı: "book|patient_id|date|hour|subtime"
        # Örnek: "book|1|26.02.2026|16:00|16:10"
        if data.startswith("book|"):
            parts = data.split("|")
            if len(parts) >= 5:
                # 1) Telegram arayüzünde "Yükleniyor..." dönmesini engellemek için answerCallbackQuery
                await client.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json={
                    "callback_query_id": cq_id,
                    "text": "🤖 Randevu alma işlemi başlatılıyor... Lütfen bekleyin."
                })
                
                # 2) "Randevu alınıyor" masajı at
                patient_id = parts[1]
                date_str = parts[2]
                hour_str = parts[3]
                subtime_str = parts[4] if parts[4] != "None" else ""
                
                await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": (
                        f"⏳ <b>Randevu alma işlemi başlatıldı</b>\n\n"
                        f"📅 Tarih: {date_str}\n"
                        f"⏰ Saat: {subtime_str or hour_str}\n\n"
                        f"🔄 Tarayıcı açılıyor, arama yapılıyor ve slot seçiliyor...\n"
                        f"Bu işlem 1-2 dakika sürebilir. Sonuç geldiğinde bildirilecek."
                    ),
                    "parse_mode": "HTML"
                })
                
                # 3) Arka plan thread'inde rezervasyonu tetikle
                _trigger_booking(chat_id, patient_id, date_str, hour_str, subtime_str, token)
        
        # State makinesi - FSM Butonları
        elif data.startswith("pat|") and chat_id in user_states and user_states[chat_id]["step"] == "WAIT_PATIENT":
            parts = data.split("|")
            user_states[chat_id]["patient_id"] = int(parts[1])
            user_states[chat_id]["step"] = "WAIT_DEPT"
            await _send_text(client, token, chat_id, "Taramak istediğiniz bölümü veya doktor adını yazın:\n(Örn: Anestezi veya Ahmet)")
            
        elif data.startswith("date|") and chat_id in user_states and user_states[chat_id]["step"] == "WAIT_DATE":
            parts = data.split("|")
            user_states[chat_id]["date_range"] = parts[1]
            user_states[chat_id]["step"] = "WAIT_TIME"
            await _send_buttons(client, token, chat_id, "Harika. Saat aralığı seçin veya kendiniz yazın:\n(Örn: 13:00- veya 14:00-16:00)", TIME_PRESETS)
            
        elif data.startswith("time|") and chat_id in user_states and user_states[chat_id]["step"] == "WAIT_TIME":
            parts = data.split("|")
            user_states[chat_id]["time_range"] = parts[1]
            user_states[chat_id]["step"] = "WAIT_ACTION"
            await _send_buttons(client, token, chat_id, "Gölge Modu randevu bulduğunda ne yapsın?", ACTION_PRESETS)
            
        elif data.startswith("action|") and chat_id in user_states and user_states[chat_id]["step"] == "WAIT_ACTION":
            parts = data.split("|")
            user_states[chat_id]["action_type"] = parts[1]
            await _finalize_monitor_creation(client, token, chat_id)

async def _send_text(client: httpx.AsyncClient, token: str, chat_id: int, text: str):
    await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML"
    })

async def _send_buttons(client: httpx.AsyncClient, token: str, chat_id: int, text: str, buttons: list):
    await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": buttons}
    })

async def _start_monitor_creation(client: httpx.AsyncClient, token: str, chat_id: int):
    from backend.database import get_all_patients
    patients = get_all_patients()
    if not patients:
        await _send_text(client, token, chat_id, "Sistemde hiç hasta bulunmuyor. Lütfen önce Web arayüzünden hasta ekleyin.")
        return
        
    buttons = [[{"text": f"{p['name']}", "callback_data": f"pat|{p['id']}"}] for p in patients]
    
    user_states[chat_id] = {"step": "WAIT_PATIENT"}
    await _send_buttons(client, token, chat_id, "📍 <b>Yeni Randevu Araması (Gölge Modu)</b>\nLütfen randevu aranacak hastayı seçin:", buttons)

async def _handle_text_input(client: httpx.AsyncClient, token: str, chat_id: int, text: str):
    state = user_states[chat_id]
    step = state["step"]
    
    if step == "WAIT_DEPT":
        state["search_text"] = text
        state["step"] = "WAIT_DATE"
        await _send_buttons(client, token, chat_id, f"Bölüm/Doktor <b>{text}</b> olarak ayarlandı.\n\nTarih aralığı seçin veya yazın:\n(Örn: 24.02.2026-28.02.2026 veya Yok)", DATE_PRESETS)
    
    elif step == "WAIT_DATE":
        state["date_range"] = text
        state["step"] = "WAIT_TIME"
        await _send_buttons(client, token, chat_id, "Saat aralığı seçin veya yazın:\n(Örn: 13:00- veya 14:00-16:00 veya Yok)", TIME_PRESETS)
        
    elif step == "WAIT_TIME":
        state["time_range"] = text
        state["step"] = "WAIT_ACTION"
        await _send_buttons(client, token, chat_id, "Gölge Modu randevu bulduğunda ne yapsın?", ACTION_PRESETS)
        
    else:
        await _send_text(client, token, chat_id, "Şu an butonlu bir seçim bekleniyor. Lütfen yukarıdaki butonlardan birine basın veya iptal etmek için /cancel yazın.")

async def _finalize_monitor_creation(client: httpx.AsyncClient, token: str, chat_id: int):
    state = user_states.pop(chat_id, None)
    if not state:
        return
        
    from backend.database import create_monitor, get_patient
    from backend.scheduler import start_scheduler
    
    pat = get_patient(state["patient_id"])
    pat_name = pat["name"] if pat else "Bilinmiyor"
    
    d_range = state.get("date_range", "Yok")
    t_range = state.get("time_range", "Yok")
    
    # DB'ye kaydet
    create_monitor(
        patient_id=state["patient_id"],
        search_text=state["search_text"],
        randevu_type="internet randevu",
        interval_minutes=5,
        action_type=state["action_type"],
        date_range=d_range,
        time_range=t_range
    )
    
    # Eger durdurulmussa tetikle
    start_scheduler()
    
    await _send_text(client, token, chat_id, 
        f"✅ <b>Gölge Modu Başarıyla Kuruldu!</b>\n\n"
        f"👤 Hasta: {pat_name}\n"
        f"🏥 Bölüm: {state['search_text']}\n"
        f"📅 Tarih: {d_range}\n"
        f"⏰ Saat: {t_range}\n"
        f"⚙️ Aksiyon: {state['action_type']}\n\n"
        f"<i>Sistem 5 dakikada bir arkaplanda arama yapacaktır.</i>"
    )

def _trigger_booking(chat_id, p_id_str, date_str, time_str, subtime_str, token, search_text=""):
    """Booking işlemini ana thread'i engellememek için ayrı bir Thread'de başlatır."""
    from backend.database import get_patient, get_active_monitors, update_monitor
    from backend.bot_runner import run_bot_with_session

    patient_id = int(p_id_str)
    patient = get_patient(patient_id)
    if not patient:
        return

    # search_text verilmemişse aktif monitor'dan al
    if not search_text:
        monitors = get_active_monitors()
        for m in monitors:
            if m["patient_id"] == patient_id:
                search_text = m["search_text"]
                break

    # Booking başlamadan önce bu hastanın tüm monitor'larını kapat
    # (scheduler tekrar tarama yapmasın)
    try:
        for m in get_active_monitors():
            if m["patient_id"] == patient_id:
                update_monitor(m["id"], is_active=False)
                print(f"[BOOKING] Monitor #{m['id']} kapatıldı (booking başlatılıyor)")
    except Exception as e:
        print(f"[BOOKING] Monitor kapatma hatası (pre-booking): {e}")

    bot_config = {
        "tc": patient["tc_kimlik"],
        "birth_date": patient["dogum_tarihi"],
        "phone": patient.get("phone", ""),
        "doctor": search_text,
        "randevu_type": "internetten randevu",
    }

    book_target = {
        "date": date_str,
        "hour": time_str,
        "subtime": subtime_str
    }

    def _run():
        import httpx
        from backend.database import get_active_monitors, update_monitor
        success = False
        msg = ""
        try:
            print(f"[BOOKING] Randevu alma başlatılıyor: {date_str} {subtime_str or time_str}")
            res = run_bot_with_session(bot_config, book_target=book_target)
            print(f"[BOOKING] Bot sonucu: {res.get('status')} | booking={res.get('booking')}")
            booking = res.get("booking", {})
            if booking.get("success"):
                success = True
                msg = (
                    f"✅ 🎉 <b>Randevunuz başarıyla alındı!</b>\n\n"
                    f"📅 Tarih: {date_str}\n"
                    f"⏰ Saat: {subtime_str or time_str}\n"
                    f"📋 {booking.get('message', '')}\n\n"
                    f"🔕 Gölge modu otomatik olarak kapatıldı."
                )
            else:
                error = booking.get("message", "") or res.get("error", "Bilinmeyen hata")
                msg = f"❌ Randevu alınamadı.\n📅 {date_str} {subtime_str or time_str}\nDetay: {error}"
        except Exception as e:
            import traceback
            traceback.print_exc()
            msg = f"❌ Beklenmeyen sistem hatası: {e}"

        if success:
            print(f"[BOOKING] Randevu başarılı! Monitor'lar zaten kapatılmıştı.")
        else:
            # Booking başarısız — monitor'ları tekrar aktifle
            print(f"[BOOKING] Randevu başarısız, monitor'lar tekrar aktifleştiriliyor...")
            try:
                from backend.database import get_all_monitors
                for m in get_all_monitors():
                    if m["patient_id"] == patient_id and not m["is_active"]:
                        update_monitor(m["id"], is_active=True)
                        print(f"[BOOKING] Monitor #{m['id']} tekrar aktifleştirildi")
            except Exception as e:
                print(f"[BOOKING] Monitor tekrar aktifleştirme hatası: {e}")

        # Sonucu Telegram'a geri yolla
        if msg:
            try:
                resp = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": msg,
                    "parse_mode": "HTML"
                }, timeout=10.0)
                print(f"[BOOKING] Telegram bildirim gönderildi: status={resp.status_code}")
            except Exception as e:
                print(f"[BOOKING] Telegram bildirim hatası: {e}")

    # Per-patient executor kullan — aynı session thread'inde sıralı çalışsın
    from backend.session_manager import SessionManager
    sm = SessionManager()
    tc = patient["tc_kimlik"]
    executor = sm.get_executor(tc)
    executor.submit(_run)

def start_telegram_poller():
    _stop_polling.clear()
    asyncio.create_task(poll_telegram())

def stop_telegram_poller():
    _stop_polling.set()
