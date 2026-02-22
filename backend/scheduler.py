import asyncio
import os
import threading
from datetime import datetime, timedelta

from backend.database import get_active_monitors, update_monitor, get_patient
from backend.session_manager import SessionManager
from backend.bot_runner import run_bot_with_session
from backend.notifications import send_telegram_message_sync

# A global event to signal the background task to stop cleanly on shutdown.
_stop_event = asyncio.Event()

# Registry to track active running tasks by monitor ID so we can cancel them on deletion
_active_runs: dict[int, threading.Event] = {}

def cancel_monitor(monitor_id: int):
    """Signals a running monitor instance to abort immediately."""
    if monitor_id in _active_runs:
        print(f"[SHADOW] İptal sinyali gönderiliyor (Monitor ID: {monitor_id})...")
        _active_runs[monitor_id].set()


def _dummy_status_callback(step: str, message: str):
    """Callback for the bot during background execution. Discards messages except critical logs."""
    pass


async def _run_monitor(monitor: dict, loop: asyncio.AbstractEventLoop):
    """Executes a single monitor task by invoking the bot."""
    # Çalışmadan önce monitor hala aktif mi kontrol et (booking sırasında kapatılmış olabilir)
    fresh = get_active_monitors()
    still_active = any(m["id"] == monitor["id"] for m in fresh)
    if not still_active:
        print(f"[SHADOW] Monitor #{monitor['id']} artık aktif değil, atlanıyor.")
        return

    patient = get_patient(monitor["patient_id"])
    if not patient:
        print(f"[SHADOW] Hasta bulunamadı (ID: {monitor['patient_id']}), monitor durduruluyor.")
        update_monitor(monitor["id"], is_active=False)
        return

    print(f"[SHADOW] İzleme başlatılıyor: {patient['name']} -> {monitor['search_text']}")

    # Hemen last_checked güncelle — scheduler'ın tekrar tetiklemesini engelle
    update_monitor(monitor["id"], last_checked=datetime.now().isoformat())

    bot_config = {
        "tc": patient["tc_kimlik"],
        "birth_date": patient["dogum_tarihi"],
        "phone": patient.get("phone", ""),
        "doctor": monitor["search_text"],
        "clinic": "",
        "department": "",
        "randevu_type": monitor["randevu_type"],
        "patient_id": patient["id"],
        "action_type": monitor["action_type"],
        "date_range": monitor.get("date_range", ""),
        "time_range": monitor.get("time_range", ""),
    }

    cancel_event = threading.Event()
    _active_runs[monitor["id"]] = cancel_event

    sm = SessionManager()
    tc = patient["tc_kimlik"]
    executor = sm.get_executor(tc)

    # Let the background scan run via executor
    try:
        result = await loop.run_in_executor(
            executor,
            lambda: run_bot_with_session(
                bot_config, _dummy_status_callback, cancel_event=cancel_event,
                probe_subtimes=True, book_target=None
            )
        )
        print(f"[SHADOW] İzleme sonucu: {result.get('status')} - Toplam uygun: {result.get('total_available')}")

        # If we successfully scanned, update the last_checked timestamp
        update_monitor(monitor["id"], last_checked=datetime.now().isoformat())

        # Müsait randevu varsa action_type'a göre işlem yap
        if result.get("status") == "AVAILABLE" and result.get("total_available", 0) > 0:
            action_type = monitor.get("action_type", "notify")
            await _handle_monitor_result(monitor, patient, result, action_type)

    except Exception as e:
        print(f"[SHADOW] Hata oluştu (Monitor ID: {monitor['id']}): {e}")
    finally:
        _active_runs.pop(monitor["id"], None)


async def _handle_monitor_result(monitor: dict, patient: dict, result: dict, action_type: str):
    """Tarama sonucuna göre bildirim / booking / Telegram saat seçimi yapar."""
    from backend.notifications import send_telegram_message_sync, send_notification_with_buttons_sync

    pat_name = patient["name"]
    search_text = monitor["search_text"]
    probed = result.get("probed_subtimes", [])
    alternatives = result.get("alternatives", [])

    # Tarih/saat filtreleri
    date_range = monitor.get("date_range", "") or ""
    time_range = monitor.get("time_range", "") or ""

    # Filtrelenmiş alt-saatleri hazırla
    filtered = _filter_probed(probed, date_range, time_range)

    if action_type == "notify":
        # Sadece metin bildirimi gönder
        if filtered:
            lines = [f"🔔 <b>Müsait Randevu Bulundu!</b>\n👤 {pat_name} | 🏥 {search_text}\n"]
            for item in filtered:
                times_str = ", ".join(item["subtimes"])
                lines.append(f"📅 {item['date']} {item['hour']}: {times_str}")
            send_telegram_message_sync("\n".join(lines))
        else:
            # Probed yoksa alternatiflerden özet
            lines = [f"🔔 <b>Müsait Randevu Bulundu!</b>\n👤 {pat_name} | 🏥 {search_text}\n"]
            for alt in alternatives:
                slots = alt.get("appointments", {}).get("available_slots", [])
                if slots:
                    by_date = {}
                    for s in slots:
                        by_date.setdefault(s["date"], []).append(s["time"])
                    for d, times in by_date.items():
                        lines.append(f"📅 {d}: {', '.join(times)}")
            send_telegram_message_sync("\n".join(lines))

    elif action_type == "ask_telegram":
        # Butonlarla Telegram'a gönder — kullanıcı seçsin
        if not filtered:
            send_telegram_message_sync(
                f"🔍 {pat_name} | {search_text}\nArama yapıldı ancak filtrelerinize uygun alt-saat bulunamadı."
            )
            return

        text = f"🩺 <b>Müsait Randevular Bulundu!</b>\n👤 {pat_name} | 🏥 {search_text}\n\nBir saate tıklayarak randevu alabilirsiniz:"
        buttons = []
        for item in filtered:
            for st in item["subtimes"]:
                cb_data = f"book|{patient['id']}|{item['date']}|{item['hour']}|{st}"
                # Telegram callback_data max 64 byte
                if len(cb_data.encode()) <= 64:
                    buttons.append([{
                        "text": f"📅 {item['date']} ⏰ {st}",
                        "callback_data": cb_data
                    }])
        if buttons:
            send_notification_with_buttons_sync(text, buttons)
        else:
            send_telegram_message_sync(
                f"🔍 {pat_name} | {search_text}\nMüsait randevu bulundu ancak buton oluşturulamadı."
            )

    elif action_type == "auto_book":
        # En uzak tarihin en son saatini otomatik al
        if not filtered:
            send_telegram_message_sync(
                f"⚡ {pat_name} | {search_text}\nOtomatik alma: filtrelerinize uygun slot bulunamadı."
            )
            return

        # En uzaktaki (son) slot
        last = filtered[-1]
        target_subtime = last["subtimes"][-1]
        book_target = {"date": last["date"], "hour": last["hour"], "subtime": target_subtime}

        send_telegram_message_sync(
            f"⚡ <b>Otomatik Randevu Alınıyor</b>\n👤 {pat_name}\n📅 {last['date']} ⏰ {target_subtime}"
        )

        from backend.telegram_bot import _trigger_booking
        _trigger_booking(
            int(os.getenv("TELEGRAM_CHAT_ID", "0")),
            str(patient["id"]),
            last["date"],
            last["hour"],
            target_subtime,
            os.getenv("TELEGRAM_BOT_TOKEN", ""),
            search_text=search_text,
        )


