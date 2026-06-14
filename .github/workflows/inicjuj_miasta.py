name: Inicjuj listę miast

on:
  workflow_dispatch:

env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true

jobs:
  inicjuj:
    runs-on: ubuntu-latest

    steps:
      - name: Pobierz kod z repozytorium
        uses: actions/checkout@v4

      - name: Ustaw Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Zainstaluj zależności
        run: pip install requests beautifulsoup4 gspread google-auth

      - name: Utwórz plik credentials z Secret
        run: echo '${{ secrets.GOOGLE_CREDENTIALS }}' > google_credentials.json

      - name: Inicjuj listę miast
        run: python inicjuj_miasta.py
