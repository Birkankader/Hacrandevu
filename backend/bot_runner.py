"""Bot wrapper — HacettepeBot'u config override + status callback ile çalıştırır."""

import io
import gc
import sys
import threading
import time
import os
from queue import Queue, Empty

from backend.session_manager import SessionManager


def _prepare_config(config: dict) -> dict:
    """Ortak config hazırlığı."""
    captcha_key = os.getenv("CAPTCHA_API_KEY", "")
    if captcha_key:
        config.setdefault("captcha_api_key", captcha_key)
    config.setdefault("headless", True)
    config.setdefault("save_screenshot", True)
    return config


class _TeeWriter:
    """Thread-safe stdout wrapper.

    sys.stdout'u değiştirmeden, tüm print çıktılarını orijinal stdout'a yönlendirir.
    Callback mekanizması _emit() içinde kalır — burada tekrar çağrılmaz.
    """
    _real_stdout = None
    _lock = threading.Lock()
    _ref_count = 0

    @classmethod
    def install(cls):
        """sys.stdout'u tee ile değiştir (referans sayaçlı)."""
        with cls._lock:
            cls._ref_count += 1
            if cls._real_stdout is None:
                cls._real_stdout = sys.stdout
                sys.stdout = cls()

    @classmethod
    def uninstall(cls):
        """Referans düş. Son kullanıcı çıkınca stdout'u geri al."""
        with cls._lock:
            cls._ref_count = max(0, cls._ref_count - 1)
            if cls._ref_count == 0 and cls._real_stdout is not None:
                sys.stdout = cls._real_stdout
                cls._real_stdout = None

    def write(self, s):
        real = self._real_stdout or sys.__stdout__
        real.write(s)

    def flush(self):
        real = self._real_stdout or sys.__stdout__
        real.flush()


