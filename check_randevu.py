#!/usr/bin/env python3
"""
Hacettepe Üniversitesi Hastanesi Randevu Kontrol Botu
Python + Scrapling (StealthyFetcher) versiyonu

Stealth özellikler (Scrapling dahili):
  - patchright: webdriver/CDP algılama bypass
  - Canvas fingerprint gürültüsü (hide_canvas)
  - WebRTC IP sızıntı engelleme (block_webrtc)
  - Bézier eğrisi fare hareketi + doğal yazma
  - Birden fazla reCAPTCHA çözüm denemesi

Kullanım:
  .venv/bin/python check_randevu.py              # Normal çalıştırma
  .venv/bin/python check_randevu.py --setup      # İlk kurulum — reCAPTCHA güvenini oluştur
"""

import os
import sys
import json
import re
import time
import random
import shutil
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ─── Yapılandırma ───
SELECT_ALL_KEY = "Meta+a" if sys.platform == "darwin" else "Control+a"

def _build_default_cfg():
    """Ortam değişkenlerinden varsayılan yapılandırmayı oluştur."""
    setup = "--setup" in sys.argv
    return {
        "target_url": os.getenv(
            "TARGET_URL",
            "https://hastanerandevu.hacettepe.edu.tr/nucleus-hastaportal-randevu/public/main?user=PUBLIC",
        ),
        "tc": os.getenv("TC_KIMLIK_NO", ""),
        "birth_date": os.getenv("DOGUM_TARIHI", ""),
        "department": os.getenv("DEPARTMENT_TEXT", ""),
        "clinic": os.getenv("CLINIC_TEXT", ""),
        "doctor": os.getenv("DOCTOR_TEXT", ""),
        "headless": False if setup else os.getenv("HEADLESS", "true").lower() != "false",
        "check_interval_minutes": int(os.getenv("CHECK_INTERVAL_MINUTES", "0")),
        "timeout_ms": int(os.getenv("PAGE_TIMEOUT_MS", "45000")),
        "save_screenshot": os.getenv("SAVE_SCREENSHOT", "true").lower() != "false",
        "recaptcha_timeout_ms": int(os.getenv("RECAPTCHA_TIMEOUT_MS", "180000")),
        "recaptcha_max_retries": int(os.getenv("RECAPTCHA_MAX_RETRIES", "3")),
        "page_retries": int(os.getenv("PAGE_RETRIES", "5")),
        "phone": os.getenv("PHONE", ""),
        "email": os.getenv("EMAIL", ""),
        "captcha_api_key": os.getenv("CAPTCHA_API_KEY", ""),
        "randevu_type": os.getenv("RANDEVU_TYPE", "internet randevu"),
    }

CFG = _build_default_cfg()
SETUP_MODE = "--setup" in sys.argv

def _validate_env():
    """Zorunlu ortam değişkenlerini kontrol et — sadece CLI modunda çağrılır."""
    for key in ["TC_KIMLIK_NO", "DOGUM_TARIHI"]:
        if not os.getenv(key):
            print(f"[HATA] Eksik ortam değişkeni: {key}")
            sys.exit(1)

# Thread lock — eşzamanlı bot çalışmalarında CFG koruması
_bot_lock = threading.Lock()

# ─── Sabit dizinler ───
BASE_DIR = Path(__file__).parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
PROFILE_DIR = BASE_DIR / ".chrome-profile"
ARTIFACTS_DIR.mkdir(exist_ok=True)
PROFILE_DIR.mkdir(exist_ok=True)

MONTHS_TR = [
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
]

NEGATIVE_PATTERNS = [
    re.compile(r"uygun\s*randevu\s*bulunamadı", re.IGNORECASE),
    re.compile(r"müsait\s*randevu\s*yok", re.IGNORECASE),
    re.compile(r"randevu\s*bulunamadı", re.IGNORECASE),
    re.compile(r"seçilen\s*kriterlere\s*uygun\s*kayıt\s*yok", re.IGNORECASE),
    re.compile(r"randevu\s*alamadım", re.IGNORECASE),
]

POSITIVE_PATTERNS = [
    re.compile(r"uygun\s*randevu", re.IGNORECASE),
    re.compile(r"müsait", re.IGNORECASE),
    re.compile(r"randevu\s*saati", re.IGNORECASE),
    re.compile(r"tarih\s*seç", re.IGNORECASE),
]


# ═══════════════════════════════════════════════════════════════
#  Yardımcılar
# ═══════════════════════════════════════════════════════════════

def parse_birth_date(value):
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$", value)
    if not m:
        return None
    day, month, year = str(int(m[1])), int(m[2]), str(int(m[3]))
    if month < 1 or month > 12:
        return None
    return {"day": day, "month": month, "year": year,
            "month_padded": str(month).zfill(2), "month_name_tr": MONTHS_TR[month - 1]}


def human_delay(lo=200, hi=800):
    time.sleep(random.randint(lo, hi) / 1000)


def bezier_move(page, sx, sy, ex, ey, steps=25):
    """Bézier eğrisi ile doğal fare hareketi."""
    cx1 = sx + (ex - sx) * random.uniform(0.2, 0.5) + random.randint(-40, 40)
    cy1 = sy + (ey - sy) * random.uniform(0.0, 0.3) + random.randint(-30, 30)
    cx2 = sx + (ex - sx) * random.uniform(0.5, 0.8) + random.randint(-40, 40)
    cy2 = sy + (ey - sy) * random.uniform(0.7, 1.0) + random.randint(-30, 30)
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = u**3*sx + 3*u**2*t*cx1 + 3*u*t**2*cx2 + t**3*ex
        y = u**3*sy + 3*u**2*t*cy1 + 3*u*t**2*cy2 + t**3*ey
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.005, 0.02))


def simulate_human(page, extensive=False):
    """Fare hareketleri + scroll."""
    count = random.randint(4, 9) if extensive else random.randint(2, 5)
    for _ in range(count):
        x1, y1 = random.randint(80, 900), random.randint(80, 550)
        x2, y2 = random.randint(80, 900), random.randint(80, 550)
        bezier_move(page, x1, y1, x2, y2, steps=random.randint(12, 30))
        human_delay(80, 350)
    page.mouse.wheel(0, random.randint(50, 200))
    human_delay(200, 500)
    page.mouse.wheel(0, random.randint(-120, -30))
    human_delay(150, 400)


def human_type(page, locator, text):
    el = locator.first
    el.click()
    human_delay(100, 250)
    el.fill("")
    for ch in text:
        page.keyboard.type(ch, delay=random.randint(35, 120))
    human_delay(80, 250)
    # Tab ile blur tetikle — Vaadin sunucuya değeri göndersin
    page.keyboard.press("Tab")
    human_delay(100, 300)


def fill_first(page, candidates, value, use_human=True):
    for loc in candidates:
        try:
            if loc.count() > 0:
                if use_human:
                    human_type(page, loc, value)
                else:
                    el = loc.first
                    el.click(); el.fill(""); el.fill(value)
                return True
        except Exception:
            continue
    return False


def click_by_text(page, regex):
    for strategy in [
        lambda: page.get_by_role("button", name=regex).first,
        lambda: page.locator("vaadin-button, button").filter(has_text=regex).first,
    ]:
        try:
            el = strategy()
            if el.count() > 0:
                el.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def ensure_kvkk(page):
    """KVKK onay kutusunu işaretle ve Vaadin'e state change bildir."""
    clicked = False
    try:
        cb = page.locator("vaadin-checkbox").first
        if cb.count() > 0:
            cb.click(timeout=5000)
            human_delay(200, 400)
            clicked = True
    except Exception:
        pass
    if not clicked:
        try:
            inp = page.locator('input[type="checkbox"]').first
            if inp.count() > 0:
                inp.click(timeout=5000, force=True)
                clicked = True
        except Exception:
            pass
    if not clicked:
        try:
            parent = page.locator('vaadin-horizontal-layout:has-text("KVKK")').first
            if parent.count() > 0:
                cb = parent.locator('vaadin-checkbox, input[type="checkbox"]').first
                if cb.count() > 0:
                    cb.click(timeout=5000)
                    clicked = True
        except Exception:
            pass

    if clicked:
        # Vaadin checkbox change event'inin sunucuya ulaşmasını tetikle
        try:
            page.evaluate("""() => {
                var cb = document.querySelector('vaadin-checkbox');
                if (cb) {
                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                    cb.dispatchEvent(new CustomEvent('checked-changed', {bubbles: true}));
                }
            }""")
        except Exception:
            pass
        human_delay(200, 400)
    return clicked


def choose_dropdown_by_index(page, combo_index, option_text):
    """Vaadin combo-box'u indeks ile seç."""
    if not option_text:
        return True
    combos = page.locator("vaadin-combo-box:visible")
    if combos.count() <= combo_index:
        print(f"  [DEBUG] {combos.count()} combo-box bulundu, index {combo_index} yok")
        return False
    try:
        combo = combos.nth(combo_index)
        inp = combo.locator("input").first

        # Input'a tıkla (combo-box açılır)
        inp.click(timeout=5000)
        human_delay(300, 600)
        # Temizle
        inp.press(SELECT_ALL_KEY)
        inp.press("Backspace")
        human_delay(200, 400)
        # type() ile karakter karakter yaz (server-side filtering tetiklenir)
        page.keyboard.type(option_text[:15], delay=80)
        human_delay(1000, 2000)  # AJAX yanıtı bekle

        # Filtrelenen sonuçtan seç
        for sel in ['vaadin-combo-box-item', 'vaadin-combo-box-overlay [role="option"]']:
            items = page.locator(sel).all()
            for item in items:
                try:
                    txt = item.text_content() or ""
                    if option_text.lower()[:15] in txt.lower():
                        item.click(timeout=5000)
                        human_delay(500, 800)
                        return True
                except Exception:
                    continue

        # Fallback: get_by_text
        try:
            match = page.get_by_text(option_text, exact=False).first
            if match.count() > 0 and match.is_visible():
                match.click(timeout=5000)
                human_delay(500, 800)
                return True
        except Exception:
            pass

        # Son çare: Enter ile ilk sonucu al
        page.keyboard.press("Enter")
        human_delay(500, 800)
        return True
    except Exception as e:
        print(f"  [DEBUG] Dropdown hatası: {e}")
        return False


def fill_combo_commit(page, combo, candidates):
    """Vaadin combo-box'a değer yaz ve commit et.
    fill() yerine keyboard.type() kullanır — Vaadin server-side filtering ile uyumlu.
    """
    for c in candidates:
        if not c:
            continue
        try:
            combo.click(timeout=5000)
            human_delay(100, 200)
            # Mevcut değeri temizle — select all + delete
            page.keyboard.press(SELECT_ALL_KEY)
            page.keyboard.press("Backspace")
            human_delay(100, 200)
            # keyboard.type ile yaz (Vaadin filtering tetiklenir)
            page.keyboard.type(str(c), delay=60)
            human_delay(500, 800)
            # Overlay'den eşleşen sonucu bul ve tıkla
            found = False
            for sel in ['vaadin-combo-box-item', 'vaadin-combo-box-overlay [role="option"]']:
                items = page.locator(sel).all()
                for item in items:
                    try:
                        txt = (item.text_content() or "").strip()
                        if txt.lower() == str(c).strip().lower():
                            item.click(timeout=3000)
                            found = True
                            break
                    except Exception:
                        continue
                if found:
                    break
            if not found:
                # Overlay'de tam eşleşme yoksa Enter ile ilk sonucu al
                page.keyboard.press("Enter")
            human_delay(300, 500)
            val = (combo.input_value() or "").strip().lower()
            if val == str(c).strip().lower():
                return True
            # Tab ile commit dene
            page.keyboard.press("Tab")
            human_delay(200, 400)
            val = (combo.input_value() or "").strip().lower()
            if val == str(c).strip().lower():
                return True
        except Exception:
            continue
    return False


def fill_birth_combos(page, birth_date):
    parts = parse_birth_date(birth_date)
    if not parts:
        return False
    combos = page.get_by_role("combobox")
    if combos.count() < 3:
        return False
    if not fill_combo_commit(page, combos.nth(0), [parts["year"]]):
        return False
    human_delay(300, 600)
    if not fill_combo_commit(page, combos.nth(1),
                             [parts["month_name_tr"], parts["month_padded"], str(parts["month"])]):
        return False
    human_delay(300, 600)
    return fill_combo_commit(page, combos.nth(2), [parts["day"].zfill(2), parts["day"]])


# ═══════════════════════════════════════════════════════════════
#  reCAPTCHA İşleyici
# ═══════════════════════════════════════════════════════════════

def _recaptcha_present(page):
    sel = 'iframe[title*="reCAPTCHA" i], iframe[src*="recaptcha" i]'
    return page.locator(sel).count() > 0


