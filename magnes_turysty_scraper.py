import os
import requests
import gspread
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup
import re
import time
import json
from datetime import datetime

# ── Konfiguracja Google Sheets ────────────────────────────────────────────
CREDENTIALS_FILE = "google_credentials.json"
SPREADSHEET_NAME = "MagnesTurysty"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def polacz_z_arkuszem():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    klient = gspread.authorize(creds)
    arkusz = klient.open(SPREADSHEET_NAME)
    try:
        dane = arkusz.worksheet("Dane")
    except gspread.exceptions.WorksheetNotFound:
        dane = arkusz.add_worksheet(title="Dane", rows=5000, cols=6)
    try:
        log = arkusz.worksheet("Log")
    except gspread.exceptions.WorksheetNotFound:
        log = arkusz.add_worksheet(title="Log", rows=1000, cols=3)
    return dane, log


# ── Konfiguracja scrapera ─────────────────────────────────────────────────
data_rows = []

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
}
session = requests.Session()

IGNOROWANE = re.compile(
    r'^(octopus|żmigrodzka|wrocław|tel\.|znajdź|facebook|instagram|'
    r'poniedziałek|wtorek|środa|czwartek|piątek|sobota|niedziela|'
    r'godziny|otwarte|czynne|od\s+\d|'
    r'pn[.\-\s]|pt[.\-\s]|sb[.\-\s]|nd[.\-\s]|wt[.\-\s]|śr[.\-\s]|czw[.\-\s]|'
    r'pon[.\-\s]|sob[.\-\s]|niedz[.\-\s]|'
    r'\d{1,2}:\d{2}|'
    r'www\.|https?://)',
    re.IGNORECASE
)

MENU_STOPKA = re.compile(
    r'^(strona główna|o projekcie|o nas|kontakt|regulamin|polityka prywatności|'
    r'magnesturysty\.pl|znajdź nas)$',
    re.IGNORECASE
)

TYLKO_GODZINY = re.compile(r'^[\w\s.\-–,:]+\d{1,2}[:.]\d{2}', re.IGNORECASE)


def czy_adres(tekst):
    if re.search(r'\b(ul\.|al\.|pl\.|os\.|skwer|rynek|droga)\b', tekst, re.IGNORECASE):
        return True
    if re.search(r'\d+\s*[A-Za-z]?\s*(/\s*\d+)?\s*(\(.*\))?\s*$', tekst):
        return True
    return False


def jest_smieci(tekst):
    if IGNOROWANE.match(tekst):
        return True
    if TYLKO_GODZINY.match(tekst):
        return True
    return False


def wyciagnij_punkt(item):
    fragmenty = []
    for node in item.descendants:
        if isinstance(node, str):
            t = node.strip()
            if t:
                fragmenty.append(t)
    fragmenty = [f for f in fragmenty if len(f) > 1 and not jest_smieci(f)]
    if not fragmenty:
        return None, None
    nazwa = fragmenty[0]
    if MENU_STOPKA.match(nazwa):
        return None, None
    for fragment in fragmenty[1:]:
        if czy_adres(fragment):
            return nazwa, fragment
    return nazwa, ''


def pobierz_nazwe_miasta(soup):
    h2 = soup.find('h2')
    if h2:
        tekst = h2.get_text(separator=' ').strip()
        tekst = re.sub(r'^[^a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+', '', tekst).strip()
        if tekst:
            return tekst
    return None

