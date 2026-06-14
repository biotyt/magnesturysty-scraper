import requests
import gspread
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup

CREDENTIALS_FILE = "google_credentials.json"
SPREADSHEET_NAME = "MagnesTurysty"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
}

# Połącz z Google Sheets
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
klient = gspread.authorize(creds)
arkusz = klient.open(SPREADSHEET_NAME)

try:
    sheet = arkusz.worksheet("Miasta")
    sheet.clear()
except gspread.exceptions.WorksheetNotFound:
    sheet = arkusz.add_worksheet(title="Miasta", rows=2000, cols=3)

sheet.append_row(["Województwo", "Miasto", "URL"])

# Pobierz stronę główną
response = requests.get("https://magnesturysty.pl/", headers=headers, timeout=20)
soup = BeautifulSoup(response.text, 'html.parser')

wiersze = []
for accordion in soup.find_all('div', class_='eael-accordion-list'):
    # Pobierz nazwę województwa z id
    naglowek = accordion.find('div', class_='eael-accordion-header')
    if not naglowek:
        continue
    woj_id = naglowek.get('id', '')
    woj_nazwa = naglowek.find('span', class_='eael-accordion-tab-title')
    woj_nazwa = woj_nazwa.get_text(strip=True) if woj_nazwa else woj_id

    # Pobierz miasta
    for a in accordion.find_all('a', class_='map_link'):
        href = (a.get('href') or '').strip()
        href = href.replace('https://www.magnesturysty.pl/', 'https://magnesturysty.pl/')
        href = href.split('?')[0]
        if not href.endswith('/'):
            href += '/'
        nazwa = a.get_text(strip=True).lstrip('*')
        if nazwa and href:
            wiersze.append([woj_nazwa, nazwa, href])

# Zapisz do Sheets
print(f"Znaleziono {len(wiersze)} miast.")
for i in range(0, len(wiersze), 100):
    sheet.append_rows(wiersze[i:i+100])
    
print("Zapisano do zakładki 'Miasta'.")
