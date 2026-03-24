#!/usr/bin/env python3
"""WebSocket ile randevu arama testi — sadece listeleme, randevu almaz."""
import asyncio
import json
import websockets

async def test_search():
    uri = "ws://127.0.0.1:8000/ws/search"
    
    async with websockets.connect(uri, ping_interval=20, ping_timeout=60) as ws:
        # Arama komutu gönder
        search_msg = {
            "action": "search",
            "patient_id": 1,
            "search_text": "Anestezi",
            "randevu_type": "internet randevu",
        }
        
        print(f"[GÖNDER] {json.dumps(search_msg, ensure_ascii=False)}")
        await ws.send(json.dumps(search_msg))
        
        # Sonuçları dinle
        timeout = 300  # 5 dakika max
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                msg = json.loads(raw)
                msg_type = msg.get("type", "?")
                
                if msg_type == "status":
                    step = msg.get("step", "")
                    message = msg.get("message", "")
                    print(f"  [{step}] {message}")
                    
                elif msg_type == "result":
                    data = msg.get("data", {})
                    print(f"\n{'='*60}")
                    print(f"SONUÇ:")
                    print(f"  Status: {data.get('status')}")
                    print(f"  Exit code: {data.get('exit_code')}")
                    print(f"  Session reused: {data.get('session_reused')}")
                    print(f"  Total available: {data.get('total_available', 0)}")
                    print(f"  Total visible: {data.get('total_visible', 0)}")
                    
                    alts = data.get("alternatives", [])
                    for alt in alts:
                        name = alt.get("name", "?")
                        status = alt.get("status", "?")
                        formatted = alt.get("formatted", "")
                        appt = alt.get("appointments", {})
                        avail = appt.get("available_slots", [])
                        print(f"\n  📋 {name}")
                        print(f"     Status: {status}")
                        print(f"     Müsait slot: {len(avail)}")
                        if avail:
                            print(f"     Saatler: {formatted[:200]}")
                    
                    # Probed subtimes
                    probed = data.get("probed_subtimes", [])
                    if probed:
                        print(f"\n  🔍 Alt-saat detayları:")
                        for p in probed:
                            print(f"     {p.get('date')} {p.get('hour')} → {p.get('subtimes')}")
                    
                    print(f"{'='*60}")
                    break  # Sonuç geldi, çık
                    
                elif msg_type == "error":
                    print(f"  [HATA] {msg.get('message', '?')}")
                    break
                    
                elif msg_type == "session_status":
                    data = msg.get("data", {})
                    print(f"  [SESSION] active={data.get('active')}, logged_in={data.get('logged_in')}")
                    
                else:
                    print(f"  [?] {msg}")
                    
        except asyncio.TimeoutError:
            print("[TIMEOUT] 5 dakika içinde sonuç alınamadı.")

if __name__ == "__main__":
    asyncio.run(test_search())
