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
    url = "https://magnesturysty.pl/page-sitemap.xml"
    try:
        response = session.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(response.content, 'html.parser')
        linki = []
        for loc in soup.find_all('loc'):
            adres = loc.text.strip()
            if any(x in adres for x in [".png", ".jpg", "/sklep", "/kontakt",
                                         "/regulamin", "/polityka",
                                         "/miejscowosci", "/o-nas", "/o-projekcie"]):
                continue
            slug = adres.strip('/').split('/')[-1]
            if len(slug) <= 3:
                continue
            if 'magnesturysty' in slug.lower():
                continue
            nazwa = slug.replace('-', ' ').title()
            linki.append({"url": adres, "miasto": nazwa})
        return linki
    except Exception as e:
        print(f"Błąd sitemap: {e}")
        return []


# ── Geokodowanie przez Google Maps Geocoding API ──────────────────────────
geo_cache = {}
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

def oczysc_adres(pelny_adres):
    """Usuwa nazwę firmy i zostawia tylko dane adresowe."""
    parts = pelny_adres.split(',')
    if len(parts) > 1:
        return ",".join(parts[1:]).strip()
    return pelny_adres

def geokoduj(pelny_adres):
    if pelny_adres in geo_cache:
        return geo_cache[pelny_adres]

    for adres in [pelny_adres, oczysc_adres(pelny_adres)]:
        try:
            url = "https://maps.googleapis.com/maps/api/geocode/json"
            params = {
                "address": adres,
                "key": GOOGLE_MAPS_API_KEY,
                "region": "pl",
                "language": "pl"
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


# ── Generowanie mapy HTML ─────────────────────────────────────────────────
def generuj_mape_html(punkty, data_aktualizacji):
    """Generuje plik index.html z mapą Leaflet.js."""

    print("\nGeokodowanie adresów...")
    geokodowane = []
    for i, p in enumerate(punkty):
        if p["Adres"]:
            zapytanie = f"{p['Adres']}, {p['Miejscowość']}, Polska"
        else:
            zapytanie = f"{p['Miejscowość']}, Polska"

        lat, lng = geokoduj(zapytanie)
        if lat and lng:
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

    punkty_json = json.dumps(geokodowane, ensure_ascii=False)

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
    #search-bar {{ padding: 10px 16px; background: white; border-bottom: 1px solid #e0e0e0; display: flex; gap: 8px; }}
    #search {{ flex: 1; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; outline: none; }}
    #search:focus {{ border-color: #2980b9; }}
    #counter {{ font-size: 13px; color: #666; display: flex; align-items: center; white-space: nowrap; }}
    #map {{ width: 100%; height: calc(100vh - 96px); }}
    .popup-nazwa {{ font-weight: 600; font-size: 14px; margin-bottom: 4px; }}
    .popup-adres {{ font-size: 13px; color: #555; }}
    .popup-miasto {{ font-size: 12px; color: #888; margin-top: 2px; }}
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

    const map = L.map('map').setView([52.0, 19.5], 6);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19
    }}).addTo(map);

    const icon = L.divIcon({{
      html: '<div style="width:10px;height:10px;border-radius:50%;background:#e74c3c;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,0.4);"></div>',
      className: '',
      iconSize: [10, 10],
      iconAnchor: [5, 5],
      popupAnchor: [0, -8]
    }});

    const cluster = L.markerClusterGroup({{ maxClusterRadius: 40 }});
    const wszystkieMarkery = [];

    punkty.forEach(p => {{
      const marker = L.marker([p.lat, p.lng], {{icon}})
        .bindPopup(`<div class="popup-nazwa">${{p.nazwa}}</div>
          <div class="popup-adres">${{p.adres || '–'}}</div>
          <div class="popup-miasto">${{p.miasto}}</div>`);
      marker._dane = p;
      cluster.addLayer(marker);
      wszystkieMarkery.push(marker);
    }});

    map.addLayer(cluster);
    document.getElementById('counter').textContent = punkty.length + ' punktów';

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
miasta = pobierz_liste_miast()[:20]
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


# ── Generowanie mapy HTML ─────────────────────────────────────────────────
print("\nRozpoczęcie generowania mapy HTML...")
try:
    generuj_mape_html(data_rows, czas)
    print("Mapa wygenerowana pomyślnie.")
except Exception as e:
    import traceback
    print(f"BŁĄD podczas generowania mapy: {e}")
    traceback.print_exc()
    raise
