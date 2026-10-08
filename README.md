# Kripto Analiz - APK

## APK'yı GitHub ile derleme
1. github.com'da yeni (boş) bir repo aç.
2. Bu klasördeki dosyaları repoya yükle. Yapı şu olmalı:
   - `pyproject.toml`
   - `src/main.py`
   - `src/analyzer.py`
   - `.github/workflows/build-apk.yml`
   (Gizli `.github` klasörü yüklenmezse: Add file > Create new file, dosya adına
   `.github/workflows/build-apk.yml` yaz ve içeriği yapıştır.)
3. Actions sekmesi > "Build APK" > Run workflow.
4. ~10-15 dk sonra bitince çalıştırmanın sayfasında Artifacts > `kripto-analiz-apk`.
5. Zip'i aç, `.apk`'yı telefona kur (bilinmeyen kaynaklardan kuruluma izin ver).

## Bilgisayarda test (opsiyonel)
    pip install flet==0.28.3 pandas numpy requests
    python src/main.py          # veya: flet run src/main.py

## Notlar
- Binance erişilemezse otomatik olarak data-api.binance.vision denenir.
- "Demo veri" anahtarı internetsiz test içindir.
- Yalnızca analiz yapar, emir göndermez. Yatırım tavsiyesi değildir.
