import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import dotenv from 'dotenv';
import { chromium } from 'playwright';

dotenv.config();

const REQUIRED_ENV = ['TC_KIMLIK_NO', 'DOGUM_TARIHI'];
for (const key of REQUIRED_ENV) {
  if (!process.env[key]) {
    console.error(`[HATA] Eksik ortam değişkeni: ${key}`);
    process.exit(1);
  }
}

const CFG = {
  targetUrl:
    process.env.TARGET_URL ||
    'https://hastanerandevu.hacettepe.edu.tr/nucleus-hastaportal-randevu/public/main?user=PUBLIC',
  tc: process.env.TC_KIMLIK_NO,
  birthDate: process.env.DOGUM_TARIHI,
  department: process.env.DEPARTMENT_TEXT || '',
  clinic: process.env.CLINIC_TEXT || '',
  doctor: process.env.DOCTOR_TEXT || '',
  headless: String(process.env.HEADLESS || 'true').toLowerCase() !== 'false',
  checkIntervalMinutes: Number(process.env.CHECK_INTERVAL_MINUTES || 0),
  timeoutMs: Number(process.env.PAGE_TIMEOUT_MS || 45000),
  saveScreenshot: String(process.env.SAVE_SCREENSHOT || 'true').toLowerCase() !== 'false',
  recaptchaTimeoutMs: Number(process.env.RECAPTCHA_TIMEOUT_MS || 180000)
};

const NEGATIVE_PATTERNS = [
  /uygun\s*randevu\s*bulunamadı/i,
  /müsait\s*randevu\s*yok/i,
  /randevu\s*bulunamadı/i,
  /seçilen\s*kriterlere\s*uygun\s*kayıt\s*yok/i
];

const POSITIVE_PATTERNS = [
  /uygun\s*randevu/i,
  /müsait/i,
  /randevu\s*saati/i,
  /tarih\s*seç/i
];

const MONTHS_TR = [
  'Ocak',
  'Şubat',
  'Mart',
  'Nisan',
  'Mayıs',
  'Haziran',
  'Temmuz',
  'Ağustos',
  'Eylül',
  'Ekim',
  'Kasım',
  'Aralık'
];

function parseBirthDate(value) {
  const m = value.match(/^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$/);
  if (!m) return null;
  const day = String(Number(m[1]));
  const month = Number(m[2]);
  const year = String(Number(m[3]));
  if (month < 1 || month > 12) return null;
  return {
    day,
    month,
    year,
    monthPadded: String(month).padStart(2, '0'),
    monthNameTr: MONTHS_TR[month - 1]
  };
}

async function clickByText(page, regex) {
  const button = page.getByRole('button', { name: regex }).first();
  if (await button.count()) {
    await button.click({ timeout: 5000 });
    return true;
  }

  const fallback = page.locator('vaadin-button, button').filter({ hasText: regex }).first();
  if (await fallback.count()) {
    await fallback.click({ timeout: 5000 });
    return true;
  }

  return false;
}

async function ensureKvkkChecked(page) {
  const checkboxByLabel = page.getByLabel(/kvkk/i).first();
  if (await checkboxByLabel.count()) {
    if (!(await checkboxByLabel.isChecked())) {
      await checkboxByLabel.check({ timeout: 5000 });
    }
    return true;
  }

  const checkboxByRole = page.getByRole('checkbox', { name: /kvkk/i }).first();
  if (await checkboxByRole.count()) {
    if (!(await checkboxByRole.isChecked())) {
      await checkboxByRole.check({ timeout: 5000 });
    }
    return true;
  }

  const fallback = page.locator('vaadin-checkbox, input[type="checkbox"]').filter({ hasText: /kvkk/i }).first();
  if (await fallback.count()) {
    await fallback.click({ timeout: 5000 });
    return true;
  }

  return false;
}

