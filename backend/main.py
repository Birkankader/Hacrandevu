"""FastAPI uygulaması — REST + WebSocket + Static serving."""

import asyncio
import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.database import init_db, get_all_patients, get_patient, create_patient, update_patient, delete_patient
from backend.bot_runner import run_bot_with_session
from backend.session_manager import SessionManager

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
ARTIFACTS_DIR = BASE_DIR / "artifacts"

app = FastAPI(title="HacettepeBot", version="1.0.0")


# ─── Startup / Shutdown ───
@app.on_event("startup")
def on_startup():
    init_db()
    ARTIFACTS_DIR.mkdir(exist_ok=True)


@app.on_event("shutdown")
def on_shutdown():
    SessionManager().close_all()


# ─── Pydantic models ───
class PatientCreate(BaseModel):
    name: str
    tc_kimlik: str
    dogum_tarihi: str
    phone: str = ""


class PatientUpdate(BaseModel):
    name: Optional[str] = None
    tc_kimlik: Optional[str] = None
    dogum_tarihi: Optional[str] = None
    phone: Optional[str] = None


# ─── REST: Hasta CRUD ───
@app.get("/api/patients")
def list_patients():
    return get_all_patients()


@app.post("/api/patients", status_code=201)
def add_patient(data: PatientCreate):
    try:
        return create_patient(data.name, data.tc_kimlik, data.dogum_tarihi, data.phone)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(400, "Bu TC Kimlik No ile kayıtlı hasta zaten var.")
        raise HTTPException(400, str(e))


@app.put("/api/patients/{patient_id}")
def edit_patient(patient_id: int, data: PatientUpdate):
    existing = get_patient(patient_id)
    if not existing:
        raise HTTPException(404, "Hasta bulunamadı.")
    result = update_patient(patient_id, **data.model_dump(exclude_none=True))
    return result


@app.delete("/api/patients/{patient_id}")
def remove_patient(patient_id: int):
    if not delete_patient(patient_id):
        raise HTTPException(404, "Hasta bulunamadı.")
    return {"ok": True}


# ─── REST: Session durumu ───
@app.get("/api/session/{patient_id}")
def get_session_status(patient_id: int):
    patient = get_patient(patient_id)
    if not patient:
        raise HTTPException(404, "Hasta bulunamadı.")
    sm = SessionManager()
    return sm.get_status(patient["tc_kimlik"])


# ─── WebSocket: Randevu Arama (multi-message loop) ───
@app.websocket("/ws/search")
async def ws_search(ws: WebSocket):
    await ws.accept()
    cancel_event: threading.Event | None = None

    try:
        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "message": "Geçersiz JSON."})
                continue

            action = msg.get("action")

            if action == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if action == "cancel":
                if cancel_event:
                    cancel_event.set()
                    await ws.send_json({"type": "status", "step": "cancel", "message": "İptal sinyali gönderildi..."})
                else:
                    await ws.send_json({"type": "error", "message": "Aktif arama yok."})
                continue

            if action == "session_status":
                patient_id = msg.get("patient_id")
                patient = get_patient(patient_id) if patient_id else None
                if patient:
                    sm = SessionManager()
                    loop = asyncio.get_running_loop()
                    tc = patient["tc_kimlik"]
                    executor = sm.get_executor(tc)
                    status = await loop.run_in_executor(executor, lambda: sm.get_status(tc))
                    await ws.send_json({"type": "session_status", "data": status})
                else:
                    await ws.send_json({"type": "session_status", "data": {"active": False, "logged_in": False, "idle_seconds": 0}})
                continue

            if action == "close_session":
                patient_id = msg.get("patient_id")
                patient = get_patient(patient_id) if patient_id else None
                if patient:
                    sm = SessionManager()
                    loop = asyncio.get_running_loop()
                    tc = patient["tc_kimlik"]
                    executor = sm.get_executor(tc)
                    await loop.run_in_executor(executor, lambda: sm.close_session(tc))
                    await ws.send_json({"type": "session_closed"})
                else:
                    await ws.send_json({"type": "error", "message": "Hasta bulunamadı."})
                continue

            if action not in ("search", "book"):
                await ws.send_json({"type": "error", "message": f"Bilinmeyen action: {action}"})
                continue

            # ── Search / Book action ──
            patient_id = msg.get("patient_id")
            search_text = msg.get("search_text", "")
            randevu_type = msg.get("randevu_type", "internet randevu")
            book_target = msg.get("book_target")  # {"date","hour","subtime"}

            patient = get_patient(patient_id)
            if not patient:
                await ws.send_json({"type": "error", "message": "Hasta bulunamadı."})
                continue

            # Bot config oluştur
            bot_config = {
                "tc": patient["tc_kimlik"],
                "birth_date": patient["dogum_tarihi"],
                "phone": patient.get("phone", ""),
                "doctor": search_text,
                "clinic": "",
                "department": "",
                "randevu_type": randevu_type,
            }

            if action == "book" and book_target:
                init_msg = f"Randevu alınıyor: {book_target.get('date')} {book_target.get('subtime')}..."
            elif action == "search":
                init_msg = "Arama ve alt-saat keşfi başlatılıyor..."
            else:
                init_msg = "Bot başlatılıyor..."
            await ws.send_json({"type": "status", "step": "init", "message": init_msg})

            loop = asyncio.get_event_loop()
            cancel_event = threading.Event()

            # Status callback — thread'den WebSocket'e push
            def status_callback(step, message):
                try:
                    asyncio.run_coroutine_threadsafe(
                        ws.send_json({"type": "status", "step": step, "message": message}),
                        loop,
                    )
                except Exception:
                    pass

            ce = cancel_event
            tc = patient["tc_kimlik"]
            _bt = book_target if action == "book" else None
            _probe = action == "search"

            async def run_search_async():
                try:
                    sm = SessionManager()
                    executor = sm.get_executor(tc)
                    
                    result = await loop.run_in_executor(
                        executor,
                        lambda: run_bot_with_session(
                            bot_config, status_callback, cancel_event=ce,
                            probe_subtimes=_probe, book_target=_bt,
                        )
                    )
                    await ws.send_json({"type": "result", "data": result})

                    session_status = await loop.run_in_executor(executor, lambda: sm.get_status(tc))
                    await ws.send_json({"type": "session_status", "data": session_status})
                except Exception as ex:
                    try:
                        await ws.send_json({"type": "error", "message": f"Arka plan işlemi hatası: {str(ex)}"})
                    except Exception:
                        pass
            
            # Aramayı arka planda başlat (while döngüsünün beklemesini engeller)
            asyncio.create_task(run_search_async())
            
    except WebSocketDisconnect:
        if cancel_event:
            cancel_event.set()
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


# ─── Screenshot serve ───
@app.get("/api/screenshot/{name}")
def get_screenshot(name: str):
    # Güvenlik: sadece .png dosyaları, path traversal engeli
    if ".." in name or "/" in name or not name.endswith(".png"):
        raise HTTPException(400, "Geçersiz dosya adı.")
    path = ARTIFACTS_DIR / name
    if not path.exists():
        raise HTTPException(404, "Screenshot bulunamadı.")
    return FileResponse(path, media_type="image/png")


# ─── Static file serving ───
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


@app.get("/")
def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    return HTMLResponse(index_path.read_text(encoding="utf-8"))
