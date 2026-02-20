# Hacettepe Randevu Kontrol Otomasyonu

Bu proje, Hacettepe randevu ekranında TC kimlik ve doğum tarihi ile giriş yapıp uygun randevu var/yok kontrolü için Playwright tabanlı bir otomasyon sağlar.

## 1) Kurulum

```bash
npm install
npx playwright install chromium
cp .env.example .env
```

`.env` içinde `TC_KIMLIK_NO` ve `DOGUM_TARIHI` alanlarını doldurun.

## 2) Tek sefer kontrol

```bash
npm run check
```

Debug (tarayıcı açık):

```bash
npm run check:debug
```

`reCAPTCHA` olan adım için `HEADLESS=false` (debug modu) kullanın ve doğrulamayı elle tamamlayın.

## 3) Sürekli izleme

`.env` içinde:

```env
CHECK_INTERVAL_MINUTES=10
```

sonra:

```bash
npm run check
```

## 4) Çıktılar

- `artifacts/last-result.json`: Son kontrol özeti
- `artifacts/last-check.png`: Son ekran görüntüsü

## Notlar

- Site arayüzü değişirse selector'lar güncellenmelidir.
- İlk çalıştırmada `HEADLESS=false` ile açıp alanların doğru doldurulduğunu kontrol edin.
- `KVKK` kutusu script tarafından işaretlenir.
- `reCAPTCHA` otomatik geçilmez; güvenlik nedeniyle manuel doğrulama beklenir.
- Bu script randevuyu otomatik onaylamaz; sadece uygunluk kontrolü yapar.
