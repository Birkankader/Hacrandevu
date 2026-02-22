import os
import time
import threading
import concurrent.futures
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
PROFILE_DIR = BASE_DIR / ".chrome-profile"

# Varsayılan idle timeout (dakika)
SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", "10"))


@dataclass
class BrowserSession:
    """Tek bir hasta için browser session durumu."""
    session: object  # StealthySession instance
    page: object     # Playwright Page instance
    patient_tc: str
    logged_in: bool = False
    search_url: str = ""  # Login sonrası authenticated arama sayfası URL'i
    last_used: float = field(default_factory=time.time)

    def touch(self):
        self.last_used = time.time()

    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used

    def is_page_alive(self) -> bool:
        """Page hâlâ kullanılabilir mi kontrol et."""
        try:
            page = self.page
            # page.url erişimi browser crash'i yakalar
            _ = page.url
            return not page.is_closed()
        except Exception:
            return False


class SessionManager:
    """Hasta bazında browser session yönetimi (singleton)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._sessions: dict[str, BrowserSession] = {}
        self._executors: dict[str, concurrent.futures.ThreadPoolExecutor] = {}
        self._session_lock = threading.Lock()
        self._cleanup_interval = 30  # saniye
        self._idle_timeout = SESSION_IDLE_TIMEOUT_MINUTES * 60
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="session-cleanup"
        )
        self._cleanup_thread.start()

    def get_executor(self, tc: str) -> concurrent.futures.ThreadPoolExecutor:
        """Hasta için adanmış tekil thread executor döndür."""
        with self._session_lock:
            if tc not in self._executors:
                self._executors[tc] = concurrent.futures.ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix=f"bot_{tc}"
                )
            return self._executors[tc]

    def get_session(self, tc: str) -> BrowserSession | None:
        """Mevcut ve canlı session'ı döndür, yoksa None."""
        with self._session_lock:
            bs = self._sessions.get(tc)
        
        if bs is None:
            return None
            
        if not bs.is_page_alive():
            self.close_session(tc)
            return None
        bs.touch()
        return bs

    def create_session(self, tc: str, cfg: dict) -> BrowserSession:
        """Yeni browser session oluştur. Mevcut varsa kapat."""
        from scrapling.engines._browsers._stealth import StealthySession

        # Eski session varsa kapat
        self.close_session(tc)

        # Per-patient profil dizini
        profile_dir = PROFILE_DIR / tc
        profile_dir.mkdir(parents=True, exist_ok=True)

        session = StealthySession(
            headless=cfg.get("headless", True),
            block_webrtc=True,
            hide_canvas=True,
            allow_webgl=True,
            network_idle=False,
            timeout=cfg.get("timeout_ms", 45000),
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            google_search=False,
            user_data_dir=str(profile_dir),
        )
        session.start()

        # Yeni sayfa aç
        page = session.context.new_page()
        page.set_default_timeout(cfg.get("timeout_ms", 45000))

        bs = BrowserSession(
            session=session,
            page=page,
            patient_tc=tc,
        )

        with self._session_lock:
            self._sessions[tc] = bs

        return bs

    def close_session(self, tc: str):
        """Belirli bir hastanın session'ını kapat. (Caller lock tutmamalı)"""
        with self._session_lock:
            bs = self._sessions.pop(tc, None)
        if bs is None:
            return
        
        try:
            if bs.page and not bs.page.is_closed():
                bs.page.close()
        except Exception:
            pass
        try:
            bs.session.close()
        except Exception:
            pass

    def close_all(self):
        """Tüm session'ları kapat (shutdown)."""
        tcs = []
        with self._session_lock:
            tcs = list(self._sessions.keys())
        for tc in tcs:
            self.close_session(tc)

    def get_status(self, tc: str) -> dict:
        """Session durumunu döndür."""
        base_status = {"active": False, "logged_in": False, "idle_seconds": 0}
        with self._session_lock:
            bs = self._sessions.get(tc)
        
        if bs is None:
            return base_status
            
        alive = bs.is_page_alive()
        return {
            "active": alive,
            "logged_in": bs.logged_in and alive,
            "idle_seconds": round(bs.idle_seconds),
        }

    def _cleanup_loop(self):
        """Daemon thread: idle timeout aşan session'ları kapat."""
        while True:
            time.sleep(self._cleanup_interval)
            try:
                expired = []
                with self._session_lock:
                    for tc, bs in self._sessions.items():
                        if bs.idle_seconds > self._idle_timeout:
                            expired.append(tc)
                            
                for tc in expired:
                    print(f"[SESSION] Idle timeout: {tc[:4]}**** — kapatılıyor")
                    # Session nesnelerini oluşturulduğu thread'de kapatmak en güvenlisi
                    executor = self.get_executor(tc)
                    executor.submit(self.close_session, tc)
            except Exception:
                pass
