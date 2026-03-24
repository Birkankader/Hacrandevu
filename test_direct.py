#!/usr/bin/env python3
"""Doğrudan test — Anestezi randevu arama."""
import sys
import os

# .env yükle
from dotenv import load_dotenv
load_dotenv()

from check_randevu import HacettepeBot

def status_cb(step, msg):
    print(f"  [{step}] {msg}")

cfg = {
    "tc": "15034089976",
    "birth_date": "30.05.1994",
    "phone": "5555555555",
    "department": "Anestezi",
    "clinic": "",
    "doctor": "Anestezi",
    "headless": False,
    "randevu_type": "internet randevu",
    "check_interval_minutes": 0,
    "timeout_ms": 45000,
    "save_screenshot": True,
    "recaptcha_timeout_ms": 120000,
    "recaptcha_max_retries": 3,
    "page_retries": 3,
    "email": "",
    "captcha_api_key": os.getenv("CAPTCHA_API_KEY", ""),
    "target_url": "https://hastanerandevu.hacettepe.edu.tr/nucleus-hastaportal-randevu/public/main?user=PUBLIC",
}

bot = HacettepeBot(config_override=cfg, status_callback=status_cb)
code = bot.run_once()
print(f"\n=== Sonuç kodu: {code} ===")
if bot.result:
    import json
    print(json.dumps(bot.result, indent=2, ensure_ascii=False)[:5000])