async function waitForManualRecaptcha(page, timeoutMs) {
  const recaptchaFrame = page
    .frameLocator('iframe[title*="reCAPTCHA" i], iframe[src*="recaptcha" i]')
    .first();

  const recaptchaPresent = (await page.locator('iframe[title*="reCAPTCHA" i], iframe[src*="recaptcha" i]').count()) > 0;
  if (!recaptchaPresent) return true;

  if (CFG.headless) {
    throw new Error('reCAPTCHA bulundu. HEADLESS=true modunda manuel doğrulama yapılamaz. HEADLESS=false ile çalıştırın.');
  }

  console.log('[BILGI] reCAPTCHA bulundu. Lütfen tarayıcıda doğrulamayı manuel tamamlayın...');

  try {
    await recaptchaFrame.locator('#recaptcha-anchor[aria-checked="true"]').waitFor({
      timeout: timeoutMs
    });
  } catch {
    throw new Error(`reCAPTCHA ${timeoutMs / 1000} saniye içinde tamamlanmadı.`);
  }

  return true;
}

async function fillFirst(page, locatorCandidates, value) {
  for (const locator of locatorCandidates) {
    try {
      if (await locator.count()) {
        const el = locator.first();
        await el.click({ timeout: 1500 });
        await el.fill('');
        await el.fill(value);
        return true;
      }
    } catch {
      // Sonraki adayı dene.
    }
  }
  return false;
}

async function chooseDropdownOption(page, labelRegex, optionText) {
  if (!optionText) return true;

  try {
    const hostCombo = page.locator('vaadin-combo-box').filter({ hasText: labelRegex }).first();
    if (await hostCombo.count()) {
      await hostCombo.click({ timeout: 5000 });
    } else {
      const combo = page.getByRole('combobox', { name: labelRegex }).first();
      if (!(await combo.count())) return false;
      await combo.click({ timeout: 5000 });
    }

    const option = page.getByRole('option', { name: new RegExp(optionText, 'i') }).first();
    if (await option.count()) {
      await option.click({ timeout: 5000 });
      return true;
    }

    const fallback = page.locator('vaadin-item').filter({ hasText: new RegExp(optionText, 'i') }).first();
    if (await fallback.count()) {
      await fallback.click({ timeout: 5000 });
      return true;
    }
  } catch {
    return false;
  }

  return false;
}

async function chooseByLabel(page, labelRegex, candidates) {
  for (const optionText of candidates) {
    const ok = await chooseDropdownOption(page, labelRegex, optionText);
    if (ok) return true;
  }
  return false;
}

async function fillComboAndCommit(page, comboInput, candidates) {
  for (const candidate of candidates) {
    if (!candidate) continue;
    try {
      await comboInput.click({ timeout: 5000 });
      await comboInput.fill('');
      await comboInput.fill(candidate);
      await page.keyboard.press('Tab');
      await page.waitForTimeout(300);

      const current = (await comboInput.inputValue()).trim().toLowerCase();
      if (current === String(candidate).trim().toLowerCase()) {
        return true;
      }
    } catch {
      // Sonraki değeri dene.
    }
  }

  return false;
}

async function fillBirthDateByCombos(page, birthDate) {
  const parts = parseBirthDate(birthDate);
  if (!parts) return false;

  const yearCombo = page.getByRole('combobox').nth(0);
  const monthCombo = page.getByRole('combobox').nth(1);
  const dayCombo = page.getByRole('combobox').nth(2);
  if ((await yearCombo.count()) < 1 || (await monthCombo.count()) < 1 || (await dayCombo.count()) < 1) {
    return false;
  }

  const yearOk = await fillComboAndCommit(page, yearCombo, [parts.year]);
  if (!yearOk) return false;

  const monthOk = await fillComboAndCommit(page, monthCombo, [
    parts.monthNameTr,
    parts.monthPadded,
    String(parts.month)
  ]);
  if (!monthOk) return false;

  const dayOk = await fillComboAndCommit(page, dayCombo, [parts.day.padStart(2, '0'), parts.day]);
  return dayOk;
}

