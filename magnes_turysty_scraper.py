import requests
import gspread
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup
import re
import time
from datetime import datetime

# ── Konfiguracja Google Sheets ────────────────────────────────────────────
CREDENTIALS_FILE = "google_credentials.json"  # ścieżka do pobranego klucza JSON
SPREADSHEET_NAME = "MagnesTurysty"            # nazwa arkusza Google Sheets

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def polacz_z_arkuszem():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    klient = gspread.authorize(creds)
    arkusz = klient.open(SPREADSHEET_NAME)

    # Arkusz "Dane" — główne dane z punktami
    try:
        dane = arkusz.worksheet("Dane")
    except gspread.exceptions.WorksheetNotFound:
        dane = arkusz.add_worksheet(title="Dane", rows=5000, cols=6)

    # Arkusz "Log" — historia aktualizacji
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


# ── Główna pętla scrapera ─────────────────────────────────────────────────
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
                               if adres
                               else f"{nazwa}, {miasto_format}, Polska")

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

# Wyczyść arkusz i wpisz nagłówki
arkusz_dane.clear()
naglowki = ["Miejscowość", "Punkt", "Adres", "Pełny zapis (Miasto, Adres)"]
arkusz_dane.append_row(naglowki)

# Wpisz dane partiami po 500 wierszy (limit API Google)
wiersze = [[r["Miejscowość"], r["Punkt"], r["Adres"], r["Pełny zapis (Miasto, Adres)"]]
           for r in data_rows]

BATCH = 500
for i in range(0, len(wiersze), BATCH):
    arkusz_dane.append_rows(wiersze[i:i+BATCH], value_input_option="USER_ENTERED")
    print(f"  Zapisano wiersze {i+1}–{min(i+BATCH, len(wiersze))}")
    time.sleep(1)  # ochrona przed limitem API

# Wpis do logu
czas = datetime.now().strftime("%Y-%m-%d %H:%M")
arkusz_log.append_row([czas, len(data_rows), f"OK – {len(miasta)} miast"])

print(f"\nGotowe! Zapisano {len(data_rows)} punktów do Google Sheets [{czas}]")
