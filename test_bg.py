import threading
from backend.notifications import send_telegram_message_sync

def test_bg():
    print("Testing bg...")
    try:
        res = send_telegram_message_sync("🚨 Test from background thread")
        print("Result:", res)
    except Exception as e:
        print("Error:", e)

t = threading.Thread(target=test_bg)
t.start()
t.join()
