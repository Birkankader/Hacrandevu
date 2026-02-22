import os
import httpx
import asyncio
from dotenv import dotenv_values

def get_telegram_creds():
    env = dotenv_values(".env")
    token = env.get("TELEGRAM_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")
    return token, chat_id


async def send_telegram_message(text: str) -> bool:
    """
    Sends an asynchronous message to the configured Telegram chat.
    Returns True if successful or disabled, False if failed.
    """
    token, chat_id = get_telegram_creds()
    if not token or not chat_id:
        # User has not configured Telegram, fail silently
        return True

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                print(f"[NOTIFY] Telegram mesajı gönderildi: {text[:50]}...")
                return True
            else:
                print(f"[NOTIFY] Telegram API Hatası: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        print(f"[NOTIFY] Telegram Gönderim Hatası: {e}")
        return False


def send_telegram_message_sync(text: str) -> bool:
    """
    Synchronous wrapper to send Telegram messages from non-async contexts.
    Uses asyncio.run or the existing event loop if one is running.
    """
    try:
        token, chat_id = get_telegram_creds()
        if not token or not chat_id:
            return True

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_telegram_message(text))
            return True
        except RuntimeError:
            return asyncio.run(send_telegram_message(text))
    except Exception as e:
        print(f"[NOTIFY] Sync gönderme hatası: {e}")
        return False

async def send_telegram_message_with_buttons(text: str, buttons: list[list[dict]]) -> bool:
    token, chat_id = get_telegram_creds()
    if not token or not chat_id:
        return True

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": buttons
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                print(f"[NOTIFY] Telegram butonlu mesajı gönderildi.")
                return True
            else:
                print(f"[NOTIFY] Telegram API Hatası: {response.status_code} - {response.text}")
                return False
    except Exception as e:
        print(f"[NOTIFY] Telegram Butonlu Gönderim Hatası: {e}")
        return False

def send_notification_with_buttons_sync(text: str, buttons: list[list[dict]]) -> bool:
    try:
        token, chat_id = get_telegram_creds()
        if not token or not chat_id:
            return True

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_telegram_message_with_buttons(text, buttons))
            return True
        except RuntimeError:
            return asyncio.run(send_telegram_message_with_buttons(text, buttons))
    except Exception as e:
        print(f"[NOTIFY] Sync butonlu gönderme hatası: {e}")
        return False
