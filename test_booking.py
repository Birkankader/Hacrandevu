"""Booking akışını doğrudan test eder — scheduler'ın yaptığı şeyi simüle eder."""
import os
import sys
import time

# .env'yi yükle
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Manuel yükle
    with open(".env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k.strip(), v)

from backend.bot_runner import run_bot_with_session

# Hasta bilgileri
bot_config = {
    "tc": "15034089976",
    "birth_date": "30.05.1994",
    "phone": "5442302995",
    "doctor": "Anestezi",
    "randevu_type": "internetten randevu",
}

# 17.03.2026 — dialog'daki gerçek alt-saat 08:55 (08:00 slotunun subtimei)
book_target = {
    "date": "17.03.2026",
    "hour": "08:00",
    "subtime": "08:55",
}

def status_cb(step, msg):
    print(f"  [{step}] {msg}")

print("=" * 60)
print(f"BOOKING TEST — Hedef: {book_target}")
print("=" * 60)

t0 = time.time()
try:
    result = run_bot_with_session(
        bot_config,
        status_callback=status_cb,
        book_target=book_target,
    )
    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print(f"SONUÇ ({elapsed:.1f}s):")
    print(f"  status:   {result.get('status')}")
    print(f"  booking:  {result.get('booking')}")
    print(f"  exit_code: {result.get('exit_code')}")
    print(f"  session_reused: {result.get('session_reused')}")
    if result.get('error'):
        print(f"  error:    {result.get('error')}")
    print("=" * 60)
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"\nHATA: {e}")