def _filter_probed(probed: list, date_range: str, time_range: str) -> list:
    """Probed subtimes'ı tarih ve saat filtrelerine göre süzer."""
    if not probed:
        return []

    filtered = []
    for item in probed:
        # Tarih filtresi
        if date_range and date_range != "Yok":
            if not _date_matches(item["date"], date_range):
                continue

        # Saat filtresi — subtimes listesini filtrele
        if time_range and time_range != "Yok":
            matching_times = [st for st in item["subtimes"] if _time_matches(st, time_range)]
        else:
            matching_times = item["subtimes"]

        if matching_times:
            filtered.append({
                "date": item["date"],
                "hour": item["hour"],
                "subtimes": matching_times,
            })

    return filtered


def _date_matches(date_str: str, date_range: str) -> bool:
    """Tarih filtresine uyuyor mu? Formatlar: 'bugun', 'GG.AA.YYYY-GG.AA.YYYY', 'GG.AA.YYYY'"""
    if date_range == "bugun":
        today = datetime.now().strftime("%d.%m.%Y")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")
        return date_str in (today, tomorrow)

    try:
        d = datetime.strptime(date_str, "%d.%m.%Y")
    except ValueError:
        return True  # parse edilemezse geçir

    if "-" in date_range:
        parts = date_range.split("-")
        if len(parts) == 2:
            try:
                start = datetime.strptime(parts[0].strip(), "%d.%m.%Y")
                end = datetime.strptime(parts[1].strip(), "%d.%m.%Y")
                return start <= d <= end
            except ValueError:
                return True
    else:
        # Tek tarih
        try:
            target = datetime.strptime(date_range.strip(), "%d.%m.%Y")
            return d == target
        except ValueError:
            return True

    return True


def _time_matches(time_str: str, time_range: str) -> bool:
    """Saat filtresine uyuyor mu? Formatlar: 'HH:MM-HH:MM', 'HH:MM-'"""
    if not time_range or time_range == "Yok":
        return True

    try:
        t = datetime.strptime(time_str.strip(), "%H:%M").time()
    except ValueError:
        return True

    if "-" in time_range:
        parts = time_range.split("-")
        try:
            start = datetime.strptime(parts[0].strip(), "%H:%M").time() if parts[0].strip() else None
            end = datetime.strptime(parts[1].strip(), "%H:%M").time() if parts[1].strip() else None
        except ValueError:
            return True

        if start and end:
            return start <= t <= end
        elif start:
            return t >= start
        elif end:
            return t <= end

    return True


async def monitor_loop():
    """Background task that wakes up every minute to check if any monitor needs to be run."""
    print("[SHADOW] Arka plan zamanlayıcısı (Scheduler) başlatıldı.")
    loop = asyncio.get_running_loop()

    while not _stop_event.is_set():
        try:
            active_monitors = get_active_monitors()
            now = datetime.now()

            for mon in active_monitors:
                last_fmt = mon["last_checked"]
                
                # Default to execution if never checked
                should_run = False
                if not last_fmt:
                    should_run = True
                else:
                    try:
                        last_exec = datetime.fromisoformat(last_fmt)
                        elapsed = (now - last_exec).total_seconds() / 60.0
                        if elapsed >= mon["interval_minutes"]:
                            should_run = True
                    except ValueError:
                        should_run = True

                if should_run:
                    # Run this monitor in a background task so we don't block the scheduler loop
                    asyncio.create_task(_run_monitor(mon, loop))
                    
        except Exception as e:
            print(f"[SHADOW] Scheduler döngü hatası: {e}")

        # Sleep for 60 seconds, waking up early if _stop_event is set
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            pass

    print("[SHADOW] Arka plan zamanlayıcısı durduruldu.")


def start_scheduler():
    """Starts the scheduler as a background asyncio task."""
    _stop_event.clear()
    asyncio.create_task(monitor_loop())


def stop_scheduler():
    """Signals the scheduler to stop."""
    _stop_event.set()
