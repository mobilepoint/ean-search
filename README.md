# GTIN/EAN Finder via Google CSE

Aplicație Streamlit care caută și completează EAN-13 (GTIN-13) după SKU sau denumire, folosind **Google Custom Search API**.

## Funcționalități
- Alegi modul de căutare: **Doar SKU** sau **Doar Nume**.
- Sari automat rândurile unde coloana țintă are deja valoare.
- Acceptă CSV-uri delimitate cu `,` sau `;`.
- Validează codurile EAN-13.
- Afișează în sidebar consumul de requests și quota rămasă.

## Pași de deploy
1. Urcă fișierele pe GitHub.
2. Creează aplicația pe Streamlit Cloud (`app.py` entrypoint).
3. În Settings → Secrets adaugă:
