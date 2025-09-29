# -*- coding: utf-8 -*-
import re, time
import pandas as pd
import streamlit as st
import requests
from io import BytesIO

st.set_page_config(page_title="GTIN/EAN Finder via Google CSE (Excel)", layout="wide")
st.title("GTIN/EAN Finder via Google CSE (Excel)")

GOOGLE_API_KEY = st.secrets.get("GOOGLE_API_KEY")
GOOGLE_CSE_CX = st.secrets.get("GOOGLE_CSE_CX")

# counter requests
if "request_count" not in st.session_state:
    st.session_state["request_count"] = 0
DAILY_LIMIT = 100  # free tier guideline

def clean_digits(s: str) -> str:
    return re.sub(r"[^0-9]", "", s or "")

def ean13_check_digit(d12: str) -> int:
    assert len(d12) == 12 and d12.isdigit()
    s = 0
    for i, ch in enumerate(d12):
        n = ord(ch) - 48
        s += n * (3 if (i % 2 == 1) else 1)
    return (10 - (s % 10)) % 10

def is_valid_ean13(code: str) -> bool:
    code = clean_digits(code)
    if len(code) != 13:
        return False
    cd = int(code[-1])
    return ean13_check_digit(code[:12]) == cd

def upc12_to_gtin13(upc: str):
    d = clean_digits(upc)
    if len(d) != 12:
        return None
    candidate = "0" + d
    return candidate if is_valid_ean13(candidate) else None

EAN_RE = re.compile(r"\b(?:\d[ \t\-]?){12,14}\b")

def find_eans_in_text(text: str):
    out = []
    for m in EAN_RE.finditer(text or ""):
        digits = clean_digits(m.group(0))
        if len(digits) == 13 and is_valid_ean13(digits):
            out.append(digits)
        elif len(digits) == 12:
            gt = upc12_to_gtin13(digits)
            if gt:
                out.append(gt)
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            uniq.append(x); seen.add(x)
    return uniq

def choose_best_ean(texts_with_weights):
    scores = {}
    for text, w in texts_with_weights:
        codes = find_eans_in_text(text)
        for c in codes:
            base = 1.0
            if re.search(r"(ean|gtin|barcode|cod\s*ean|ean-13)", text.lower()):
                base += 1.0
            scores[c] = scores.get(c, 0.0) + base * w
    if not scores:
        return None
    return max(scores.items(), key=lambda kv: kv[1])[0]

def google_search(query: str, num: int = 5):
    if not GOOGLE_API_KEY or not GOOGLE_CSE_CX:
        return []
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_CX, "q": query, "num": min(num, 10), "safe": "off"}
    try:
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=12)
        st.session_state["request_count"] += 1
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("items", []) or []
    except Exception:
        return []

def fetch_url_text(url: str, timeout: int = 12) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        txt = re.sub(r"<[^>]+>", " ", r.text, flags=re.DOTALL)
        return re.sub(r"\s+", " ", txt)
    except Exception:
        return ""

def lookup(mode: str, sku: str, name: str, max_urls: int = 5):
    if mode == "Doar SKU":
        queries = [f'"{sku}" ean', f'"{sku}" gtin', f'"{sku}" cod ean']
    else:  # Doar Nume
        queries = [f'"{name}" ean', f'"{name}" gtin']
    texts = []
    for q in queries:
        items = google_search(q, num=max_urls)
        for rank, it in enumerate(items):
            w = 1.0 + (max_urls - rank) * 0.1
            snippet = it.get("snippet", ""); link = it.get("link")
            texts.append((snippet, w))
            if link:
                page = fetch_url_text(link)
                if page:
                    texts.append((page, w + 0.5))
        best = choose_best_ean(texts)
        if best:
            return best
    return choose_best_ean(texts)

def to_excel_bytes(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="EANs")
    return output.getvalue()

# Sidebar quota
st.sidebar.header("Quota Google API")
st.sidebar.write(f"Requests in session: {st.session_state['request_count']}")
st.sidebar.write(f"Estimated daily free limit: {DAILY_LIMIT}")
remaining = max(0, DAILY_LIMIT - st.session_state["request_count"])
st.sidebar.write(f"Remaining (est.): {remaining}")

uploaded = st.file_uploader("Încarcă fișier Excel", type=["xls", "xlsx"])
if uploaded is not None:
    try:
        df = pd.read_excel(uploaded, engine="openpyxl")
    except Exception as e:
        st.error(f"Eroare la citirea Excel: {e}")
        st.stop()

    st.write("Previzualizare:", df.head(10))

    cols = list(df.columns)
    col_sku = st.selectbox("Coloană SKU", cols, index=0)
    col_name = st.selectbox("Coloană Denumire", cols, index=1 if len(cols) > 1 else 0)
    col_target = st.selectbox("Coloană țintă pentru EAN-13", cols, index=len(cols)-1)
    mode = st.radio("Cum cauți EAN?", ["Doar SKU", "Doar Nume"])
    max_rows = st.number_input("Procesează maximum N rânduri", min_value=1, max_value=len(df), value=min(50, len(df)))

    if st.button("Pornește căutarea EAN"):
        done = 0
        bar = st.progress(0)
        status = st.empty()
        for idx, row in df.head(int(max_rows)).iterrows():
            sku = str(row.get(col_sku, "")).strip()
            name = str(row.get(col_name, "")).strip()
            current = str(row.get(col_target, "")).strip()

            # Skip dacă există deja ceva în coloană (inclusiv NOT_FOUND)
            if current:
                done += 1
                bar.progress(int(done * 100 / max_rows))
                continue

            found = lookup(mode, sku, name)
            if found and is_valid_ean13(found):
                df.at[idx, col_target] = found
            else:
                df.at[idx, col_target] = "NOT_FOUND"

            done += 1
            if done % 5 == 0:
                status.write(f"Procesate: {done}/{int(max_rows)}")
            bar.progress(int(done * 100 / max_rows))
            time.sleep(0.2)

        st.success(f"Terminat. Rânduri procesate: {done}.")
        st.download_button(
            "Descarcă Excel completat",
            data=to_excel_bytes(df),
            file_name="output_ean.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with st.expander("Teste rapide validator EAN"):
    samples = ["5903396373473", "4006381333931", "036000291452", "1234567890128"]
    rows = []
    for s in samples:
        d = clean_digits(s)
        valid = is_valid_ean13(d)
        conv = upc12_to_gtin13(d) if len(d) == 12 else ""
        rows.append({"input": s, "digits": d, "is_valid_ean13": valid, "upc_to_gtin13": conv})
    st.dataframe(pd.DataFrame(rows))
