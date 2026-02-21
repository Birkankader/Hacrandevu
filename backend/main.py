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
from backend.bot_runner import run_bot_search

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
ARTIFACTS_DIR = BASE_DIR / "artifacts"

app = FastAPI(title="HacettepeBot", version="1.0.0")


# ─── Startup ───
@app.on_event("startup")
def on_startup():
    init_db()
    ARTIFACTS_DIR.mkdir(exist_ok=True)


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


# ─── WebSocket: Randevu Arama ───
@app.websocket("/ws/search")
async def ws_search(ws: WebSocket):
    await ws.accept()
    try:
        raw = await ws.receive_text()
        msg = json.loads(raw)

        if msg.get("action") != "search":
            await ws.send_json({"type": "error", "message": "Geçersiz action."})
            await ws.close()
            return

        patient_id = msg.get("patient_id")
        search_text = msg.get("search_text", "")
        randevu_type = msg.get("randevu_type", "internet randevu")

        patient = get_patient(patient_id)
        if not patient:
            await ws.send_json({"type": "error", "message": "Hasta bulunamadı."})
            await ws.close()
            return

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

        await ws.send_json({"type": "status", "step": "init", "message": "Bot başlatılıyor..."})

        loop = asyncio.get_event_loop()

        # Status callback — thread'den WebSocket'e push
        def status_callback(step, message):
            try:
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({"type": "status", "step": step, "message": message}),
                    loop,
                )
            except Exception:
                pass

        # Bot'u ayrı thread'de çalıştır (blocking I/O)
        result = await loop.run_in_executor(
            None,
            lambda: run_bot_search(bot_config, status_callback),
        )

        await ws.send_json({"type": "result", "data": result})

    except WebSocketDisconnect:
        pass
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
