"""Bot wrapper — HacettepeBot'u config override + status callback ile çalıştırır."""

import io
import sys
import threading
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
    """stdout'u hem gerçek stdout'a hem callback'e yaz."""
    def __init__(self, real_stdout, status_callback=None):
        self._real = real_stdout
        self._cb = status_callback

    def write(self, s):
        self._real.write(s)
        if self._cb and s.strip():
            try:
                self._cb("stdout", s.strip())
            except Exception:
                pass

    def flush(self):
        self._real.flush()


def run_bot_with_session(config: dict, status_callback=None) -> dict:
    """Session-aware bot araması. Mevcut session varsa login atlar.

    Args:
        config: Bot yapılandırması (tc, birth_date, doctor, vb.)
        status_callback: fn(step, message) — her adımda çağrılır

    Returns:
        {"status": str, "alternatives": list, "exit_code": int, "session_reused": bool}
    """
    from check_randevu import HacettepeBot, _bot_lock, RecaptchaFailed

    config = _prepare_config(config)
    patient_tc = config.get("tc", "")
    sm = SessionManager()

    real_stdout = sys.stdout
    tee = _TeeWriter(real_stdout, status_callback)

    with _bot_lock:
        sys.stdout = tee
        try:
            bot = HacettepeBot(
                config_override=config,
                status_callback=status_callback,
            )

            bs = sm.get_session(patient_tc)
            session_reused = bs is not None and bs.logged_in

            if session_reused:
                # Mevcut session — login atla
                bs.touch()
                exit_code = bot.run_with_page(
                    bs.page,
                    skip_login=True,
                    search_text=config.get("doctor") or config.get("clinic") or "",
                    randevu_type=config.get("randevu_type", "internet randevu"),
                )
                bs.touch()
            else:
                # Yeni session oluştur
                if bs and not bs.logged_in:
                    sm.close_session(patient_tc)

                bs = sm.create_session(patient_tc, config)
                try:
                    exit_code = bot.run_with_page(
                        bs.page,
                        skip_login=False,
                        search_text=config.get("doctor") or config.get("clinic") or "",
                        randevu_type=config.get("randevu_type", "internet randevu"),
                    )
                    bs.logged_in = True
                    bs.touch()
                except (RecaptchaFailed, Exception) as e:
                    sm.close_session(patient_tc)
                    raise

            result = bot.result or {}
            result["exit_code"] = exit_code
            result["session_reused"] = session_reused
            return result

        except Exception as e:
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
            sys.stdout = real_stdout


def run_bot_search(config: dict, status_callback=None) -> dict:
    """Bot aramasını çalıştır ve sonucu döndür (geriye uyumluluk — session kullanmaz).

    Args:
        config: Bot yapılandırması (tc, birth_date, doctor, vb.)
        status_callback: fn(step, message) — her adımda çağrılır

    Returns:
        {"status": str, "slots": dict, "exit_code": int}
    """
    from check_randevu import HacettepeBot, _bot_lock

    config = _prepare_config(config)

    real_stdout = sys.stdout
    tee = _TeeWriter(real_stdout, status_callback)

    with _bot_lock:
        sys.stdout = tee
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
            sys.stdout = real_stdout