async function runOnce() {
  const browser = await chromium.launch({ headless: CFG.headless });
  const context = await browser.newContext({ locale: 'tr-TR' });
  const page = await context.newPage();
  page.setDefaultTimeout(CFG.timeoutMs);

  try {
    await page.goto(CFG.targetUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);

    const tcFilled = await fillFirst(
      page,
      [
        page.getByLabel(/(t\.?c\.?|tc).*kimlik/i),
        page.locator('input[name*="tc" i], input[id*="tc" i]'),
        page.locator('input[placeholder*="T.C" i], input[placeholder*="Kimlik" i]'),
        page.getByRole('textbox', { name: /(t\.?c\.?|tc).*kimlik/i })
      ],
      CFG.tc
    );

    let birthFilled = await fillFirst(
      page,
      [
        page.getByLabel(/doğum\s*tarihi/i),
        page.locator('input[name*="dog" i], input[id*="dog" i], input[name*="birth" i], input[id*="birth" i]'),
        page.locator('input[placeholder*="Doğum" i], input[placeholder*="gg" i], input[placeholder*="aa" i]'),
        page.getByRole('textbox', { name: /doğum\s*tarihi/i })
      ],
      CFG.birthDate
    );

    if (!birthFilled) {
      birthFilled = await fillBirthDateByCombos(page, CFG.birthDate);
    }

    if (!tcFilled || !birthFilled) {
      throw new Error('TC veya doğum tarihi alanı bulunamadı. İlk çalıştırmada HEADLESS=false ile selector kontrolü yapın.');
    }

    await ensureKvkkChecked(page);
    await waitForManualRecaptcha(page, CFG.recaptchaTimeoutMs);

    await clickByText(page, /(devam|sorgula|giriş|ileri|randevu\s*ara)/i);
    await page.waitForTimeout(2500);

    if (CFG.department) {
      const ok = await chooseDropdownOption(page, /(bölüm|branş|klinik|poliklinik)/i, CFG.department);
      if (!ok) throw new Error(`Bölüm/branş seçimi başarısız: ${CFG.department}`);
    }

    if (CFG.clinic) {
      const ok = await chooseDropdownOption(page, /(klinik|poliklinik|birim)/i, CFG.clinic);
      if (!ok) throw new Error(`Klinik seçimi başarısız: ${CFG.clinic}`);
    }

    if (CFG.doctor) {
      const ok = await chooseDropdownOption(page, /(doktor|hekim)/i, CFG.doctor);
      if (!ok) throw new Error(`Doktor seçimi başarısız: ${CFG.doctor}`);
    }

    await clickByText(page, /(ara|sorgula|listele|randevu|uygun)/i);
    await page.waitForTimeout(3000);

    const bodyText = (await page.locator('body').innerText()).replace(/\s+/g, ' ').trim();
    const hasNegative = NEGATIVE_PATTERNS.some((re) => re.test(bodyText));
    const hasPositive = POSITIVE_PATTERNS.some((re) => re.test(bodyText));

    const timestamp = new Date().toISOString();
    const result = {
      timestamp,
      status: hasNegative ? 'NOT_AVAILABLE' : hasPositive ? 'POSSIBLY_AVAILABLE' : 'UNKNOWN',
      url: page.url()
    };

    fs.writeFileSync(
      path.join(process.cwd(), 'artifacts', 'last-result.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8'
    );

    if (CFG.saveScreenshot) {
      await page.screenshot({
        path: path.join(process.cwd(), 'artifacts', 'last-check.png'),
        fullPage: true
      });
    }

    if (result.status === 'NOT_AVAILABLE') {
      console.log(`[${timestamp}] Uygun randevu bulunamadı.`);
      return 2;
    }

    if (result.status === 'POSSIBLY_AVAILABLE') {
      console.log(`[${timestamp}] Muhtemel uygun randevu bulundu. Ekranı kontrol edin.`);
      return 0;
    }

    console.log(`[${timestamp}] Durum belirsiz. Ekran görüntüsünü kontrol edin: artifacts/last-check.png`);
    return 3;
  } finally {
    await context.close();
    await browser.close();
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function main() {
  if (CFG.checkIntervalMinutes > 0) {
    console.log(`Sürekli izleme aktif. Her ${CFG.checkIntervalMinutes} dakikada bir kontrol.`);
    while (true) {
      try {
        await runOnce();
      } catch (error) {
        console.error(`[HATA] ${error.message}`);
      }
      await sleep(CFG.checkIntervalMinutes * 60 * 1000);
    }
  }

  const code = await runOnce();
  process.exit(code);
}

main().catch((error) => {
  console.error(`[KRITIK] ${error.message}`);
  process.exit(1);
});
