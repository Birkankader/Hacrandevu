"""Bot wrapper — HacettepeBot'u config override + status callback ile çalıştırır."""

import io
import sys
import threading
import os
from queue import Queue, Empty


def run_bot_search(config: dict, status_callback=None) -> dict:
    """Bot aramasını çalıştır ve sonucu döndür.

    Args:
        config: Bot yapılandırması (tc, birth_date, doctor, vb.)
        status_callback: fn(step, message) — her adımda çağrılır

    Returns:
        {"status": str, "slots": dict, "exit_code": int}
    """
    from check_randevu import HacettepeBot, _bot_lock

    # .env'den captcha key'i al (frontend'e hiç gitmez)
    captcha_key = os.getenv("CAPTCHA_API_KEY", "")
    if captcha_key:
        config.setdefault("captcha_api_key", captcha_key)

    # Headless varsayılan olarak True (web modunda)
    config.setdefault("headless", True)
    config.setdefault("save_screenshot", True)

    # stdout capture — free function print'lerini de yakalar
    real_stdout = sys.stdout
    capture_buf = io.StringIO()

    class TeeWriter:
        """stdout'u hem gerçek stdout'a hem capture buffer'a yaz."""
        def write(self, s):
            real_stdout.write(s)
            capture_buf.write(s)
            # Print satırlarından status callback oluştur
            if status_callback and s.strip():
                try:
                    status_callback("stdout", s.strip())
                except Exception:
                    pass

        def flush(self):
            real_stdout.flush()

    with _bot_lock:
        sys.stdout = TeeWriter()
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