def _try_auto_solve(page, attempt: int) -> bool:
    """Tek bir otomatik reCAPTCHA çözme denemesi."""
    sel = 'iframe[title*="reCAPTCHA" i], iframe[src*="recaptcha" i]'

    print(f"  [Deneme {attempt}] Fare hareketi simüle ediliyor...")
    try:
        simulate_human(page, extensive=True)
    except Exception:
        print(f"  [Deneme {attempt}] Tarayıcı yanıt vermiyor.")
        return False
    human_delay(1000, 2500)

    # reCAPTCHA checkbox'ına doğal yaklaş
    try:
        iframe_el = page.locator(sel).first
        bbox = iframe_el.bounding_box()
        if not bbox:
            return False
        sx, sy = random.randint(200, 700), random.randint(200, 500)
        tx = bbox["x"] + 28 + random.uniform(-4, 4)
        ty = bbox["y"] + 28 + random.uniform(-4, 4)
        bezier_move(page, sx, sy, tx, ty, steps=random.randint(20, 35))
        human_delay(300, 700)
    except Exception:
        pass

    # Checkbox'a tıkla
    try:
        frame = page.frame_locator(sel).first
        anchor = frame.locator("#recaptcha-anchor")
        if anchor.count() == 0:
            return False
        anchor.click(timeout=5000)
        print(f"  [Deneme {attempt}] Checkbox tıklandı, sonuç bekleniyor...")
        # 8 saniye yeterli — challenge açılacaksa çoktan açılmıştır
        frame.locator('#recaptcha-anchor[aria-checked="true"]').wait_for(timeout=8000)
        return True
    except Exception:
        return False



def _run_in_main_world(page, js_code):
    """Ana JS world'de kod çalıştır.
    Patchright isolated_context=False desteği varsa onu kullan,
    yoksa <script> tag fallback.
    """
    try:
        page.evaluate(js_code, isolated_context=False)
    except TypeError:
        # isolated_context parametresi desteklenmiyorsa <script> tag fallback
        page.evaluate("""(js) => {
            var s = document.createElement('script');
            s.textContent = js;
            document.head.appendChild(s);
            document.head.removeChild(s);
        }""", js_code)


def _eval_in_main_world(page, js_code, arg=None):
    """Ana JS world'de kod çalıştır ve sonucu döndür.
    _run_in_main_world'den farkı: return değerini alabilir.
    """
    try:
        if arg is not None:
            return page.evaluate(js_code, arg, isolated_context=False)
        return page.evaluate(js_code, isolated_context=False)
    except TypeError:
        # Fallback: script tag ile çalıştır, sonucu DOM attribute üzerinden al
        wrapper = f"""(function(){{
            var __result;
            try {{ __result = (function(){{ {js_code} }})(); }} catch(e) {{ __result = 'ERROR:' + e.message; }}
            document.body.setAttribute('data-mw-result', JSON.stringify(__result));
        }})();"""
        page.evaluate("""(js) => {{
            var s = document.createElement('script');
            s.textContent = js;
            document.head.appendChild(s);
            document.head.removeChild(s);
        }}""", wrapper)
        import json as _json
        raw = page.evaluate("() => document.body.getAttribute('data-mw-result') || 'null'")
        page.evaluate("() => document.body.removeAttribute('data-mw-result')")
        try:
            return _json.loads(raw)
        except Exception:
            return raw


def _solve_with_2captcha(page, api_key, attempt=1, max_attempts=2) -> bool:
    """2captcha servisi ile reCAPTCHA v2 çöz.

    Geliştirilmiş versiyon:
    - Vaadin $server.callback birincil callback yöntemi
    - Token enjeksiyon başarısız olursa yeni token ile tekrar deneme
    - Post-enjeksiyon doğrulama
    - NO_CALLBACK durumunda False dönüş (riskli True yerine)
    """
    try:
        from twocaptcha import TwoCaptcha
    except ImportError:
        print("  [2captcha] 2captcha-python paketi yüklü değil. Kurulum: pip install 2captcha-python")
        return False

    print(f"  [2captcha] === Token deneme {attempt}/{max_attempts} ===")

    # ── Sitekey çıkarma (3 strateji) ──
    sitekey = None

    # Strateji 1: iframe src parametresinden
    try:
        iframe = page.locator('iframe[src*="recaptcha" i]').first
        if iframe.count() > 0:
            src = iframe.get_attribute("src") or ""
            import urllib.parse
            params = urllib.parse.parse_qs(urllib.parse.urlparse(src).query)
            sitekey = params.get("k", [None])[0]
            if sitekey:
                print(f"  [2captcha] Sitekey iframe src'den alındı: {sitekey[:12]}...")
    except Exception:
        pass

    # Strateji 2: data-sitekey attribute
    if not sitekey:
        try:
            el = page.locator("[data-sitekey]").first
            if el.count() > 0:
                sitekey = el.get_attribute("data-sitekey")
                if sitekey:
                    print(f"  [2captcha] Sitekey data-sitekey'den alındı: {sitekey[:12]}...")
        except Exception:
            pass

    # Strateji 3: Sayfa kaynağından regex ile
    if not sitekey:
        try:
            html = page.content()
            match = re.search(r'(?:data-sitekey|sitekey)["\s:=]+["\']([A-Za-z0-9_-]{40})', html)
            if match:
                sitekey = match.group(1)
                print(f"  [2captcha] Sitekey sayfa kaynağından alındı: {sitekey[:12]}...")
        except Exception:
            pass

    if not sitekey:
        print("  [2captcha] Sitekey bulunamadı — reCAPTCHA widget sayfada yok olabilir.")
        return False

    # ── 2captcha'ya çözüm isteği gönder ──
    print(f"  [2captcha] Sitekey: {sitekey[:16]}... | URL: {page.url[:60]}")
    print("  [2captcha] Çözüm isteniyor... (genellikle 20-60 saniye)")
    token = None
    try:
        solver = TwoCaptcha(api_key)
        solver.recaptcha_timeout = 300   # 5 dk (token 120s'de expire olur, fazla bekleme gereksiz)
        solver.polling_interval = 5      # 5s aralıkla kontrol (daha hızlı yanıt)
        result = solver.recaptcha(sitekey=sitekey, url=page.url)
        token = result.get("code", "") if isinstance(result, dict) else str(result)
        if not token:
            print("  [2captcha] Servis boş token döndü.")
            return False
        print(f"  [2captcha] Token alındı ({len(token)} karakter).")
    except Exception as e:
        err_str = str(e)
        if "ERROR_CAPTCHA_UNSOLVABLE" in err_str:
            print("  [2captcha] Captcha çözülemedi (ERROR_CAPTCHA_UNSOLVABLE).")
        elif "ERROR_ZERO_BALANCE" in err_str:
            print("  [2captcha] Bakiye yetersiz (ERROR_ZERO_BALANCE)!")
        elif "ERROR_WRONG_USER_KEY" in err_str or "ERROR_KEY_DOES_NOT_EXIST" in err_str:
            print("  [2captcha] Geçersiz API anahtarı!")
        else:
            print(f"  [2captcha] Servis hatası: {e}")
        return False

    # ── Challenge popup açıksa kapat ──
    try:
        bframe = page.locator('iframe[src*="recaptcha" i][title*="challenge" i]')
        if bframe.count() > 0:
            page.keyboard.press("Escape")
            time.sleep(1)
            print("  [2captcha] Challenge popup kapatıldı.")
    except Exception:
        pass

    # ── Token enjeksiyonu (tek atomik çağrı — ana JS world) ──
    print("  [2captcha] Token enjekte ediliyor...")
    try:
        # Tek bir ana-world çağrısında: textarea doldur + callback tetikle
        inject_js = """(function(token) {
            var ok = false;
            var method = '';
            var errors = [];

            // ── Textarea'ları doldur ──
            var selectors = [
                'textarea[name="g-recaptcha-response"]',
                '#g-recaptcha-response',
                'textarea.g-recaptcha-response'
            ];
            selectors.forEach(function(sel) {
                document.querySelectorAll(sel).forEach(function(el) {
                    el.value = token;
                    el.innerHTML = token;
                    el.style.display = 'block';
                });
            });

            // ─── Yöntem 1 (BİRİNCİL): Vaadin $server — birden fazla metod adı dene ───
            var serverMethods = ['callback', 'setResponse', 'verifyCallback',
                                 'onCaptchaResponse', 'recaptchaCallback', 'onCallback'];
            var searchRoots = [
                document.querySelector('.g-recaptcha'),
                document.querySelector('[data-sitekey]'),
                document.querySelector('#recaptcha-container'),
                document.querySelector('div[id*="recaptcha"]')
            ];
            for (var r = 0; r < searchRoots.length && !ok; r++) {
                var el = searchRoots[r];
                while (el && !ok) {
                    if (el.$server) {
                        for (var mi = 0; mi < serverMethods.length && !ok; mi++) {
                            var mName = serverMethods[mi];
                            if (typeof el.$server[mName] === 'function') {
                                try {
                                    el.$server[mName](token);
                                    ok = true;
                                    method = '$server.' + mName + ' (' + (el.tagName || '?') + ')';
                                } catch(e) {
                                    errors.push('$server.' + mName + ' hata: ' + e.message);
                                }
                            }
                        }
                    }
                    el = el.parentElement;
                }
            }

            // Vaadin geniş tarama — tüm $server elementlerini tara
            if (!ok) {
                var allEls = document.querySelectorAll('*');
                for (var i = 0; i < allEls.length && !ok; i++) {
                    var vel = allEls[i];
                    if (vel.$server) {
                        for (var mi2 = 0; mi2 < serverMethods.length && !ok; mi2++) {
                            var mName2 = serverMethods[mi2];
                            if (typeof vel.$server[mName2] === 'function') {
                                try {
                                    vel.$server[mName2](token);
                                    ok = true;
                                    method = '$server.' + mName2 + ' (scan: ' +
                                             (vel.tagName || '?') + '#' + (vel.id || '') + ')';
                                } catch(e) {
                                    errors.push('scan $server.' + mName2 + ': ' + e.message);
                                }
                            }
                        }
                    }
                }
            }

            // ─── Yöntem 2: myCallback (Vaadin closure) ───
            if (!ok && typeof myCallback === 'function') {
                try { myCallback(token); ok = true; method = 'myCallback'; }
                catch(e) { errors.push('myCallback hata: ' + e.message); }
            }

            // ─── Yöntem 3: data-callback attribute ───
            if (!ok) {
                var cbDivs = document.querySelectorAll('[data-callback]');
                for (var j = 0; j < cbDivs.length && !ok; j++) {
                    var cn = cbDivs[j].getAttribute('data-callback');
                    if (cn && typeof window[cn] === 'function') {
                        try { window[cn](token); ok = true; method = 'data-callback: ' + cn; }
                        catch(e) { errors.push(cn + '() hata: ' + e.message); }
                    }
                }
            }

            // ─── Yöntem 4: ___grecaptcha_cfg callback (derinlik 10) ───
            if (!ok) {
                try {
                    if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                        for (var cid in ___grecaptcha_cfg.clients) {
                            var client = ___grecaptcha_cfg.clients[cid];
                            function findCb(obj, depth) {
                                if (depth > 10 || !obj) return null;
                                for (var key in obj) {
                                    try {
                                        if (typeof obj[key] === 'function' &&
                                            (key.toLowerCase().indexOf('callback') >= 0 ||
                                             key === 'cb' || key === 'fn')) {
                                            return obj[key];
                                        }
                                        if (typeof obj[key] === 'object' && obj[key] !== null) {
                                            var found = findCb(obj[key], depth + 1);
                                            if (found) return found;
                                        }
                                    } catch(e) { continue; }
                                }
                                return null;
                            }
                            var cb = findCb(client, 0);
                            if (cb) {
                                try { cb(token); ok = true; method = '___grecaptcha_cfg callback'; }
                                catch(e) { errors.push('grecaptcha_cfg hata: ' + e.message); }
                            }
                            if (ok) break;
                        }
                    }
                } catch(e) {
                    errors.push('grecaptcha_cfg arama: ' + e.message);
                }
            }

            // grecaptcha.getResponse override
            if (typeof grecaptcha !== 'undefined') {
                try { grecaptcha.getResponse = function(){ return token; }; } catch(e) {}
            }

            return {
                ok: ok,
                method: method,
                errors: errors
            };
        })('""" + token.replace("'", "\\'") + """');"""

        rc_result = _eval_in_main_world(page, inject_js)

        if not rc_result or not isinstance(rc_result, dict):
            print(f"  [2captcha] Enjeksiyon sonucu alınamadı: {rc_result}")
            if attempt < max_attempts:
                human_delay(1000, 2000)
                return _solve_with_2captcha(page, api_key, attempt + 1, max_attempts)
            return False

        if rc_result.get("errors"):
            print(f"  [2captcha] Callback hataları: {'; '.join(rc_result['errors'][:3])}")

        if rc_result.get("ok"):
            print(f"  [2captcha] Token enjekte edildi ve callback tetiklendi: {rc_result.get('method', '?')}")
        else:
            print("  [2captcha] Callback bulunamadı — hiçbir Vaadin $server yöntemi eşleşmedi.")
            if attempt < max_attempts:
                print(f"  [2captcha] {attempt + 1}. token denenecek...")
                human_delay(1000, 2000)
                return _solve_with_2captcha(page, api_key, attempt + 1, max_attempts)
            else:
                print("  [2captcha] Tüm denemeler callback bulamadı.")
                return False

        # ── Post-enjeksiyon doğrulama (HIZLI — token expire olmasın) ──
        print("  [2captcha] Kısa doğrulama bekleniyor (1.5 saniye)...")
        time.sleep(1.5)

        # Doğrulama 1: reCAPTCHA widget checked oldu mu?
        if _verify_recaptcha_checked(page):
            print("  [2captcha] reCAPTCHA checkbox doğrulandı (checked)!")
            return True

        # Doğrulama 2: reCAPTCHA iframe kaybolmuş olabilir (Vaadin sayfa yenileme)
        if not _recaptcha_present(page):
            print("  [2captcha] reCAPTCHA widget kayboldu — başarılı!")
            return True

        # Doğrulama 3: Vaadin hata bildirimi kontrol
        try:
            notif = page.locator("vaadin-notification-card")
            if notif.count() > 0:
                notif_text = (notif.first.text_content() or "").lower()
                if any(kw in notif_text for kw in ["hata", "geçersiz", "başarısız", "doğrulama"]):
                    print(f"  [2captcha] Sunucu hata bildirimi: {notif_text[:150]}")
                    if attempt < max_attempts:
                        _dismiss_challenge(page)
                        human_delay(1000, 2000)
                        return _solve_with_2captcha(page, api_key, attempt + 1, max_attempts)
                    return False
        except Exception:
            pass

        # Callback başarılı çağrıldı — sunucu tarafı doğrulama widget'ta görünmeyebilir
        # (2captcha token enjeksiyonunda checkbox her zaman checked olmaz)
        print("  [2captcha] Callback başarıyla çağrıldı, form gönderimi ile devam ediliyor.")
        return True

    except Exception as e:
        print(f"  [2captcha] Enjeksiyon hatası: {e}")
        if attempt < max_attempts:
            human_delay(1000, 2000)
            return _solve_with_2captcha(page, api_key, attempt + 1, max_attempts)
        return False


