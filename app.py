# -*- coding: utf-8 -*-
import re, time
import pandas as pd
import streamlit as st
import requests
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

st.set_page_config(page_title="GTIN/EAN Finder via Google CSE (Excel)", layout="wide")
st.title("GTIN/EAN Finder via Google CSE (Excel + Google Drive)")

# === Google API keys ===
GOOGLE_API_KEY = st.secrets.get("GOOGLE_API_KEY")
GOOGLE_CSE_CX = st.secrets.get("GOOGLE_CSE_CX")

# === Google Drive service account ===
@st.cache_resource
def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    return build("drive", "v3", credentials=creds)

drive_service = get_drive_service()
DRIVE_FOLDER_ID = st.secrets.get("DRIVE_FOLDER_ID")

# === Helpers pentru EAN ===
def clean_digits(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")

def ean13_check_digit(d12: str) -> int:
    s = sum((ord(ch)-48) * (3 if i%2 else 1) for i, ch in enumerate(d12))
    return (10 - (s % 10)) % 10

def is_valid_ean13(code: str) -> bool:
    d = clean_digits(code)
    return len(d) == 13 and ean13_check_digit(d[:12]) == int(d[-1])

def upc12_to_gtin13(upc: str):
    d = clean_digits(upc)
    if len(d) != 12: return None
    cand = "0" + d
    return cand if is_valid_ean13(cand) else None

EAN_RE = re.compile(r"\b(?:\d[ \t\-]?){12,14}\b")

def find_eans_in_text(text: str):
    out = []
    for m in EAN_RE.finditer(text or ""):
        d = clean_digits(m.group(0))
        if len(d) == 13 and is_valid_ean13(d): out.append(d)
        elif len(d) == 12:
            gt = upc12_to_gtin13(d)
            if gt: out.append(gt)
    return list(dict.fromkeys(out))

def choose_best_ean(texts_with_weights):
    scores = {}
    for text, w in texts_with_weights:
        for c in find_eans_in_text(text):
            base = 1.0
            if re.search(r"(ean|gtin|barcode|cod\s*ean|ean-13)", text.lower()):
                base += 1.0
            scores[c] = scores.get(c, 0.0) + base * w
    return max(scores.items(), key=lambda kv: kv[1])[0] if scores else None

# === Google Custom Search ===
if "request_count" not in st.session_state:
    st.session_state["request_count"] = 0
DAILY_LIMIT = 100

def google_search(query: str, num: int = 5):
    if not GOOGLE_API_KEY or not GOOGLE_CSE_CX:
        return []
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_CX, "q": query, "num": min(num, 10), "safe": "off"}
    r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=12)
    st.session_state["request_count"] += 1
    if r.status_code != 200: return []
    return r.json().get("items", []) or []

def fetch_url_text(url: str, timeout: int = 12) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200: return ""
        txt = re.sub(r"<[^>]+>", " ", r.text, flags=re.DOTALL)
        return re.sub(r"\s+", " ", txt)
    except Exception: return ""

def lookup(mode: str, sku: str, name: str, query_status, max_urls: int = 5):
    if mode == "Doar SKU":
        queries = [f'"{sku}" ean', f'"{sku}" gtin']
    else:
        queries = [f'"{name}" ean', f'"{name}" gtin']
    texts = []
    for q in queries:
        query_status.write(f"Query trimis: {q}")
        items = google_search(q, num=max_urls)
        for rank, it in enumerate(items):
            w = 1.0 + (max_urls - rank) * 0.1
            texts.append((it.get("snippet",""), w))
            link = it.get("link")
            if link:
                page = fetch_url_text(link)
                if page: texts.append((page, w+0.5))
        best = choose_best_ean(texts)
        if best: return best
    return choose_best_ean(texts)

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="EANs")
    return output.getvalue()

# === Upload în Google Drive ===
def upload_to_drive(file_bytes: bytes, filename: str, mimetype: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
    media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype=mimetype, resumable=False)
    file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    f = drive_service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink, webContentLink").execute()
    return f

# === Interfața ===
st.sidebar.header("Quota Google API")
st.sidebar.write("Requests in session:", st.session_state["request_count"])
st.sidebar.write("Estimated daily free limit:", DAILY_LIMIT)

uploaded = st.file_uploader("Încarcă fișier Excel", type=["xls", "xlsx"])
if uploaded:
    try:
        df = pd.read_excel(uploaded, engine="openpyxl")
    except Exception as e:
        st.error(f"Eroare la citirea Excel: {e}")
        st.stop()

    st.write("Previzualizare:", df.head(10))
    cols = list(df.columns)
    col_sku = st.selectbox("Coloană SKU", cols, index=0)
    col_name = st.selectbox("Coloană Denumire", cols, index=1 if len(cols)>1 else 0)
    col_target = st.selectbox("Coloană țintă pentru EAN-13", cols, index=len(cols)-1)
    mode = st.radio("Cum cauți EAN?", ["Doar SKU", "Doar Nume"])

    mode_rows = st.radio("Ce rânduri procesezi?", ["Primele N rânduri", "Toate rândurile"])
    if mode_rows == "Primele N rânduri":
        max_rows = st.number_input("N rânduri de procesat", 1, len(df), min(50,len(df)))
    else:
        max_rows = len(df)

    if st.button("Pornește căutarea EAN"):
        done = 0; bar = st.progress(0); status = st.empty(); query_status = st.empty()
        for idx, row in df.head(int(max_rows)).iterrows():
            sku, name = str(row.get(col_sku,"")).strip(), str(row.get(col_name,"")).strip()
            current = str(row.get(col_target,"")).strip()

            if current and (is_valid_ean13(current) or current.upper()=="NOT_FOUND"):
                done+=1; bar.progress(int(done*100/max_rows)); continue

            found = lookup(mode, sku, name, query_status)
            if found and is_valid_ean13(found):
                df.at[idx, col_target] = found
            else:
                df.at[idx, col_target] = "NOT_FOUND"

            done+=1
            if done%5==0: status.write(f"Procesate: {done}/{int(max_rows)}")
            bar.progress(int(done*100/max_rows)); time.sleep(0.2)

        st.success(f"Terminat. Rânduri procesate: {done}.")
        excel_data = to_excel_bytes(df)
        uploaded_file = upload_to_drive(excel_data, "output_ean.xlsx")

        st.write("📂 Fișier salvat pe Google Drive:")
        st.write("⬇️ [Descarcă direct](" + uploaded_file.get("webContentLink") + ")")
        st.write("🌐 [Vezi în Drive](" + uploaded_file.get("webViewLink") + ")")