def pobierz_liste_miast():
    """Pobiera listę miast ze strony głównej przez Selenium (obsługuje JavaScript)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    import time as t

    WOJEWODZTWA = [
        'dolnoslaskie', 'kujawsko-pomorskie', 'lubelskie', 'lubuskie',
        'lodzkie', 'malopolskie', 'mazowieckie', 'opolskie', 'podkarpackie',
        'podlaskie', 'pomorskie', 'slaskie', 'swietokrzyskie',
        'warminsko-mazurskie', 'wielkopolskie', 'zachodniopomorskie'
    ]

    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')

    print("Uruchamiam Selenium...")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    linki = []
    seen = set()

    try:
        driver.get("https://magnesturysty.pl/")
        # Poczekaj aż załadują się linki do miast
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "map_city"))
        )
        # Dodatkowe oczekiwanie na pełne załadowanie
        t.sleep(3)

        elementy = driver.find_elements(By.CSS_SELECTOR, "a.map_link")
        print(f"Znaleziono {len(elementy)} linków przez Selenium.")

        for el in elementy:
            href = el.get_attribute('href') or ''
            href = href.replace('https://www.magnesturysty.pl/', 'https://magnesturysty.pl/')
            if not href.startswith('https://magnesturysty.pl/'):
                continue
            href = href.split('?')[0]  # Usuń parametry
            if not href.endswith('/'):
                href += '/'
            slug = href.strip('/').split('/')[-1]
            if len(slug) <= 3 or 'magnesturysty' in slug.lower():
                continue
            if any(x in href for x in ['.png', '.jpg', '/sklep', '/kontakt',
                                        '/regulamin', '/polityka']):
                continue
            if href not in seen:
                seen.add(href)
                nazwa = slug.replace('-', ' ').title()
                linki.append({"url": href, "miasto": nazwa})

    except Exception as e:
        print(f"Błąd Selenium: {e} — fallback na requests")
        # Fallback na requests jeśli Selenium zawiedzie
        try:
            response = session.get("https://magnesturysty.pl/", headers=headers, timeout=20)
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href'].strip()
                href = href.replace('https://www.magnesturysty.pl/', 'https://magnesturysty.pl/')
                if not href.startswith('https://magnesturysty.pl/'):
                    continue
                href = href.split('?')[0]
                if not href.endswith('/'):
                    href += '/'
                slug = href.strip('/').split('/')[-1]
                if len(slug) <= 3 or 'magnesturysty' in slug.lower():
                    continue
                if any(woj in slug for woj in WOJEWODZTWA) or slug == 'gora-swietej-anny':
                    if href not in seen:
                        seen.add(href)
                        nazwa = slug.replace('-', ' ').title()
                        linki.append({"url": href, "miasto": nazwa})
        except Exception as e2:
            print(f"Błąd fallback: {e2}")
    finally:
        driver.quit()

    print(f"Znaleziono {len(linki)} miast łącznie.")
    return linki


# ── Geokodowanie przez Google Maps Geocoding API ──────────────────────────
geo_cache = {}
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")


def oczysc_adres(pelny_adres):
    parts = pelny_adres.split(',')
    if len(parts) > 1:
        return ",".join(parts[1:]).strip()
    return pelny_adres


def wczytaj_cache_z_sheets(klient_gspread):
    """Wczytuje zapisane współrzędne z zakładki 'Geo Cache'."""
    global geo_cache
    try:
        arkusz = klient_gspread.open(SPREADSHEET_NAME)
        try:
            sheet = arkusz.worksheet("Geo Cache")
        except gspread.exceptions.WorksheetNotFound:
            sheet = arkusz.add_worksheet(title="Geo Cache", rows=5000, cols=3)
            sheet.append_row(["Adres", "Lat", "Lng"])
            print("  Utworzono zakładkę 'Geo Cache'.")
            return sheet

        wiersze = sheet.get_all_records()
        for w in wiersze:
            adres = str(w.get("Adres", "")).strip()
            try:
                lat = float(str(w.get("Lat", "")).replace(",", "."))
                lng = float(str(w.get("Lng", "")).replace(",", "."))
                if adres and lat and lng:
                    geo_cache[adres] = (lat, lng)
            except (ValueError, TypeError):
                continue
        print(f"  Wczytano {len(geo_cache)} wpisów z cache.")
        return sheet
    except Exception as e:
        print(f"  BŁĄD wczytywania cache: {e}")
        return None


def zapisz_nowe_do_cache(sheet_cache, nowe_wpisy):
    """Dopisuje tylko nowe wpisy do zakładki 'Geo Cache'."""
    if not nowe_wpisy or sheet_cache is None:
        return
    wiersze = [[adres, lat, lng] for adres, (lat, lng) in nowe_wpisy.items()]
    for i in range(0, len(wiersze), 100):
        sheet_cache.append_rows(wiersze[i:i+100])
    print(f"  Dopisano {len(wiersze)} nowych wpisów do cache.")


def geokoduj(pelny_adres):
    """Geokoduje adres — najpierw sprawdza cache, potem odpytuje API."""
    if pelny_adres in geo_cache:
        return geo_cache[pelny_adres]

    for adres in [pelny_adres, oczysc_adres(pelny_adres)]:
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": adres,
                "key": GOOGLE_MAPS_API_KEY,
                "region": "pl",
                "language": "pl",
                "components": "country:PL"
            }
            r = requests.get(url, params=params, timeout=10)
            wyniki = r.json()
            if wyniki.get("status") == "OK":
                loc = wyniki["results"][0]["geometry"]["location"]
                lat, lng = loc["lat"], loc["lng"]
                geo_cache[pelny_adres] = (lat, lng)
                return lat, lng
        except Exception:
            continue

    geo_cache[pelny_adres] = (None, None)
    return None, None


def pobierz_mam_magnesy(klient_gspread):
    """Pobiera i geokoduje miejscowości z zakładki 'Mam magnes'."""
    try:
        arkusz = klient_gspread.open(SPREADSHEET_NAME)
        try:
            sheet = arkusz.worksheet("Mam magnes")
        except gspread.exceptions.WorksheetNotFound:
            sheet = arkusz.add_worksheet(title="Mam magnes", rows=500, cols=2)
            sheet.append_row(["Miejscowość", "Data dodania"])
            print("  Utworzono zakładkę 'Mam magnes'.")
            return []

        wiersze = sheet.get_all_records()
        magnesy = []
        for w in wiersze:
            nazwa = str(w.get("Miejscowość", "")).strip()
            if not nazwa:
                continue
            zapytanie = f"{nazwa}, Polska"
            lat, lng = geokoduj(zapytanie)
            if lat and lng:
                magnesy.append({"miasto": nazwa, "lat": lat, "lng": lng})
                print(f"  ✓ {nazwa} -> {lat:.4f}, {lng:.4f}")
            else:
                print(f"  ✗ BRAK GEOKODU: {nazwa}")

        print(f"  Geokodowano {len(magnesy)} miejscowości z 'Mam magnes'.")
        return magnesy
    except Exception as e:
        print(f"  UWAGA: Błąd pobierania zakładki 'Mam magnes': {e}")
        return []


# ── Generowanie mapy HTML ─────────────────────────────────────────────────
def generuj_mape_html(punkty, magnesy, data_aktualizacji, sheet_cache):
    """Generuje plik index.html z mapą Leaflet.js."""

    print("\nGeokodowanie adresów...")
    geokodowane = []
    nowe_wpisy = {}

    for i, p in enumerate(punkty):
        if p["Adres"]:
            miasto_krotko = p["Miejscowość"].split(',')[0].strip()
            zapytanie = f"{p['Adres']}, {miasto_krotko}, Polska"
        else:
            miasto_krotko = p["Miejscowość"].split(',')[0].strip()
            zapytanie = f"{miasto_krotko}, Polska"

        bylo_w_cache = zapytanie in geo_cache
        lat, lng = geokoduj(zapytanie)

        if lat and lng:
            # Jeśli to nowy wpis — zapamiętaj do zapisania
            if not bylo_w_cache:
                nowe_wpisy[zapytanie] = (lat, lng)

            geokodowane.append({
                "nazwa": p["Punkt"],
                "adres": p["Adres"],
                "miasto": p["Miejscowość"],
                "lat": lat,
                "lng": lng
            })
        else:
            print(f"  BRAK GEOKODU: {p['Punkt']} | {p['Adres']} | {p['Miejscowość']}")

        if (i + 1) % 100 == 0:
            print(f"  Geokodowano {i+1}/{len(punkty)}...")

    print(f"  Geokodowano {len(geokodowane)}/{len(punkty)} punktów.")
    print(f"  Nowych wpisów do cache: {len(nowe_wpisy)}")

    # Zapisz nowe wpisy do cache w Sheets
    zapisz_nowe_do_cache(sheet_cache, nowe_wpisy)

    punkty_json = json.dumps(geokodowane, ensure_ascii=False)
    magnesy_json = json.dumps(magnesy, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MagnesTurysty – Mapa punktów sprzedaży</title>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css"/>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.Default.css"/>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; }}
    #header {{ background: #2c3e50; color: white; padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; }}
    #header h1 {{ font-size: 16px; font-weight: 500; }}
    #header span {{ font-size: 12px; opacity: 0.7; }}
    #search-bar {{ padding: 10px 16px; background: white; border-bottom: 1px solid #e0e0e0; display: flex; gap: 8px; align-items: center; }}
    #search {{ flex: 1; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; outline: none; }}
    #search:focus {{ border-color: #2980b9; }}
    #counter {{ font-size: 13px; color: #666; white-space: nowrap; }}
    #map {{ width: 100%; height: calc(100vh - 96px); }}
    .popup-nazwa {{ font-weight: 600; font-size: 14px; margin-bottom: 4px; }}
    .popup-adres {{ font-size: 13px; color: #555; }}
    .popup-miasto {{ font-size: 12px; color: #888; margin-top: 2px; }}
    .popup-magnes {{ font-size: 13px; color: #c0392b; font-weight: 500; margin-top: 4px; }}
    .legenda {{ position: absolute; bottom: 30px; right: 10px; z-index: 1000; background: white;
                padding: 10px 14px; border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,0.2);
                font-size: 13px; line-height: 1.8; }}
    .legenda-item {{ display: flex; align-items: center; gap: 8px; }}
  </style>
</head>
<body>
  <div id="header">
    <h1>📍 MagnesTurysty – Punkty sprzedaży</h1>
    <span>Aktualizacja: {data_aktualizacji}</span>
  </div>
  <div id="search-bar">
    <input id="search" type="text" placeholder="Szukaj miejscowości lub punktu..."/>
    <div id="counter">Ładowanie...</div>
  </div>
  <div id="map"></div>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.min.js"></script>
  <script>
    const punkty = {punkty_json};
    const magnesy = {magnesy_json};

    const map = L.map('map').setView([52.0, 19.5], 6);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19
    }}).addTo(map);

    const iconPunkt = L.icon({{
      iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
      shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
      iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34]
    }});

    const iconMagnes = L.icon({{
      iconUrl: 'https://raw.githubusercontent.com/biotyt/magnesturysty-scraper/main/Magnes.png',
      iconSize: [48, 32],
      iconAnchor: [24, 32],
      popupAnchor: [0, -32]
    }});

    const cluster = L.markerClusterGroup({{ maxClusterRadius: 40 }});
    const wszystkieMarkery = [];

    punkty.forEach(p => {{
      const marker = L.marker([p.lat, p.lng], {{icon: iconPunkt}})
        .bindPopup(`<div class="popup-nazwa">${{p.nazwa}}</div>
          <div class="popup-adres">${{p.adres || '–'}}</div>
          <div class="popup-miasto">${{p.miasto}}</div>`);
      marker._dane = p;
      cluster.addLayer(marker);
      wszystkieMarkery.push(marker);
    }});

    map.addLayer(cluster);

    magnesy.forEach(m => {{
      L.marker([m.lat, m.lng], {{icon: iconMagnes, zIndexOffset: 1000}})
        .addTo(map)
        .bindPopup(`<div class="popup-nazwa">${{m.miasto}}</div>
          <div class="popup-magnes">🧲 Mam magnes z tej miejscowości!</div>`);
    }});

    document.getElementById('counter').textContent = punkty.length + ' punktów';

    const legenda = L.control({{position: 'bottomright'}});
    legenda.onAdd = () => {{
      const div = L.DomUtil.create('div', 'legenda');
      div.innerHTML = `
        <div class="legenda-item"><img src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png" style="height:20px"> Punkt sprzedaży</div>
        <div class="legenda-item" style="margin-top:4px"><img src="https://raw.githubusercontent.com/biotyt/magnesturysty-scraper/main/Magnes.png" style="height:20px"> Mam magnes</div>
      `;
      return div;
    }};
    legenda.addTo(map);

    document.getElementById('search').addEventListener('input', function() {{
      const q = this.value.toLowerCase().trim();
      cluster.clearLayers();
      const pasujace = q
        ? wszystkieMarkery.filter(m =>
            m._dane.nazwa.toLowerCase().includes(q) ||
            m._dane.miasto.toLowerCase().includes(q) ||
            (m._dane.adres && m._dane.adres.toLowerCase().includes(q)))
        : wszystkieMarkery;
      pasujace.forEach(m => cluster.addLayer(m));
      document.getElementById('counter').textContent = pasujace.length + ' punktów';
    }});
  </script>
</body>
</html>"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Wygenerowano index.html")


# ── Główna pętla scrapera ──────────────────────────────────────────────────
miasta = pobierz_liste_miast()
print(f"Pobrano {len(miasta)} miast. Zaczynam analizę...")

for index, pozycja in enumerate(miasta):
    print(f"[{index+1}/{len(miasta)}] Analizuję: {pozycja['miasto']}... ", end="")
    try:
        res = session.get(pozycja["url"], headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        nazwa_z_h2 = pobierz_nazwe_miasta(soup)
        miasto_format = nazwa_z_h2 if nazwa_z_h2 else pozycja["miasto"].replace(" ", ", ", 1)
        znaleziono = 0
        kontenery = soup.find_all('div', class_='elementor-widget-container')
        for kontener in kontenery:
            if kontener.find_parent(['nav', 'footer', 'header']):
                continue
            lista = kontener.find('ul')
            if not lista:
                continue
            for item in lista.find_all('li'):
                nazwa, adres = wyciagnij_punkt(item)
                if nazwa is None:
                    continue
                pelny_zapis = (f"{nazwa}, {adres}, {miasto_format}, Polska"
                               if adres else f"{nazwa}, {miasto_format}, Polska")
                data_rows.append({
                    "Miejscowość": miasto_format,
                    "Punkt": nazwa,
                    "Adres": adres,
                    "Pełny zapis (Miasto, Adres)": pelny_zapis
                })
                znaleziono += 1
        print(f"Znaleziono: {znaleziono}")
    except Exception as e:
        print(f"BŁĄD: {e}")
    time.sleep(0.3)


# ── Zapis do Google Sheets ────────────────────────────────────────────────
print("\nŁączę z Google Sheets...")
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
klient_gspread = gspread.authorize(creds)

arkusz_dane, arkusz_log = polacz_z_arkuszem()
arkusz_dane.clear()
naglowki = ["Miejscowość", "Punkt", "Adres", "Pełny zapis (Miasto, Adres)"]
arkusz_dane.append_row(naglowki)
wiersze = [[r["Miejscowość"], r["Punkt"], r["Adres"], r["Pełny zapis (Miasto, Adres)"]]
           for r in data_rows]
BATCH = 500
for i in range(0, len(wiersze), BATCH):
    arkusz_dane.append_rows(wiersze[i:i+BATCH], value_input_option="USER_ENTERED")
    print(f"  Zapisano wiersze {i+1}–{min(i+BATCH, len(wiersze))}")
    time.sleep(1)
czas = datetime.now().strftime("%Y-%m-%d %H:%M")
arkusz_log.append_row([czas, len(data_rows), f"OK – {len(miasta)} miast"])
print(f"Zapisano {len(data_rows)} punktów do Google Sheets [{czas}]")

# ── Wczytaj cache geokodowania ────────────────────────────────────────────
print("\nWczytuję cache geokodowania...")
sheet_cache = wczytaj_cache_z_sheets(klient_gspread)

# ── Pobierz zakładkę "Mam magnes" ────────────────────────────────────────
print("\nPobieram zakładkę 'Mam magnes'...")
magnesy = pobierz_mam_magnesy(klient_gspread)

# ── Generowanie mapy HTML ─────────────────────────────────────────────────
print("\nRozpoczęcie generowania mapy HTML...")
try:
    generuj_mape_html(data_rows, magnesy, czas, sheet_cache)
    print("Mapa wygenerowana pomyślnie.")
except Exception as e:
    import traceback
    print(f"BŁĄD podczas generowania mapy: {e}")
    traceback.print_exc()
    raise