def _verify_recaptcha_checked(page) -> bool:
    """reCAPTCHA checkbox'ının gerçekten checked olduğunu doğrula."""
    try:
        sel = 'iframe[title*="reCAPTCHA" i], iframe[src*="recaptcha" i]'
        frame = page.frame_locator(sel).first
        checked = frame.locator('#recaptcha-anchor[aria-checked="true"]')
        return checked.count() > 0
    except Exception:
        return False


def _notify_user(message):
    """macOS bildirimi gönder (sesli)."""
    if sys.platform == "darwin":
        try:
            import subprocess
            subprocess.Popen([
                "osascript", "-e",
                f'display notification "{message}" with title "HacettepeBot" sound name "Glass"'
            ])
        except Exception:
            pass
    print(f"\a")  # Terminal bell


def _dismiss_challenge(page):
    """reCAPTCHA challenge popup'ını kapat ve widget'ı sıfırla."""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        bframe = page.locator('iframe[src*="recaptcha" i][title*="challenge" i]')
        if bframe.count() > 0:
            page.mouse.click(100, 100)
            time.sleep(0.5)
    except Exception:
        pass
    try:
        _run_in_main_world(page, """(function(){
            if (typeof grecaptcha !== 'undefined') {
                try { grecaptcha.reset(); } catch(e) {}
            }
        })();""")
        time.sleep(1)
    except Exception:
        pass


def _wait_for_manual_solve(page, timeout_s) -> bool:
    """Kullanıcının tarayıcıda reCAPTCHA çözmesini bekle."""
    try:
        sel = 'iframe[title*="reCAPTCHA" i], iframe[src*="recaptcha" i]'
        frame = page.frame_locator(sel).first
        frame.locator('#recaptcha-anchor[aria-checked="true"]').wait_for(
            timeout=timeout_s * 1000
        )
        return True
    except Exception:
        return False


def handle_recaptcha(page, timeout_ms, headless, max_retries, captcha_api_key=None) -> bool:
    """reCAPTCHA çözme stratejisi (2captcha öncelikli):

    1. CAPTCHA_API_KEY varsa → 2captcha HEMEN dene (zaman kaybetme)
    2. 2captcha başarısızsa → auto-solve denemeleri
    3. Hâlâ çözülemediyse ve headless değilse → manuel çözüm (son çare)
    """
    human_delay(500, 1000)

    if not _recaptcha_present(page):
        print("[BILGI] reCAPTCHA algılanmadı — stealth mod başarılı!")
        return True

    api_key = captcha_api_key or CFG.get("captcha_api_key", "")
    print("[BILGI] reCAPTCHA algılandı.")

    # ══════════════════════════════════════════════════════════
    #  Adım 1: 2captcha (BİRİNCİL YÖNTEM)
    # ══════════════════════════════════════════════════════════
    if api_key:
        print("[BILGI] CAPTCHA_API_KEY mevcut — 2captcha birincil yöntem olarak deneniyor...")

        # Kısa bir insan davranışı simüle et (tamamen hareketsiz sayfa şüpheli)
        try:
            simulate_human(page, extensive=False)
        except Exception:
            pass
        human_delay(500, 1500)

        # 2captcha ile çöz (dahili olarak 2 token denemesi yapar)
        if _solve_with_2captcha(page, api_key, attempt=1, max_attempts=2):
            print("[BILGI] reCAPTCHA 2captcha ile çözüldü!")
            return True
        else:
            print("[UYARI] 2captcha başarısız oldu. Alternatif yöntemler deneniyor...")
            _dismiss_challenge(page)
            human_delay(1000, 2000)
    else:
        print("[BILGI] CAPTCHA_API_KEY tanımlı değil — otomatik/manuel yöntemler kullanılacak.")

    # ══════════════════════════════════════════════════════════
    #  Adım 2: Auto-solve denemeleri (yedek yöntem)
    # ══════════════════════════════════════════════════════════
    print("[BILGI] Auto-solve denemeleri başlıyor...")
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            _dismiss_challenge(page)
            human_delay(2000, 4000)

        try:
            simulate_human(page, extensive=True)
        except Exception:
            print(f"  [Deneme {attempt}] Tarayıcı yanıt vermiyor.")
            return False
        human_delay(1000, 2000)

        if _try_auto_solve(page, attempt):
            time.sleep(1)
            if _verify_recaptcha_checked(page):
                print(f"[BILGI] reCAPTCHA {attempt}. denemede otomatik geçildi!")
                return True

        print(f"  [Deneme {attempt}/{max_retries}] Auto-solve başarısız.")

    # ══════════════════════════════════════════════════════════
    #  Adım 3: Manuel çözüm (SON ÇARE — sadece headless değilse)
    # ══════════════════════════════════════════════════════════
    if not headless:
        _dismiss_challenge(page)
        human_delay(500, 1000)

        _notify_user("reCAPTCHA çözmeniz gerekiyor! (Son çare)")
        manual_timeout = min(timeout_ms // 1000, 120)
        print(f"[BILGI] Tüm otomatik yöntemler başarısız. Tarayıcıda reCAPTCHA\'yı çözün ({manual_timeout}s)...")

        if _wait_for_manual_solve(page, manual_timeout):
            print("[BILGI] reCAPTCHA manuel olarak çözüldü!")
            return True
        print("[UYARI] Manuel çözüm zaman aşımı.")

    print("[UYARI] reCAPTCHA çözülemedi — tüm yöntemler denendi.")
    return False

# ═══════════════════════════════════════════════════════════════
#  Bilgi Tamamlama Dialogu
# ═══════════════════════════════════════════════════════════════

def handle_info_dialog(page, phone, email):
    """Giriş sonrası bilgi tamamlama dialogunu doldur (Vaadin overlay)."""
    print("[BILGI] Bilgi tamamlama dialogu kontrol ediliyor...")
    try:
        page.locator("vaadin-dialog-overlay").wait_for(state="attached", timeout=8000)
        print("[BILGI] Vaadin dialog overlay bulundu!")
    except Exception:
        try:
            onayla = page.get_by_role("button", name=re.compile(r"onayla", re.I))
            if onayla.count() == 0:
                print("[BILGI] Bilgi dialogu yok, devam.")
                return True
        except Exception:
            print("[BILGI] Bilgi dialogu yok, devam.")
            return True

    time.sleep(1)

    # Telefon alanını doldur
    if phone:
        filled = False
        for get_field in [
            lambda: page.locator('vaadin-dialog-overlay input[placeholder*="5"]').first,
            lambda: page.locator('input[placeholder*="5xx"]').first,
            lambda: page.locator('input[placeholder*="5"]').nth(
                page.locator('input[placeholder*="5"]').count() - 1
            ),
            lambda: page.get_by_placeholder(re.compile(r"5xx|telefon", re.I)).first,
        ]:
            try:
                field = get_field()
                if field.count() > 0 and field.is_visible():
                    field.click()
                    field.fill("")
                    field.fill(phone)
                    print(f"[BILGI] Telefon dolduruldu: {phone[:3]}***")
                    human_delay(300, 600)
                    filled = True
                    break
            except Exception:
                continue
        if not filled:
            print("[UYARI] Telefon alanı bulunamadı.")

    # Email doldur
    if email:
        for get_field in [
            lambda: page.locator('vaadin-dialog-overlay input[placeholder*="@"]').first,
            lambda: page.locator('input[placeholder*="@"]').first,
            lambda: page.get_by_placeholder(re.compile(r"@|email", re.I)).first,
        ]:
            try:
                field = get_field()
                if field.count() > 0 and field.is_visible():
                    field.click()
                    field.fill("")
                    field.fill(email)
                    print("[BILGI] Email dolduruldu.")
                    human_delay(200, 400)
                    break
            except Exception:
                continue

    # Onayla butonu
    human_delay(300, 600)
    for get_btn in [
        lambda: page.get_by_role("button", name=re.compile(r"onayla", re.I)).first,
        lambda: page.locator("vaadin-dialog-overlay vaadin-button").first,
        lambda: page.locator("vaadin-dialog-overlay button").first,
    ]:
        try:
            btn = get_btn()
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=5000)
                print("[BILGI] Onayla tıklandı!")
                # Dialog overlay'in kapanmasını bekle
                try:
                    page.locator("vaadin-dialog-overlay").wait_for(
                        state="detached", timeout=15000
                    )
                    print("[BILGI] Dialog kapandı.")
                except Exception:
                    time.sleep(3)
                    try:
                        if page.locator("vaadin-dialog-overlay").count() > 0:
                            page.keyboard.press("Escape")
                            time.sleep(2)
                    except Exception:
                        pass
                time.sleep(2)
                return True
        except Exception:
            continue

    print("[UYARI] Onayla butonu tıklanamadı.")
    return False


class RecaptchaFailed(Exception):
    pass


# ═══════════════════════════════════════════════════════════════
#  Bot — Scrapling StealthyFetcher
# ═══════════════════════════════════════════════════════════════