def run_bot_with_session(config: dict, status_callback=None, cancel_event=None,
                         probe_subtimes=False, book_target=None) -> dict:
    """Session-aware bot araması. Mevcut session varsa login atlar.

    Args:
        config: Bot yapılandırması (tc, birth_date, doctor, vb.)
        status_callback: fn(step, message) — her adımda çağrılır
        cancel_event: threading.Event — set edilirse bot iptal olur
        probe_subtimes: True ise açık slotların alt-saatlerini keşfeder
        book_target: {"date", "hour", "subtime"} — belirli randevuyu al

    Returns:
        {"status": str, "alternatives": list, "exit_code": int, "session_reused": bool}
    """
    from check_randevu import HacettepeBot, RecaptchaFailed, BotCancelled

    config = _prepare_config(config)
    patient_tc = config.get("tc", "")
    sm = SessionManager()

    # Thread-safe stdout koruma — orijinal stdout kaybolmasını önler
    _TeeWriter.install()
    try:
        bot = HacettepeBot(
            config_override=config,
            status_callback=status_callback,
            cancel_event=cancel_event,
        )

        bs = sm.get_session(patient_tc)
        session_reused = bs is not None and bs.logged_in

        search_args = dict(
            search_text=config.get("doctor") or config.get("clinic") or "",
            randevu_type=config.get("randevu_type", "internet randevu"),
            probe_subtimes=probe_subtimes,
            book=bool(book_target),
            book_target=book_target,
            action_type=config.get("action_type", "notify"),
        )

        # --- GC: Python tarafı temizlik ---
        gc.collect()

        if session_reused:
            # Mevcut session — arama sayfasına dönüp yeniden ara
            bs.touch()
            try:
                # Login sonrası kayıtlı URL'e git yerine sayfayı temizle
                try:
                    if bs.page.url == bs.search_url or ("public/main" in bs.page.url.lower()):
                        bs.page.keyboard.press("Escape")
                        time.sleep(0.3)
                        bs.page.keyboard.press("Escape")
                        time.sleep(0.3)
                        bs.page.evaluate("document.body.click()")
                        time.sleep(0.5)
                    elif bs.search_url:
                        bs.page.goto(bs.search_url, wait_until="networkidle", timeout=30000)
                        time.sleep(2)
                except Exception as e:
                    print(f"[SESSION] UI temizleme hatası (devam ediliyor): {e}")

                exit_code = bot.run_with_page(
                    bs.page, skip_login=True, **search_args,
                )
                # --- GC: Chromium JS heap temizliği ---
                try:
                    bs.page.evaluate("() => { if (window.gc) window.gc(); }")
                except Exception:
                    pass
                bs.touch()
            except BotCancelled:
                raise
            except Exception as e:
                # Session expire olmuş — yeniden login dene
                import traceback
                print(f"[SESSION] Oturum hatası, yeniden login: {e}")
                traceback.print_exc()
                if status_callback:
                    status_callback("init", "[BILGI] Oturum geçersiz, yeniden giriş yapılıyor...")
                sm.close_session(patient_tc)
                bs = sm.create_session(patient_tc, config)
                session_reused = False
                exit_code = bot.run_with_page(
                    bs.page, skip_login=False, **search_args,
                )
                bs.logged_in = True
                bs.search_url = getattr(bot, 'post_login_url', '') or bs.page.url
                # --- GC: Chromium JS heap temizliği (session expire recovery) ---
                try:
                    bs.page.evaluate("() => { if (window.gc) window.gc(); }")
                except Exception:
                    pass
                bs.touch()
        else:
            # Yeni session oluştur
            if bs and not bs.logged_in:
                sm.close_session(patient_tc)

            bs = sm.create_session(patient_tc, config)
            try:
                exit_code = bot.run_with_page(
                    bs.page, skip_login=False, **search_args,
                )
                bs.logged_in = True
                bs.search_url = getattr(bot, 'post_login_url', '') or bs.page.url
                # --- GC: Chromium JS heap temizliği (yeni session) ---
                try:
                    bs.page.evaluate("() => { if (window.gc) window.gc(); }")
                except Exception:
                    pass
                bs.touch()
            except (RecaptchaFailed, Exception) as e:
                sm.close_session(patient_tc)
                raise

        result = bot.result or {}
        result["exit_code"] = exit_code
        result["session_reused"] = session_reused
        return result

    except BotCancelled:
        return {
            "status": "CANCELLED",
            "error": "Arama iptal edildi.",
            "exit_code": 1,
            "session_reused": False,
            "slots": {"green": 0, "red": 0, "grey": 0, "total": 0, "details": []},
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Hata durumunda session'ı kapat
        try:
            sm.close_session(patient_tc)
        except Exception:
            pass
        return {
            "status": "ERROR",
            "error": str(e),
            "exit_code": 1,
            "session_reused": False,
            "slots": {"green": 0, "red": 0, "grey": 0, "total": 0, "details": []},
        }
    finally:
        _TeeWriter.uninstall()
        # --- GC: Her arama sonrası Python bellek temizliği ---
        gc.collect()


def run_bot_search(config: dict, status_callback=None) -> dict:
    """Bot aramasını çalıştır ve sonucu döndür (geriye uyumluluk — session kullanmaz).

    Args:
        config: Bot yapılandırması (tc, birth_date, doctor, vb.)
        status_callback: fn(step, message) — her adımda çağrılır

    Returns:
        {"status": str, "slots": dict, "exit_code": int}
    """
    from check_randevu import HacettepeBot

    config = _prepare_config(config)

    real_stdout = sys.stdout
    tee = _TeeWriter(real_stdout, status_callback)

    _TeeWriter.install()
    try:
        bot = HacettepeBot(
            config_override=config,
            status_callback=status_callback,
        )
        exit_code = bot.run_once()

        result = bot.result or {}
        result["exit_code"] = exit_code
        return result
    except Exception as e:
        return {
            "status": "ERROR",
            "error": str(e),
            "exit_code": 1,
            "slots": {"green": 0, "red": 0, "grey": 0, "total": 0, "details": []},
        }
    finally:
        _TeeWriter.uninstall()
        gc.collect()