class HacettepeBot:
    def __init__(self, config_override=None, status_callback=None):
        self.result = None
        self._status_callback = status_callback
        self._cfg = dict(CFG)
        if config_override:
            self._cfg.update(config_override)

    def _emit(self, step, message):
        """Print + status callback çağrısı."""
        print(message)
        if self._status_callback:
            try:
                self._status_callback(step, message)
            except Exception:
                pass

    def _screenshot(self, page, name):
        try:
            page.screenshot(path=str(ARTIFACTS_DIR / f"{name}.png"), full_page=True)
        except Exception:
            pass

    def run_once(self) -> int:
        """Tek kontrol — reCAPTCHA başarısız olursa temiz profil ile tekrar dener."""
        from scrapling.fetchers import StealthyFetcher

        cfg = self._cfg
        max_retries = cfg["page_retries"]
        self._emit("init", f"[BILGI] Hedef: {cfg['target_url']}")
        self._emit("init", f"[BILGI] Mod: {'SETUP' if SETUP_MODE else 'headless=' + str(cfg['headless'])}")

        for attempt in range(1, max_retries + 1):
            # Her denemede temiz profil
            if PROFILE_DIR.exists():
                shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            PROFILE_DIR.mkdir(exist_ok=True)

            self._emit("retry", f"\n[BILGI] === Deneme {attempt}/{max_retries} ===")

            # page_action sonucunu paylaşmak için closure dict
            flow_result = {"code": None, "error": None}

            def page_action(page):
                page.set_default_timeout(cfg["timeout_ms"])
                try:
                    code = self._flow(page)
                    flow_result["code"] = code
                except RecaptchaFailed:
                    flow_result["error"] = "recaptcha"
                except Exception as e:
                    flow_result["error"] = str(e)
                    try:
                        self._screenshot(page, "error")
                    except Exception:
                        pass

            try:
                StealthyFetcher.fetch(
                    cfg["target_url"],
                    headless=cfg["headless"],
                    block_webrtc=True,
                    hide_canvas=True,
                    allow_webgl=True,
                    network_idle=True,
                    timeout=300000,       # 5 dk — page_action içindeki tüm flow için
                    page_action=page_action,
                    locale="tr-TR",
                    timezone_id="Europe/Istanbul",
                    google_search=False,  # Hacettepe portalına Google referer gönderme
                )
            except Exception as e:
                if flow_result["code"] is None and flow_result["error"] is None:
                    flow_result["error"] = str(e)

            # Sonuç değerlendirme
            if flow_result["code"] is not None:
                return flow_result["code"]
            elif flow_result["error"] == "recaptcha":
                print("[BILGI] reCAPTCHA başarısız, tekrar denenecek...")
                if attempt < max_retries:
                    human_delay(2000, 5000)
                continue
            else:
                err = flow_result["error"] or "Bilinmeyen hata"
                if "closed" in err.lower() or "target" in err.lower():
                    print(f"[BILGI] Tarayıcı kapandı: {err}")
                    if attempt < max_retries:
                        human_delay(2000, 5000)
                        continue
                print(f"[HATA] {err}")
                return 1

        print("[HATA] Tüm denemeler başarısız.")
        return 1


    def _search_and_select_first(self, page, search_text):
        """Üstteki arama alanına yaz, dialog'dan tüm alternatifleri topla, ilkini seç.

        Returns:
            (selected: bool, alternatives: list[str])
        """

        # ── Adım 1: "Birim veya Doktor ismi ile arama" metninin altındaki input'u bul ──
        search_field = None

        # JS ile label'e en yakın non-combo input'u bul ve data attribute ile işaretle
        try:
            found = page.evaluate("""() => {
                var walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT, null);
                var labelEl = null;
                while (walker.nextNode()) {
                    if (walker.currentNode.textContent.indexOf('Birim veya Doktor ismi ile arama') >= 0) {
                        labelEl = walker.currentNode.parentElement;
                        break;
                    }
                }
                if (!labelEl) return false;

                var labelRect = labelEl.getBoundingClientRect();

                var candidates = document.querySelectorAll('vaadin-text-field, input');
                var best = null;
                var bestDist = 999999;
                for (var i = 0; i < candidates.length; i++) {
                    var el = candidates[i];
                    if (el.closest('vaadin-combo-box')) continue;
                    if (el.type === 'hidden' || el.type === 'checkbox') continue;
                    var rect = el.getBoundingClientRect();
                    if (rect.width < 10 || rect.height < 5) continue;
                    var dy = rect.top - labelRect.bottom;
                    if (dy < -20) continue;
                    var dist = Math.abs(dy) + Math.abs(rect.left - labelRect.left) * 0.5;
                    if (dist < bestDist) {
                        bestDist = dist;
                        best = el;
                    }
                }
                if (best) {
                    best.setAttribute('data-hacbot-search', 'true');
                    return true;
                }
                return false;
            }""")

            if found:
                search_field = page.locator('[data-hacbot-search="true"]').first
                if search_field.count() == 0:
                    search_field = None
                else:
                    print("  [ARAMA] Arama alanı JS ile bulundu.")
        except Exception as e:
            print(f"  [ARAMA] JS alan arama hatası: {e}")

        # Fallback: label text ile parent üzerinden
        if not search_field:
            try:
                label = page.get_by_text("Birim veya Doktor ismi ile arama")
                if label.count() > 0:
                    parent = label.locator("..")
                    inp = parent.locator("vaadin-text-field, input").first
                    if inp.count() > 0 and inp.is_visible():
                        search_field = inp
                        print("  [ARAMA] Arama alanı fallback ile bulundu.")
            except Exception:
                pass

        if not search_field:
            print("  [ARAMA] Arama alanı bulunamadı.")
            self._screenshot(page, "debug-no-search-field")
            return False, []

        print(f"  [ARAMA] Arama alanı bulundu, yazılıyor: {search_text[:30]}...")
        try:
            tag = search_field.evaluate("el => el.tagName.toLowerCase()")
            if tag == "vaadin-text-field":
                search_field.locator("input").first.click()
            else:
                search_field.click()
            human_delay(200, 400)
            page.keyboard.press(SELECT_ALL_KEY)
            page.keyboard.press("Backspace")
            human_delay(100, 200)
            page.keyboard.type(search_text, delay=60)
            human_delay(1500, 3000)
        except Exception as e:
            print(f"  [ARAMA] Arama alanına yazılamadı: {e}")
            return False, []

        self._screenshot(page, "debug-after-search-type")

        # ── Adım 2: Enter ile arama tetikle ──
        try:
            page.keyboard.press("Enter")
            print("  [ARAMA] Enter ile arama tetiklendi.")
            human_delay(2000, 4000)
        except Exception:
            pass

        self._screenshot(page, "debug-after-search-submit")

        # ── Adım 3: Modal/dialog açıldıysa, içindeki arama alanına search_text yaz ──
        dialog_found = False
        try:
            dialog = page.locator("vaadin-dialog-overlay")
            if dialog.count() > 0 and dialog.first.is_visible():
                dialog_found = True
                print("  [ARAMA] Dialog/modal açıldı.")

                dialog_input = None
                for get_dinput in [
                    lambda: dialog.first.locator('input[placeholder*="ara" i]').first,
                    lambda: dialog.first.locator('input[placeholder*="search" i]').first,
                    lambda: dialog.first.locator('input:not([type="hidden"])').first,
                    lambda: dialog.first.locator('vaadin-text-field input').first,
                ]:
                    try:
                        di = get_dinput()
                        if di.count() > 0 and di.is_visible():
                            dialog_input = di
                            break
                    except Exception:
                        continue

                if dialog_input:
                    print(f"  [ARAMA] Dialog içi arama alanı bulundu, yazılıyor...")
                    dialog_input.click()
                    human_delay(200, 400)
                    dialog_input.fill("")
                    page.keyboard.type(search_text[:20], delay=60)
                    human_delay(1000, 2000)
                    page.keyboard.press("Enter")
                    human_delay(1500, 3000)

                self._screenshot(page, "debug-dialog-search")
        except Exception:
            pass

        # ── Adım 4: Tüm alternatifleri topla, sonra ilkini seç ──
        alternatives = []
        selected = False
        first_item = None

        result_selectors = [
            'vaadin-grid-cell-content',
            'vaadin-grid vaadin-grid-cell-content',
            '[role="row"]',
            '[role="option"]',
            'tr',
            'vaadin-item',
            'vaadin-combo-box-item',
            'div[class*="result"]',
            'div[class*="item"]',
            'span[class*="item"]',
        ]

        containers = []
        if dialog_found:
            try:
                containers.append(page.locator("vaadin-dialog-overlay").first)
            except Exception:
                pass
        containers.append(page)

        search_lower = search_text.lower().strip()

        # Önce tüm eşleşen alternatifleri topla (tıklamadan)
        for container in containers:
            if alternatives:
                break
            for sel in result_selectors:
                if alternatives:
                    break
                try:
                    items = container.locator(sel).all()
                    for item in items:
                        try:
                            txt = (item.text_content() or "").strip()
                            if not txt or len(txt) < 3:
                                continue
                            txt_lower = txt.lower().strip()
                            if txt_lower.startswith(search_lower) or \
                               search_lower == txt_lower or \
                               search_lower in txt_lower or \
                               txt_lower.split(" - ")[0].strip() == search_lower:
                                alternatives.append(txt)
                                if first_item is None:
                                    first_item = item
                        except Exception:
                            continue
                except Exception:
                    continue

        if alternatives:
            print(f"  [ARAMA] {len(alternatives)} alternatif bulundu: {[a[:40] for a in alternatives]}")

        # İlk eşleşeni tıkla
        if first_item is not None:
            try:
                print(f"  [ARAMA] İlk alternatif seçiliyor: {alternatives[0][:60]}")
                first_item.click(timeout=5000)
                selected = True
                human_delay(500, 1000)
            except Exception as e:
                print(f"  [ARAMA] İlk alternatif tıklanamadı: {e}")

        if selected:
            print("  [ARAMA] Sonuç seçildi!")
            human_delay(1000, 2000)
            try:
                page.locator("vaadin-dialog-overlay").wait_for(
                    state="detached", timeout=5000
                )
            except Exception:
                pass
        else:
            print("  [ARAMA] Sonuçlardan eşleşen bulunamadı.")
            self._screenshot(page, "debug-no-match")

        return selected, alternatives

    # ── Scoped combo okuma helper ──

    def _read_combo_items(self, page, combo, max_items=50):
        """Vaadin combo-box'un seçeneklerini SCOPED olarak oku.

        Global `page.locator("vaadin-combo-box-item")` yerine combo'nun
        kendi filteredItems/items property'sini kullanır.
        """
        items_text = []
        try:
            # Combo'yu aç
            try:
                inp = combo.locator("input").first
                inp.click(timeout=3000)
            except Exception:
                combo.click(timeout=3000)
            human_delay(400, 700)

            # Strateji 1: JS filteredItems / items property
            try:
                items_text = combo.evaluate("""el => {
                    var items = el.filteredItems || el.items || [];
                    return items.slice(0, %d).map(function(i) {
                        if (typeof i === 'string') return i;
                        if (i && i.label) return i.label;
                        return String(i);
                    }).filter(function(t) { return t && t !== 'undefined' && t !== 'null'; });
                }""" % max_items)
            except Exception:
                items_text = []

            # Strateji 2: combo'nun overlay element'inden DOM okuma
            if not items_text:
                try:
                    items_text = combo.evaluate("""el => {
                        var ov = el._overlayElement || (el.$ && el.$.overlay);
                        if (!ov) {
                            var ovId = el.getAttribute('id');
                            if (ovId) ov = document.getElementById(ovId + '-overlay');
                        }
                        if (!ov) ov = document.querySelector('vaadin-combo-box-overlay[opened]');
                        if (!ov) return [];
                        var nodes = ov.querySelectorAll('vaadin-combo-box-item, [role="option"]');
                        var result = [];
                        for (var i = 0; i < nodes.length && i < %d; i++) {
                            var txt = (nodes[i].textContent || '').trim();
                            if (txt) result.push(txt);
                        }
                        return result;
                    }""" % max_items)
                except Exception:
                    items_text = []

            # Strateji 3 (son çare): global selector
            if not items_text:
                for sel in ['vaadin-combo-box-item', 'vaadin-combo-box-overlay [role="option"]']:
                    locator_items = page.locator(sel).all()
                    for item in locator_items[:max_items]:
                        try:
                            txt = (item.text_content() or "").strip()
                            if txt:
                                items_text.append(txt)
                        except Exception:
                            continue
                    if items_text:
                        break

        except Exception as e:
            print(f"  [READ-COMBO] Okuma hatası: {e}")
        finally:
            try:
                page.keyboard.press("Escape")
                human_delay(200, 400)
            except Exception:
                pass
        return items_text

    # ── Randevu tipi combo ──

    def _find_randevu_type_combo(self, page):
        """Randevu tipi combobox'u bul — pozisyon bazlı.

        Strateji: 'Randevu Alamadım' butonunun hemen üstündeki,
        değeri boş olan (veya 'internet' içeren) combo-box.
        Dolu olan combo'lar (hastane, bölüm, doktor) zaten değer içerir,
        randevu tipi combo'su henüz seçilmediği için boştur.

        Returns:
            combo element veya None
        """
        try:
            # Daha önce işaretledik mi?
            marked = page.locator('[data-hacbot-type-combo="true"]')
            if marked.count() > 0:
                return marked.first

            # JS ile pozisyon bazlı bul:
            # "Randevu Alamadım" butonunun hemen üstündeki combo
            found = page.evaluate("""() => {
                // Çapa: "Randevu Alamadım" butonu
                var anchorBtn = null;
                var buttons = document.querySelectorAll('vaadin-button, button');
                for (var i = 0; i < buttons.length; i++) {
                    var txt = (buttons[i].textContent || '').toLowerCase();
                    if (txt.indexOf('randevu') >= 0 && txt.indexOf('alamad') >= 0) {
                        anchorBtn = buttons[i];
                        break;
                    }
                }
                if (!anchorBtn) return -1;

                var anchorRect = anchorBtn.getBoundingClientRect();
                var combos = document.querySelectorAll('vaadin-combo-box');

                // Butonun üstündeki combo'ları mesafeye göre sırala
                var above = [];
                for (var j = 0; j < combos.length; j++) {
                    var combo = combos[j];
                    var rect = combo.getBoundingClientRect();
                    if (rect.width < 10 || rect.height < 5) continue;
                    var style = window.getComputedStyle(combo);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    // Combo butonun üstünde veya aynı hizada olmalı
                    if (rect.bottom > anchorRect.top + 10) continue;

                    var inp = combo.querySelector('input');
                    var val = inp ? (inp.value || '').trim() : '';

                    above.push({
                        index: j,
                        dist: anchorRect.top - rect.bottom,
                        value: val
                    });
                }

                if (above.length === 0) return -1;

                // En yakından en uzağa sırala
                above.sort(function(a, b) { return a.dist - b.dist; });

                // En yakın combo: değeri boş veya "internet" içeren
                // (Ekranda gördüğümüz gibi: dolu combo'lar hastane/bölüm/doktor,
                //  boş olan randevu tipi combo'su)
                for (var k = 0; k < above.length; k++) {
                    var c = above[k];
                    if (c.value === '' || c.value.toLowerCase().indexOf('internet') >= 0) {
                        combos[c.index].setAttribute('data-hacbot-type-combo', 'true');
                        return c.index;
                    }
                }

                // Hiçbiri boş değilse en yakını al (internet zaten seçilmiş olabilir)
                var nearest = above[0];
                combos[nearest.index].setAttribute('data-hacbot-type-combo', 'true');
                return nearest.index;
            }""")

            if found is not None and found >= 0:
                result = page.locator('[data-hacbot-type-combo="true"]').first
                if result.count() > 0:
                    try:
                        val = result.locator("input").first.input_value() or ""
                    except Exception:
                        val = ""
                    print(f"  [TYPE-COMBO] Randevu tipi combo bulundu (index {found}, değer: '{val}')")
                    return result

            print("  [TYPE-COMBO] Randevu tipi combo bulunamadı.")
        except Exception as e:
            print(f"  [TYPE-COMBO] Arama hatası: {e}")
        return None

    def _select_randevu_type(self, page, randevu_type):
        """Randevu tipi combobox'unu bul ve belirtilen tipi seç.

        Gerçek seçenekler: "İnternet Sonuç", "İnternetten Randevu"
        Kullanıcı config'den: "internet randevu" veya "internet sonuç"

        Eşleştirme: "randevu" kelimesi varsa → "İnternetten Randevu"
                    "sonuç"/"sonuc" kelimesi varsa → "İnternet Sonuç"

        Returns:
            bool — seçim başarılı mı
        """
        self._emit("selecting_type", f"[BILGI] Randevu tipi seçiliyor: {randevu_type}")

        combo = self._find_randevu_type_combo(page)
        if not combo:
            print("  [TYPE-COMBO] Randevu tipi combobox bulunamadı — bu adım atlanıyor.")
            return False

        # Hangi seçeneği arıyoruz? keyword belirle
        rt_lower = randevu_type.lower()
        if "sonuç" in rt_lower or "sonuc" in rt_lower:
            keyword = "sonuç"
        else:
            keyword = "randevu"

        try:
            # Scoped okuma ile seçenekleri öğren
            option_texts = self._read_combo_items(page, combo)
            print(f"  [TYPE-COMBO] Scoped seçenekler: {option_texts}")

            if not option_texts:
                print("  [TYPE-COMBO] Hiç seçenek okunamadı.")
                return False

            # Eşleşen seçenek metnini bul
            target_text = None
            for txt in option_texts:
                if keyword in txt.lower():
                    target_text = txt
                    break
            if not target_text:
                target_text = option_texts[0]
                print(f"  [TYPE-COMBO] Keyword eşleşmedi, ilk seçenek: '{target_text}'")

            # Combo'yu tekrar aç ve hedef seçeneği tıkla
            try:
                toggle = combo.locator('[part="toggle-button"], [slot="suffix"]').first
                if toggle.count() > 0:
                    toggle.click(timeout=3000)
                else:
                    combo.locator("input").first.click(timeout=3000)
            except Exception:
                combo.locator("input").first.click(timeout=3000)
            human_delay(500, 1000)

            selected = False
            for sel in ['vaadin-combo-box-item', 'vaadin-combo-box-overlay [role="option"]']:
                items = page.locator(sel).all()
                for item in items:
                    try:
                        txt = (item.text_content() or "").strip()
                        if txt == target_text:
                            item.click(timeout=5000)
                            human_delay(500, 800)
                            time.sleep(3)
                            self._emit("selecting_type", f"[BILGI] Randevu tipi seçildi: {txt}")
                            selected = True
                            break
                    except Exception:
                        continue
                if selected:
                    break

            if not selected:
                page.keyboard.press("Escape")
                human_delay(200, 400)
                print("  [TYPE-COMBO] Hedef seçenek tıklanamadı.")

            return selected
        except Exception as e:
            print(f"  [TYPE-COMBO] Seçim hatası: {e}")
            return False

    # ── Birim/Doktor combo (pozisyon bazlı) ──

    @staticmethod
    def _looks_like_date_options(texts):
        """Seçenek listesinin tarih (yıl/ay/gün) verisi olup olmadığını kontrol et.

        Returns:
            bool — True ise bu bir tarih combo'sudur
        """
        if not texts:
            return False
        sample = texts[:15]
        n = len(sample)

        year_count = sum(1 for t in sample if re.match(r"^\d{4}$", t.strip()))
        if year_count >= n * 0.4:
            return True

        day_count = sum(1 for t in sample
                        if re.match(r"^\d{1,2}$", t.strip()) and 1 <= int(t.strip()) <= 31)
        if day_count >= n * 0.4:
            return True

        month_count = sum(1 for t in sample if t.strip() in MONTHS_TR)
        if month_count >= n * 0.3:
            return True

        return False

    @staticmethod
    def _looks_like_internet_options(texts):
        """Seçenek listesinin internet randevu/sonuç combo'su olup olmadığını kontrol et."""
        if not texts:
            return False
        internet_count = sum(1 for t in texts[:10] if "internet" in t.lower())
        return internet_count >= 1

    def _find_unit_doctor_combo(self, page):
        """Birim/doktor combo-box'unu bul — aday eleme + açıp doğrulama.

        İki aşamalı:
        1. JS ile aday combo index listesi oluştur (label/değer bazlı ön eleme)
        2. Her adayı sırayla aç, ilk seçeneklere bak — yıl/ay/gün/internet ise reddet

        Returns:
            combo element veya None
        """
        try:
            # Daha önce doğrulanmış combo var mı?
            marked = page.locator('[data-hacbot-unit-combo="true"]')
            if marked.count() > 0:
                return marked.first

            # ── Aşama 1: JS ile aday indekslerini topla ──
            candidate_indices = page.evaluate("""() => {
                var combos = document.querySelectorAll('vaadin-combo-box');
                var indices = [];

                // "Randevu alamadım" butonunun Y koordinatı (çapa)
                var anchorY = null;
                var buttons = document.querySelectorAll('vaadin-button, button');
                for (var b = 0; b < buttons.length; b++) {
                    var btnText = (buttons[b].textContent || '').toLowerCase();
                    if (btnText.indexOf('randevu') >= 0 && btnText.indexOf('alamad') >= 0) {
                        anchorY = buttons[b].getBoundingClientRect().y;
                        break;
                    }
                }

                for (var i = 0; i < combos.length; i++) {
                    var combo = combos[i];

                    // Görünür olmayan combo'ları atla
                    var rect = combo.getBoundingClientRect();
                    if (rect.width < 10 || rect.height < 5) continue;
                    var style = window.getComputedStyle(combo);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;

                    // Zaten işaretlenmişse atla
                    if (combo.getAttribute('data-hacbot-type-combo')) continue;
                    if (combo.getAttribute('data-hacbot-date-combo')) continue;

                    var label = (combo.getAttribute('label') || '').toLowerCase();
                    var inp = combo.querySelector('input');
                    var val = inp ? (inp.value || '').toLowerCase().trim() : '';

                    // Label kesin tarih kelimesi içeriyorsa atla
                    var dateLabels = ['yıl', 'yil', 'gün', 'gun', 'year', 'month', 'day',
                                      'doğum', 'dogum', 'birth'];
                    if (dateLabels.some(function(kw) { return label.indexOf(kw) >= 0; })) continue;

                    // Mevcut değer kesin tarih verisi ise atla
                    var months = ['ocak', 'şubat', 'mart', 'nisan', 'mayıs', 'haziran',
                                  'temmuz', 'ağustos', 'eylül', 'ekim', 'kasım', 'aralık'];
                    if (/^\\d{4}$/.test(val)) continue;
                    if (/^\\d{1,2}$/.test(val) && parseInt(val) >= 1 && parseInt(val) <= 31) continue;
                    if (months.indexOf(val) >= 0) continue;
                    if (val.indexOf('internet') >= 0) continue;

                    // Sıralama için mesafe hesapla
                    var dist = anchorY !== null ? Math.abs(rect.y - anchorY) : i * 100;
                    indices.push({index: i, dist: dist});
                }

                // Çapaya en yakından en uzağa sırala
                indices.sort(function(a, b) { return a.dist - b.dist; });
                return indices.map(function(c) { return c.index; });
            }""")

            if not candidate_indices:
                print("  [UNIT-COMBO] Hiç aday combo bulunamadı (JS ön eleme).")
                return None

            print(f"  [UNIT-COMBO] {len(candidate_indices)} aday combo bulundu, doğrulanıyor...")

            # ── Aşama 2: Her adayı aç, seçeneklere bak, doğrula ──
            combos = page.locator("vaadin-combo-box")

            for idx in candidate_indices:
                combo = combos.nth(idx)
                try:
                    # Scoped okuma ile combo'nun gerçek seçeneklerini al
                    sample_texts = self._read_combo_items(page, combo, max_items=15)

                    # Doğrulama: tarih verisi mi?
                    if self._looks_like_date_options(sample_texts):
                        combo.evaluate('el => el.setAttribute("data-hacbot-date-combo", "true")')
                        print(f"  [UNIT-COMBO] Aday index {idx} tarih combo'su — atlanıyor (örnek: {sample_texts[:3]})")
                        continue

                    # Doğrulama: internet/tip combo'su mu?
                    if self._looks_like_internet_options(sample_texts):
                        combo.evaluate('el => el.setAttribute("data-hacbot-type-combo", "true")')
                        print(f"  [UNIT-COMBO] Aday index {idx} tip combo'su — atlanıyor")
                        continue

                    # Bu combo geçerli! İşaretle ve döndür.
                    combo.evaluate('el => el.setAttribute("data-hacbot-unit-combo", "true")')
                    print(f"  [UNIT-COMBO] Birim/doktor combo doğrulandı (index {idx}, "
                          f"{len(sample_texts)} seçenek, örnek: {[t[:25] for t in sample_texts[:3]]})")
                    return combo

                except Exception as e:
                    print(f"  [UNIT-COMBO] Aday index {idx} kontrol hatası: {e}")
                    continue

            print("  [UNIT-COMBO] Tüm adaylar elendi — birim/doktor combo bulunamadı.")
        except Exception as e:
            print(f"  [UNIT-COMBO] Arama hatası: {e}")
        return None

    def _get_unit_combo_options(self, page):
        """Birim/doktor combo-box'un tüm seçeneklerini topla (scoped).

        Returns:
            list[str] — seçenek isimleri
        """
        options = []
        try:
            combo = self._find_unit_doctor_combo(page)
            if not combo:
                return options

            raw_items = self._read_combo_items(page, combo, max_items=200)
            for txt in raw_items:
                if txt and len(txt) >= 3 and txt not in options:
                    options.append(txt)

            # Son güvenlik: seçenekler tarih verisine benziyorsa bu yanlış combo
            if self._looks_like_date_options(options):
                print(f"  [UNIT-COMBO] UYARI: Seçenekler tarih verisi! İşaret kaldırılıyor (örnek: {options[:5]})")
                combo.evaluate('el => { el.removeAttribute("data-hacbot-unit-combo"); '
                               'el.setAttribute("data-hacbot-date-combo", "true"); }')
                return []

            print(f"  [UNIT-COMBO] {len(options)} seçenek bulundu: {[o[:30] for o in options[:5]]}")
        except Exception as e:
            print(f"  [UNIT-COMBO] Seçenek toplama hatası: {e}")
        return options

    def _select_unit_combo_option(self, page, option_text):
        """Birim/doktor combo-box'ta belirtilen seçeneği seç.

        Returns:
            bool — seçim başarılı mı
        """
        try:
            combo = self._find_unit_doctor_combo(page)
            if not combo:
                return False

            inp = combo.locator("input").first
            inp.click(timeout=5000)
            human_delay(300, 600)
            page.keyboard.press(SELECT_ALL_KEY)
            page.keyboard.press("Backspace")
            human_delay(200, 400)

            page.keyboard.type(option_text[:15], delay=80)
            human_delay(1000, 2000)

            for sel in ['vaadin-combo-box-item', 'vaadin-combo-box-overlay [role="option"]']:
                items = page.locator(sel).all()
                for item in items:
                    try:
                        txt = (item.text_content() or "").strip()
                        if option_text.lower()[:15] in txt.lower():
                            item.click(timeout=5000)
                            human_delay(500, 800)
                            time.sleep(3)
                            return True
                    except Exception:
                        continue

            page.keyboard.press("Enter")
            human_delay(500, 800)
            time.sleep(3)
            return True
        except Exception as e:
            print(f"  [UNIT-COMBO] Seçenek seçme hatası ({option_text[:30]}): {e}")
            return False

    # ── Randevu çıkarma (metin bazlı) ──

    def _extract_appointments(self, page):
        """Sayfa DOM'undan randevu bilgilerini renk + metin bazlı çıkar.

        Hacettepe portalı renk kodlu grid kullanır:
          Yeşil → AÇIK, Kırmızı → DOLU, Gri → KAPALI,
          Mavi/Mor → WEB KAPASİTE DOLU, Teal → AÇILACAK

        Returns:
            dict: {available_slots: [{date, time, raw, status}], total_visible: int, has_availability: bool}
        """
        result = {"available_slots": [], "total_visible": 0, "has_availability": False}

        # Önce sayfadaki DOM yapısını anlamak için diagnostik
        try:
            diag = page.evaluate("""() => {
                var info = {};
                // Vaadin grid var mı?
                info.vaadinGridCount = document.querySelectorAll('vaadin-grid').length;
                info.vaadinGridCellCount = document.querySelectorAll('vaadin-grid-cell-content').length;
                info.tableCount = document.querySelectorAll('table').length;
                info.tdCount = document.querySelectorAll('td').length;

                // Sayfadaki tüm metin içinde saat deseni ara
                var bodyText = document.body ? document.body.innerText || '' : '';
                var timeMatches = bodyText.match(/\\d{1,2}[:.:]\\d{2}/g);
                info.timeMatchesInBody = timeMatches ? timeMatches.slice(0, 20) : [];

                // Shadow DOM'lardaki element sayıları
                var shadowHosts = document.querySelectorAll('*');
                var shadowCount = 0;
                for (var i = 0; i < shadowHosts.length; i++) {
                    if (shadowHosts[i].shadowRoot) shadowCount++;
                }
                info.shadowHostCount = shadowCount;

                // Vaadin grid shadow root içerikleri
                var grids = document.querySelectorAll('vaadin-grid');
                info.gridDetails = [];
                for (var g = 0; g < grids.length; g++) {
                    var grid = grids[g];
                    var detail = {tag: grid.tagName, childCount: grid.children.length};
                    if (grid.shadowRoot) {
                        detail.shadowChildCount = grid.shadowRoot.children.length;
                        detail.shadowHTML = grid.shadowRoot.innerHTML.substring(0, 500);
                    }
                    // Vaadin grid items
                    if (grid.items) {
                        detail.itemCount = grid.items.length;
                        detail.firstItems = grid.items.slice(0, 3).map(function(it) {
                            return JSON.stringify(it).substring(0, 200);
                        });
                    }
                    info.gridDetails.push(detail);
                }

                // div/span içinde saat deseni olan elementleri doğrudan say
                var allEls = document.querySelectorAll('div, span, td, th, a, button, p, label');
                var timeCells = [];
                var timeRe = /\d{1,2}[:.]\d{2}/;
                for (var j = 0; j < allEls.length && timeCells.length < 30; j++) {
                    var el = allEls[j];
                    var txt = el.textContent || '';
                    // Sadece kısa metinleri kontrol et (saat hücresi genelde kısadır)
                    if (txt.length < 30 && timeRe.test(txt)) {
                        var rect = el.getBoundingClientRect();
                        timeCells.push({
                            tag: el.tagName,
                            text: txt.trim().substring(0, 50),
                            class: (el.className || '').substring(0, 100),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height),
                            bg: window.getComputedStyle(el).backgroundColor
                        });
                    }
                }
                info.timeCellsFound = timeCells;

                return info;
            }""")
            print(f"  [APPOINTMENTS-DIAG] Vaadin grid: {diag.get('vaadinGridCount', 0)}, "
                  f"cells: {diag.get('vaadinGridCellCount', 0)}, "
                  f"tables: {diag.get('tableCount', 0)}, tds: {diag.get('tdCount', 0)}")
            print(f"  [APPOINTMENTS-DIAG] Shadow hosts: {diag.get('shadowHostCount', 0)}")
            print(f"  [APPOINTMENTS-DIAG] Body time matches: {diag.get('timeMatchesInBody', [])}")
            time_cells = diag.get('timeCellsFound', [])
            print(f"  [APPOINTMENTS-DIAG] Time cells found: {len(time_cells)}")
            for tc in time_cells[:10]:
                print(f"    {tc['tag']} text='{tc['text']}' class='{tc['class'][:50]}' "
                      f"size={tc['w']}x{tc['h']} bg={tc['bg']}")
            grid_details = diag.get('gridDetails', [])
            for gd in grid_details:
                print(f"  [APPOINTMENTS-DIAG] Grid: children={gd.get('childCount')}, "
                      f"shadow={gd.get('shadowChildCount', 'N/A')}, "
                      f"items={gd.get('itemCount', 'N/A')}")
                if gd.get('firstItems'):
                    for fi in gd['firstItems']:
                        print(f"    Item: {fi[:150]}")
        except Exception as e:
            print(f"  [APPOINTMENTS-DIAG] Diagnostik hatası: {e}")

        try:
            appt_data = page.evaluate("""() => {
                var data = {available_slots: [], total_visible: 0, all_slots: [], debug: []};
                var timeRe = /(\d{1,2})[:.:](\d{2})/;

                function classifyColor(bgColor, el) {
                    var cls = (el.className || '').toLowerCase();
                    if (/green|available|acik|açık|success|musait/.test(cls)) return 'açık';
                    if (/red|full|dolu|danger|occupied/.test(cls)) return 'dolu';
                    if (/gr[ae]y|closed|kapal|disabled/.test(cls)) return 'kapalı';
                    if (/blue|purple|capacity|kapasite/.test(cls)) return 'web_kapasite_dolu';
                    if (/teal|cyan|acilacak/.test(cls)) return 'açılacak';

                    var m = bgColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
                    if (!m) return 'bilinmiyor';
                    var r = parseInt(m[1]), g = parseInt(m[2]), b = parseInt(m[3]);
                    var maxC = Math.max(r, g, b), minC = Math.min(r, g, b);

                    if (minC > 230) return 'bos';
                    if (maxC - minC < 30 && maxC < 200 && maxC > 50) return 'kapalı';
                    if (g > r + 30 && g > b + 30) return 'açık';
                    if (r > g + 30 && r > b + 30) return 'dolu';
                    if (b > r + 20 && b > g + 20) return 'web_kapasite_dolu';
                    if (g > r + 20 && b > r + 20 && Math.abs(g - b) < 60) return 'açılacak';
                    return 'bilinmiyor';
                }

                var monthMap = {'oca':'01','şub':'02','sub':'02','mar':'03','nis':'04',
                    'may':'05','haz':'06','tem':'07','ağu':'08','agu':'08',
                    'eyl':'09','eki':'10','kas':'11','ara':'12'};
                var curYear = new Date().getFullYear();

                function parseTrDate(text) {
                    var m = text.trim().match(/(\d{1,2})\s+([a-zçğıöşüA-ZÇĞİÖŞÜ]+)/i);
                    if (m) {
                        var day = m[1].padStart(2,'0');
                        var mon = monthMap[m[2].substring(0,3).toLowerCase()];
                        if (mon) return day + '.' + mon + '.' + curYear;
                    }
                    return '';
                }

                // Shadow DOM dahil tüm elementleri topla
                function collectAllElements(root, result) {
                    if (!root) return;
                    var children = root.children || root.childNodes || [];
                    for (var i = 0; i < children.length; i++) {
                        var child = children[i];
                        if (child.nodeType === 1) {
                            result.push(child);
                            // Shadow root varsa içine dal
                            if (child.shadowRoot) {
                                collectAllElements(child.shadowRoot, result);
                            }
                            collectAllElements(child, result);
                        }
                    }
                }

                // Tarih başlıklarını topla (pozisyon bazlı eşleştirme için)
                var dateHeaders = [];
                var allEls = [];
                collectAllElements(document.body, allEls);

                var dateHdrRe = /^\s*\d{1,2}\s+[A-Za-zçğıöşüÇĞİÖŞÜ]+\s*$/;
                for (var h = 0; h < allEls.length; h++) {
                    var htxt = (allEls[h].textContent || '').trim();
                    if (dateHdrRe.test(htxt) && htxt.length < 30) {
                        var hrect = allEls[h].getBoundingClientRect();
                        if (hrect.width > 0 && hrect.height > 0) {
                            var pd = parseTrDate(htxt);
                            if (pd) {
                                dateHeaders.push({
                                    date: pd,
                                    centerX: hrect.left + hrect.width / 2,
                                    text: htxt
                                });
                            }
                        }
                    }
                }
                data.debug.push('dateHeaders: ' + dateHeaders.length + ' found');

                // Saat hücrelerini tara — shadow DOM dahil
                var visited = {};
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    // Sadece yaprak veya kısa metinli elementler
                    var text = '';
                    // innerText yerine textContent - ama sadece kısa olanlar
                    var rawText = el.textContent || '';
                    if (rawText.length > 40) continue;
                    text = rawText.trim();
                    if (!text) continue;

                    var tm = text.match(timeRe);
                    if (!tm) continue;

                    var rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    var posKey = Math.round(rect.left) + ',' + Math.round(rect.top);
                    if (visited[posKey]) continue;
                    visited[posKey] = true;

                    var time = tm[1].padStart(2,'0') + ':' + tm[2];

                    // Renk analizi: element ve parent'ları tara
                    var bg = '';
                    var checkEl = el;
                    var depth = 0;
                    while (depth < 6) {
                        if (!checkEl) break;
                        var style = window.getComputedStyle(checkEl);
                        bg = style.backgroundColor;
                        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') break;
                        // Shadow host'a da bak
                        checkEl = checkEl.parentElement || (checkEl.getRootNode && checkEl.getRootNode().host);
                        depth++;
                    }

                    var status = classifyColor(bg || '', el);
                    if (status === 'bos') continue;

                    // Tarih eşleştirme: X konumuna en yakın tarih başlığı
                    var date = '';
                    var cellCX = rect.left + rect.width / 2;
                    var minDist = Infinity;
                    for (var d = 0; d < dateHeaders.length; d++) {
                        var dist = Math.abs(cellCX - dateHeaders[d].centerX);
                        if (dist < minDist) { minDist = dist; date = dateHeaders[d].date; }
                    }

                    data.total_visible++;
                    var slotInfo = {date: date, time: time,
                        raw: (date ? date + ' ' : '') + time + ' [' + status + ']',
                        status: status};
                    data.all_slots.push(slotInfo);
                    if (status === 'açık') data.available_slots.push(slotInfo);
                }

                data.debug.push('total_visible: ' + data.total_visible);
                return data;
            }""")

            if appt_data:
                result["available_slots"] = appt_data.get("available_slots", [])
                result["total_visible"] = appt_data.get("total_visible", 0)
                result["has_availability"] = len(result["available_slots"]) > 0

                debug_msgs = appt_data.get("debug", [])
                for dm in debug_msgs:
                    print(f"  [APPOINTMENTS-JS] {dm}")

                all_slots = appt_data.get("all_slots", [])
                if all_slots:
                    status_counts = {}
                    for s in all_slots:
                        st = s.get("status", "?")
                        status_counts[st] = status_counts.get(st, 0) + 1
                    print(f"  [APPOINTMENTS] Toplam: {result['total_visible']}, "
                          f"Müsait: {len(result['available_slots'])}, "
                          f"Dağılım: {status_counts}")
                else:
                    print(f"  [APPOINTMENTS] Hiç slot bulunamadı (total_visible=0)")

        except Exception as e:
            print(f"  [APPOINTMENTS] Çıkarma hatası: {e}")
            import traceback
            traceback.print_exc()

        return result

    def _classify_appointments(self, page, appt_info):
        """Randevu bilgisine göre durum sınıflandır."""
        if appt_info.get("has_availability"):
            return "AVAILABLE"
        if appt_info.get("total_visible", 0) > 0:
            return "NOT_AVAILABLE"

        try:
            body = re.sub(r"\s+", " ", page.locator("body").inner_text()).strip()
        except Exception:
            body = ""

        neg = any(p.search(body) for p in NEGATIVE_PATTERNS)
        if neg:
            return "NOT_AVAILABLE"
        pos = any(p.search(body) for p in POSITIVE_PATTERNS)
        if pos:
            return "POSSIBLY_AVAILABLE"
        return "UNKNOWN"

    def _format_slots(self, available_slots):
        """Slot listesini okunabilir metne çevirir.

        Returns:
            str — "15.03.2026: 10:30, 12:00 | 16.03.2026: 09:00"
        """
        if not available_slots:
            return "Müsait slot yok"

        # Tarihe göre grupla
        by_date = {}
        for slot in available_slots:
            date = slot.get("date", "") or "Tarih belirsiz"
            time_str = slot.get("time", "") or slot.get("raw", "?")
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(time_str)

        parts = []
        for date, times in by_date.items():
            parts.append(f"{date}: {', '.join(times)}")

        return " | ".join(parts)

    def _is_date_combo(self, page, combo):
        """Combo-box'un doğum tarihi combo'su (Yıl/Ay/Gün) olup olmadığını kontrol et."""
        try:
            # Label kontrolü
            label = (combo.get_attribute("label") or "").lower()
            if any(kw in label for kw in ["yıl", "yil", "ay", "gün", "gun", "year", "month", "day"]):
                return True

            # Mevcut değer kontrolü
            inp = combo.locator("input").first
            val = (inp.input_value() or "").strip()

            # Değer 4 haneli yıl mı?
            if re.match(r"^\d{4}$", val):
                return True
            # Değer 1-2 haneli gün mü?
            if re.match(r"^\d{1,2}$", val) and 1 <= int(val) <= 31:
                return True
            # Değer Türkçe ay adı mı?
            if val in MONTHS_TR:
                return True

            # Değer boşsa seçeneklere bakarak karar ver
            if not val:
                try:
                    inp.click(timeout=3000)
                    human_delay(300, 500)
                    items = page.locator("vaadin-combo-box-item").all()
                    sample_texts = []
                    for item in items[:10]:
                        try:
                            txt = (item.text_content() or "").strip()
                            if txt:
                                sample_texts.append(txt)
                        except Exception:
                            continue
                    page.keyboard.press("Escape")
                    human_delay(200, 400)

                    if sample_texts:
                        # Çoğu 4 haneli yılsa → tarih combo'su
                        year_count = sum(1 for t in sample_texts if re.match(r"^\d{4}$", t))
                        if year_count >= len(sample_texts) * 0.5:
                            return True
                        # Çoğu 1-31 arası sayıysa → gün combo'su
                        day_count = sum(1 for t in sample_texts if re.match(r"^\d{1,2}$", t) and 1 <= int(t) <= 31)
                        if day_count >= len(sample_texts) * 0.5:
                            return True
                        # Çoğu Türkçe ay adıysa → ay combo'su
                        month_count = sum(1 for t in sample_texts if t in MONTHS_TR)
                        if month_count >= len(sample_texts) * 0.5:
                            return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def _find_doctor_combo(self, page):
        """Doğum tarihi combo'larını atlayıp doktor/birim combo-box'unu bul.

        Returns:
            combo element veya None
        """
        combos = page.locator("vaadin-combo-box:visible")
        count = combos.count()
        if count == 0:
            print("  [COMBO] Sayfada combo-box bulunamadı.")
            return None

        for i in range(count):
            combo = combos.nth(i)
            if self._is_date_combo(page, combo):
                continue
            print(f"  [COMBO] Doktor/birim combo-box bulundu (index {i}/{count}).")
            return combo

        print(f"  [COMBO] {count} combo-box bulundu ama hepsi tarih combo'su.")
        return None

    def _get_combo_options(self, page):
        """Randevu sayfasındaki doktor/birim combo-box'un tüm seçeneklerini topla.

        Returns:
            list[str] — seçenek isimleri
        """
        options = []
        try:
            combo = self._find_doctor_combo(page)
            if not combo:
                return options

            inp = combo.locator("input").first

            # Combo-box'u aç
            inp.click(timeout=5000)
            human_delay(500, 1000)

            # Tüm seçenekleri topla
            for sel in ['vaadin-combo-box-item', 'vaadin-combo-box-overlay [role="option"]']:
                items = page.locator(sel).all()
                for item in items:
                    try:
                        txt = (item.text_content() or "").strip()
                        if txt and len(txt) >= 3 and txt not in options:
                            options.append(txt)
                    except Exception:
                        continue
                if options:
                    break

            # Dropdown'u kapat (Escape)
            page.keyboard.press("Escape")
            human_delay(300, 500)

            print(f"  [COMBO] {len(options)} seçenek bulundu: {[o[:30] for o in options[:5]]}")
        except Exception as e:
            print(f"  [COMBO] Seçenek toplama hatası: {e}")

        return options

    def _select_combo_option(self, page, option_text):
        """Randevu sayfasındaki doktor/birim combo-box'ta belirtilen seçeneği seç.

        Returns:
            bool — seçim başarılı mı
        """
        try:
            combo = self._find_doctor_combo(page)
            if not combo:
                return False

            inp = combo.locator("input").first

            # Combo-box'a tıkla ve temizle
            inp.click(timeout=5000)
            human_delay(300, 600)
            page.keyboard.press(SELECT_ALL_KEY)
            page.keyboard.press("Backspace")
            human_delay(200, 400)

            # Seçenek metninin bir kısmını yaz (filtre tetiklenir)
            page.keyboard.type(option_text[:15], delay=80)
            human_delay(1000, 2000)

            # Overlay'den eşleşen seçeneği tıkla
            for sel in ['vaadin-combo-box-item', 'vaadin-combo-box-overlay [role="option"]']:
                items = page.locator(sel).all()
                for item in items:
                    try:
                        txt = (item.text_content() or "").strip()
                        if option_text.lower()[:15] in txt.lower():
                            item.click(timeout=5000)
                            human_delay(500, 800)
                            # Vaadin server round-trip bekle
                            time.sleep(3)
                            return True
                    except Exception:
                        continue

            # Fallback: Enter ile ilk sonucu al
            page.keyboard.press("Enter")
            human_delay(500, 800)
            time.sleep(3)
            return True
        except Exception as e:
            print(f"  [COMBO] Seçenek seçme hatası ({option_text[:30]}): {e}")
            return False

    def _classify_slots(self, page, slot_info):
        """Slot bilgisine ve sayfa metnine göre durum sınıflandır."""
        if slot_info["green"] > 0:
            return "AVAILABLE"
        try:
            body = re.sub(r"\s+", " ", page.locator("body").inner_text()).strip()
        except Exception:
            body = ""
        neg = any(p.search(body) for p in NEGATIVE_PATTERNS)
        pos = any(p.search(body) for p in POSITIVE_PATTERNS)
        if neg:
            return "NOT_AVAILABLE"
        if pos:
            return "POSSIBLY_AVAILABLE"
        return "UNKNOWN"

    def _analyze_slots(self, page):
        """Randevu slotlarını renk kodlarına göre analiz et.
        Yeşil=müsait, Kırmızı=dolu, Gri=kapalı.
        """
        result = {"green": 0, "red": 0, "grey": 0, "total": 0, "details": []}

        # Sayfa'daki tüm slot elementlerini tara
        # Vaadin grid veya tablo hücreleri olabilir
        try:
            slot_data = page.evaluate("""() => {
                const slots = {green: 0, red: 0, grey: 0, total: 0, details: []};

                // Strateji 1: background-color veya color CSS'i olan hücreleri tara
                const allElements = document.querySelectorAll(
                    'td, th, div, span, button, vaadin-grid-cell-content, ' +
                    '[class*="slot"], [class*="randevu"], [class*="saat"], ' +
                    '[class*="available"], [class*="full"], [class*="closed"], ' +
                    '[class*="green"], [class*="red"], [class*="grey"], [class*="gray"], ' +
                    '[style*="background"], [style*="color"]'
                );

                for (const el of allElements) {
                    const style = window.getComputedStyle(el);
                    const bg = style.backgroundColor;
                    const text = (el.textContent || '').trim();

                    // Saat formatı içeren elementleri filtrele (08:00, 09:30 vb.)
                    const hasTime = /\\d{1,2}[:.]\\d{2}/.test(text);
                    const cls = (el.className || '').toLowerCase();

                    // Renk sınıflandırması
                    const isGreen = bg.includes('0, 128') || bg.includes('0, 100') ||
                                    bg.includes('76, 175') || bg.includes('56, 142') ||
                                    bg.includes('40, 167') || bg.includes('46, 125') ||
                                    bg.includes('34, 139') || bg.includes('0, 150') ||
                                    bg.includes('102, 187') || bg.includes('139, 195') ||
                                    bg.includes('22, 136, 70') ||  // Hacettepe yeşil
                                    bg.includes('16, 124') || bg.includes('30, 130') ||
                                    cls.includes('green') || cls.includes('available') ||
                                    cls.includes('musait') || cls.includes('açık') ||
                                    cls.includes('acik') || cls.includes('success');

                    const isRed = bg.includes('255, 0') || bg.includes('244, 67') ||
                                  bg.includes('229, 57') || bg.includes('211, 47') ||
                                  bg.includes('183, 28') || bg.includes('255, 82') ||
                                  bg.includes('198, 40') || bg.includes('176, 0') ||
                                  cls.includes('red') || cls.includes('full') ||
                                  cls.includes('dolu') || cls.includes('danger') ||
                                  cls.includes('occupied');

                    const isGrey = bg.includes('128, 128, 128') || bg.includes('158, 158') ||
                                   bg.includes('189, 189') || bg.includes('117, 117') ||
                                   bg.includes('96, 96') || bg.includes('150, 150') ||
                                   bg.includes('169, 169') || bg.includes('192, 192') ||
                                   bg.includes('28, 55, 90') ||  // Hacettepe mavi-gri (dolu slot)
                                   cls.includes('grey') || cls.includes('gray') ||
                                   cls.includes('closed') || cls.includes('kapali') ||
                                   cls.includes('disabled') || cls.includes('inactive');

                    if (hasTime || isGreen || isRed || isGrey) {
                        if (isGreen) {
                            slots.green++;
                            slots.total++;
                            slots.details.push({time: text.substring(0, 50), color: 'green', bg: bg});
                        } else if (isRed) {
                            slots.red++;
                            slots.total++;
                            slots.details.push({time: text.substring(0, 50), color: 'red', bg: bg});
                        } else if (isGrey) {
                            slots.grey++;
                            slots.total++;
                            slots.details.push({time: text.substring(0, 50), color: 'grey', bg: bg});
                        } else if (hasTime) {
                            // Saat var ama renk belirsiz — ekle
                            slots.total++;
                            slots.details.push({time: text.substring(0, 50), color: 'unknown', bg: bg});
                        }
                    }
                }
                return slots;
            }""")
            if slot_data:
                result = slot_data
                # Details'i kısalt (max 20)
                if len(result.get("details", [])) > 20:
                    result["details"] = result["details"][:20]
        except Exception as e:
            print(f"  [DEBUG] Slot analizi hatası: {e}")

        return result

    def _flow(self, page) -> int:
        cfg = self._cfg
        # ── Google ziyareti: reCAPTCHA güven cookieleri oluştur ──
        try:
            self._emit("google_visit", "[BILGI] Google ziyareti (reCAPTCHA güven oluşturma)...")
            page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=15000)
            time.sleep(random.uniform(1.5, 3.0))
            simulate_human(page, extensive=True)
            time.sleep(random.uniform(1.0, 2.0))
            page.goto(cfg["target_url"], wait_until="networkidle", timeout=30000)
            time.sleep(2)
        except Exception as e:
            self._emit("google_visit", f"[UYARI] Google pre-visit hatası: {e}")
            try:
                page.goto(cfg["target_url"], wait_until="networkidle", timeout=30000)
                time.sleep(2)
            except Exception:
                pass

        # ── İnsan davranışı ──
        simulate_human(page, extensive=True)
        time.sleep(random.uniform(1.0, 2.0))

        # ── TC ──
        self._emit("fill_tc", "[BILGI] TC Kimlik No dolduruluyor...")
        tc_ok = fill_first(page, [
            page.get_by_label(re.compile(r"(t\.?c\.?|tc).*kimlik", re.I)),
            page.locator('input[name*="tc" i], input[id*="tc" i]'),
            page.locator('input[placeholder*="T.C" i], input[placeholder*="Kimlik" i]'),
            page.get_by_role("textbox", name=re.compile(r"(t\.?c\.?|tc).*kimlik", re.I)),
        ], cfg["tc"])

        # TC alanında change event tetikle (Vaadin sunucuya değeri göndersin)
        try:
            page.evaluate("""() => {
                var inputs = document.querySelectorAll('input');
                inputs.forEach(function(inp) {
                    if (inp.value && inp.value.length >= 11) {
                        inp.dispatchEvent(new Event('change', {bubbles: true}));
                        inp.dispatchEvent(new Event('blur', {bubbles: true}));
                    }
                });
            }""")
        except Exception:
            pass

        simulate_human(page)
        human_delay(300, 700)

        # ── Doğum tarihi ──
        self._emit("fill_birth", "[BILGI] Doğum tarihi dolduruluyor...")
        bd_ok = fill_first(page, [
            page.get_by_label(re.compile(r"doğum\s*tarihi", re.I)),
            page.locator('input[name*="dog" i], input[id*="dog" i], input[name*="birth" i], input[id*="birth" i]'),
            page.locator('input[placeholder*="Doğum" i], input[placeholder*="gg" i]'),
            page.get_by_role("textbox", name=re.compile(r"doğum\s*tarihi", re.I)),
        ], cfg["birth_date"])
        if not bd_ok:
            bd_ok = fill_birth_combos(page, cfg["birth_date"])

        if not tc_ok or not bd_ok:
            self._screenshot(page, "debug-fill-failed")
            raise RuntimeError("TC veya doğum tarihi alanı bulunamadı.")
        self._emit("fill_done", "[BILGI] Form alanları dolduruldu.")
        self._screenshot(page, "debug-after-fill")
        human_delay(300, 600)

        # ── KVKK ──
        ensure_kvkk(page)
        human_delay(300, 600)

        # ── reCAPTCHA ──
        simulate_human(page, extensive=False)
        human_delay(300, 800)

        self._emit("recaptcha", "[BILGI] reCAPTCHA işleniyor...")
        rc_ok = handle_recaptcha(
            page, cfg["recaptcha_timeout_ms"], cfg["headless"], cfg["recaptcha_max_retries"],
            captcha_api_key=cfg.get("captcha_api_key", "")
        )

        if SETUP_MODE:
            if rc_ok:
                print("\n[SETUP] reCAPTCHA çözüldü! Profil güveni kaydedildi.")
            else:
                print("\n[SETUP] reCAPTCHA çözülemedi. Tekrar deneyin.")
            return 0 if rc_ok else 1

        if not rc_ok:
            raise RecaptchaFailed("reCAPTCHA çözülemedi")

        self._screenshot(page, "debug-after-recaptcha")

        # Token expire olmasın diye hızlı devam et
        time.sleep(random.uniform(0.5, 1.0))

        # Giriş'ten önce grecaptcha.getResponse override'ı tazele
        try:
            _run_in_main_world(page, """(function(){
                var ta = document.querySelector('textarea[name="g-recaptcha-response"]');
                var token = ta ? ta.value : '';
                if (token && typeof grecaptcha !== 'undefined') {
                    grecaptcha.getResponse = function(){ return token; };
                }
            })();""")
        except Exception:
            pass

        # ── Form gönder ──
        self._emit("submit", "[BILGI] Form gönderiliyor...")
        submitted = False
        # Strateji 1: Vaadin buton tıkla (force=True ile overlay bypass)
        for get_btn in [
            lambda: page.locator("vaadin-button").filter(has_text=re.compile(r"giriş", re.I)).first,
            lambda: page.get_by_role("button", name=re.compile(r"giriş", re.I)).first,
        ]:
            try:
                btn = get_btn()
                if btn.count() > 0:
                    btn.click(timeout=5000, force=True)
                    submitted = True
                    break
            except Exception:
                continue
        # Strateji 2: click_by_text fallback
        if not submitted:
            submitted = click_by_text(page, re.compile(r"(devam|sorgula|giriş|ileri|randevu\s*ara)", re.I))
        # Strateji 3: Enter tuşu
        if not submitted:
            try:
                page.keyboard.press("Enter")
                submitted = True
                print("[BILGI] Enter tuşu ile gönderildi.")
            except Exception:
                pass
        self._emit("submit", f"[BILGI] Giriş butonu tıklandı: {submitted}")
        time.sleep(4)  # Vaadin server round-trip için yeterli
        self._screenshot(page, "debug-after-giris")

        # Vaadin notification kontrolü (sunucu hata mesajı)
        try:
            notif_cards = page.locator("vaadin-notification-card")
            if notif_cards.count() > 0:
                notif_text = notif_cards.first.text_content() or ""
                print(f"[BILGI] Sunucu bildirimi: {notif_text[:200]}")
        except Exception:
            pass

        # ── Login başarılı mı kontrol et ──
        login_ok = False

        # Önce bilgi tamamlama dialogunu kontrol et — varsa login başarılı demektir
        try:
            dialog = page.locator("vaadin-dialog-overlay")
            if dialog.count() > 0:
                dialog_text = (dialog.first.text_content() or "").lower()
                if "eksik" in dialog_text or "bilgi" in dialog_text or "onayla" in dialog_text:
                    print("[BILGI] Bilgi tamamlama dialogu bulundu — login başarılı!")
                    login_ok = True
        except Exception:
            pass

        # Post-login göstergeler
        if not login_ok:
            try:
                post_login = page.locator("button, vaadin-button, a").filter(
                    has_text=re.compile(r"güvenli|randevularım|çıkış", re.I)
                )
                if post_login.count() > 0:
                    login_ok = True
                    print("[BILGI] Login başarılı — post-login göstergeler bulundu!")
            except Exception:
                pass

        # URL değişmiş olabilir — login sayfasından farklıysa başarılı
        if not login_ok:
            try:
                current_url = page.url
                if "public/main" not in current_url.lower() or "user=PUBLIC" not in current_url:
                    login_ok = True
                    print(f"[BILGI] URL değişti — login başarılı: {current_url[:80]}")
            except Exception:
                pass

        if not login_ok:
            # reCAPTCHA iframe hâlâ varsa login sayfasındayız
            try:
                rc = page.locator('iframe[src*="recaptcha" i]')
                if rc.count() > 0:
                    try:
                        body_text = page.locator("body").inner_text()[:500]
                        for line in body_text.split("\n"):
                            line = line.strip()
                            if not line or len(line) < 5:
                                continue
                            if line in ("Giriş", "T.C. Kimlik", "Pasaport No", "Yıl", "Ay", "Gün"):
                                continue
                            if "sisteme" in line.lower() or "hata" in line.lower() or \
                               "doğrulama" in line.lower() or "gerekli" in line.lower():
                                print(f"[BILGI] Hata mesajı: {line[:200]}")
                                break
                    except Exception:
                        pass
                    print("[UYARI] Hâlâ login sayfasında — reCAPTCHA/giriş başarısız.")
                    self._screenshot(page, "login-failed")
                    raise RecaptchaFailed("Login sayfasından çıkılamadı")
            except RecaptchaFailed:
                raise
            except Exception:
                pass

        # ── Bilgi tamamlama dialogu ──
        handle_info_dialog(page, cfg["phone"], cfg["email"])

        # ── Arama ile doktor/birim bulma ──
        search_text = cfg["doctor"] or cfg["clinic"] or ""
        randevu_type = cfg.get("randevu_type", "internet randevu")
        all_results = []
        alternatives = []

        if search_text:
            self._emit("search", f"[BILGI] Arama yapılıyor: {search_text}")
            selected, alternatives = self._search_and_select_first(page, search_text)
            self._emit("search", f"[BILGI] Arama: {'başarılı' if selected else 'başarısız'}")
            time.sleep(random.uniform(2.0, 4.0))

        # ── Randevu tipi seçimi (YENİ ADIM) ──
        self._select_randevu_type(page, randevu_type)
        time.sleep(3)

        # Grid yüklenmesini bekle — Vaadin bazen yavaş olabiliyor
        for wait_attempt in range(5):
            has_grid = page.evaluate("""() => {
                // Body'de saat deseni var mı kontrol et
                var bodyText = document.body ? document.body.innerText || '' : '';
                var hasTime = /\\d{1,2}[:.:]\\d{2}/.test(bodyText);
                // Vaadin grid var mı
                var hasGrid = document.querySelectorAll('vaadin-grid').length > 0;
                // Tablo var mı
                var hasTable = document.querySelectorAll('table').length > 0;
                return {hasTime: hasTime, hasGrid: hasGrid, hasTable: hasTable};
            }""")
            print(f"  [GRID-WAIT] Deneme {wait_attempt+1}: time={has_grid.get('hasTime')}, "
                  f"grid={has_grid.get('hasGrid')}, table={has_grid.get('hasTable')}")
            if has_grid.get('hasTime') or has_grid.get('hasGrid') or has_grid.get('hasTable'):
                break
            time.sleep(2)
        else:
            print("  [GRID-WAIT] 10 saniye bekledik ama grid/saat bulunamadı.")

        time.sleep(1)
        self._screenshot(page, "debug-after-type-select")

        # ── İlk seçimin randevu analizi ──
        self._emit("analyzing", "[BILGI] Randevular analiz ediliyor...")
        ts = datetime.now().isoformat()
        appt_info = self._extract_appointments(page)
        self._screenshot(page, "debug-after-search")

        first_name = search_text
        if search_text and alternatives:
            first_name = alternatives[0]

        first_status = self._classify_appointments(page, appt_info)
        first_result = {
            "name": first_name,
            "appointments": appt_info,
            "status": first_status,
            "formatted": self._format_slots(appt_info["available_slots"]),
        }
        all_results.append(first_result)

        if appt_info["has_availability"]:
            detail = self._format_slots(appt_info["available_slots"])
            self._emit("available", f"[BILGI] MÜSAİT: {first_name} — {detail}")

        self._screenshot(page, f"slot-{first_name[:20].replace(' ', '_')}")

        # ── Birim/Doktor combo'daki TÜM seçenekleri tara ──
        if search_text:
            combo_options = self._get_unit_combo_options(page)
            scanned_names = {first_name.lower().strip()}

            for opt in combo_options:
                opt_lower = opt.lower().strip()
                if opt_lower in scanned_names:
                    continue
                scanned_names.add(opt_lower)

                self._emit("scanning", f"[BILGI] Taranıyor: {opt}")
                ok = self._select_unit_combo_option(page, opt)
                if not ok:
                    self._emit("scanning", f"[UYARI] Seçilemedi: {opt}")
                    continue

                opt_appt = self._extract_appointments(page)
                opt_status = self._classify_appointments(page, opt_appt)
                opt_result = {
                    "name": opt,
                    "appointments": opt_appt,
                    "status": opt_status,
                    "formatted": self._format_slots(opt_appt["available_slots"]),
                }
                all_results.append(opt_result)

                if opt_appt["has_availability"]:
                    detail = self._format_slots(opt_appt["available_slots"])
                    self._emit("available", f"[BILGI] MÜSAİT: {opt} — {detail}")

                self._screenshot(page, f"slot-{opt[:20].replace(' ', '_')}")

        # ── Toplam özet hesapla ──
        overall_status = "NOT_AVAILABLE"
        total_available = 0
        total_visible = 0
        for r in all_results:
            a = r["appointments"]
            total_available += len(a.get("available_slots", []))
            total_visible += a.get("total_visible", 0)
            if r["status"] in ("AVAILABLE", "POSSIBLY_AVAILABLE"):
                overall_status = "AVAILABLE"

        if overall_status != "AVAILABLE" and not all_results:
            overall_status = "UNKNOWN"

        self.result = {
            "timestamp": ts, "status": overall_status, "url": page.url,
            "total_available": total_available,
            "total_visible": total_visible,
            "alternatives": all_results,
        }

        (ARTIFACTS_DIR / "last-result.json").write_text(
            json.dumps(self.result, indent=2, ensure_ascii=False) + "\n"
        )
        if cfg["save_screenshot"]:
            self._screenshot(page, "last-check")

        self._emit("result", f"[{ts}] {len(all_results)} alternatif tarandı. Toplam görünen: {total_visible}, müsait: {total_available}")
        if overall_status == "AVAILABLE":
            avail_names = [r["name"] for r in all_results if r["status"] == "AVAILABLE"]
            avail_details = []
            for r in all_results:
                if r["status"] == "AVAILABLE":
                    avail_details.append(f"{r['name']}: {r['formatted']}")
            self._emit("result", f"[{ts}] *** MÜSAİT RANDEVU: {'; '.join(avail_details)} ***")
            return 0
        if overall_status == "NOT_AVAILABLE":
            self._emit("result", f"[{ts}] Hiçbir alternatifde uygun randevu bulunamadı.")
            return 2
        self._emit("result", f"[{ts}] Durum belirsiz → artifacts/last-check.png")
        return 3

    def run(self) -> int:
        interval = self._cfg["check_interval_minutes"]
        if interval > 0:
            print(f"[BILGI] Sürekli izleme: her {interval} dk.")
            while True:
                try:
                    code = self.run_once()
                    if code == 0:
                        print("[BILGI] Randevu bulundu! Döngü durduruluyor.")
                        return 0
                except Exception as e:
                    print(f"[HATA] {e}")
                time.sleep(interval * 60)
        return self.run_once()


if __name__ == "__main__":
    _validate_env()
    sys.exit(HacettepeBot().run())
