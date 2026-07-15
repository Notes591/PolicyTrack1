# -*- coding: utf-8 -*-
import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import requests
import xml.etree.ElementTree as ET
import re
from streamlit_autorefresh import st_autorefresh
import pandas as pd
import time
from deep_translator import GoogleTranslator

# ====== إعداد الصفحة ======
st.set_page_config(page_title="📦 تتبع الشحنات - Bulky Noon", page_icon="🚚", layout="wide")
st.title("🚚 نظام تتبع الشحنات (Bulky Noon)")
st_autorefresh(interval=600000, key="auto_refresh")

# ====== الاتصال بجوجل شيت ======
@st.cache_resource
def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets",
             "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        st.secrets["gcp_service_account"], scope)
    return gspread.authorize(creds)

client = get_gspread_client()

# ====== أسماء الشيتات ======
SHEET_NAME        = "Complaints"
POLICY_SHEET      = "bulkyfitnessnoon"
DELIVERED_SHEET   = "تم التسليم بلكي نون"
RETURNED_SHEET    = "تم الارجاع بلكي نون"
ORDERS_SHEET      = "Delivery of shipments"
DELIVERED_ARCHIVE = "Delivered Archive bulky noon"
RETURNED_ARCHIVE  = "Returned Archive bulky noon"

def get_or_create_sheet(name):
    try:
        return client.open(SHEET_NAME).worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = client.open(SHEET_NAME).add_worksheet(title=name, rows="100", cols="10")
        ws.append_row(["Order Number", "Policy Number", "Date", "Status", "Days Since Shipment", "حالة الشحن"])
        return ws

try:
    policy_sheet = client.open(SHEET_NAME).worksheet(POLICY_SHEET)
except Exception as e:
    st.error(f"❌ لا يمكن فتح الشيت الرئيسي: {e}")
    st.stop()

delivered_sheet         = get_or_create_sheet(DELIVERED_SHEET)
returned_sheet          = get_or_create_sheet(RETURNED_SHEET)
delivered_archive_sheet = get_or_create_sheet(DELIVERED_ARCHIVE)
returned_archive_sheet  = get_or_create_sheet(RETURNED_ARCHIVE)

# ====== شيت الطلبات ======
try:
    order_sheet = client.open(SHEET_NAME).worksheet(ORDERS_SHEET)
    order_data  = order_sheet.get_all_values()
    # العمود الثاني (index=1) يحتوي على رقم الشحنة
    order_dict  = {
        row[1].strip(): True
        for row in order_data[1:]
        if len(row) > 1 and row[1].strip()
    }
except Exception:
    order_dict = {}

# ====== حسابا Aramex ======
ARAMEX_ACCOUNTS = [
    {
        "label":              "الحساب الأول",
        "UserName":           "fitnessworld525@gmail.com",
        "Password":           "Aa12345678@",
        "Version":            "v1",
        "AccountNumber":      "71958996",
        "AccountPin":         "657448",
        "AccountEntity":      "RUH",
        "AccountCountryCode": "SA"
    },
    {
        "label":              "الحساب الثاني",
        "UserName":           "homeentryh5556@gmail.com",
        "Password":           "Aa12345678@",
        "Version":            "v1",
        "AccountNumber":      "4004297",
        "AccountPin":         "216216",
        "AccountEntity":      "RUH",
        "AccountCountryCode": "SA"
    }
]

# ====== دوال Aramex ======
def remove_xml_namespaces(xml_str):
    xml_str = re.sub(r'xmlns(:\w+)?="[^"]+"', '', xml_str)
    xml_str = re.sub(r'(<\/?)(\w+:)', r'\1', xml_str)
    return xml_str

def _fetch_aramex_status(awb_number, account):
    """جلب الحالة من حساب واحد. يرجع نص أو None لو مفيش بيانات."""
    try:
        client_info = {k: v for k, v in account.items() if k != "label"}
        payload = {
            "ClientInfo": client_info,
            "Shipments":  [awb_number],
            "Transaction": {"Reference1": "", "Reference2": "", "Reference3": "", "Reference4": "", "Reference5": ""},
            "LabelInfo":  None
        }
        url = "https://ws.aramex.net/ShippingAPI.V2/Tracking/Service_1_0.svc/json/TrackShipments"
        resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
        if resp.status_code != 200:
            return None

        xml_content = remove_xml_namespaces(resp.content.decode('utf-8'))
        root = ET.fromstring(xml_content)

        tracking_results = root.find('TrackingResults')
        if tracking_results is None:
            return None

        keyvalue = tracking_results.find('KeyValueOfstringArrayOfTrackingResultmFAkxlpY')
        if keyvalue is None:
            return None

        tracking_array = keyvalue.find('Value')
        if tracking_array is None:
            return None

        tracks = tracking_array.findall('TrackingResult')
        if not tracks:
            return None  # لا بيانات → جرّب الحساب الثاني

        last_track = sorted(
            tracks,
            key=lambda tr: tr.find('UpdateDateTime').text if tr.find('UpdateDateTime') is not None else '',
            reverse=True
        )[0]

        desc_en = last_track.find('UpdateDescription').text if last_track.find('UpdateDescription') is not None else "—"
        if not desc_en or desc_en == "—":
            return None

        try:
            desc_ar = GoogleTranslator(source='en', target='ar').translate(desc_en)
        except:
            desc_ar = "—"

        return f"{desc_en} - {desc_ar}"

    except Exception:
        return None


def get_aramex_status(awb_number):
    """يجرب الحسابين بالترتيب ويرجع أول نتيجة."""
    for account in ARAMEX_ACCOUNTS:
        result = _fetch_aramex_status(awb_number, account)
        if result:
            return result
    return "❌ لا توجد حالة متاحة"


# ====== دوال مساعدة ======
def check_status(status_text):
    text = (status_text or "").lower()
    delivered_kw = ["delivered", "تم التسليم", "shipment charges paid",
                    "customer id received", "collected by consignee"]
    returned_kw  = ["returned", "تم الإرجاع", "returned to shipper"]
    for k in delivered_kw:
        if k in text:
            return "delivered"
    for k in returned_kw:
        if k in text:
            return "returned"
    return "other"


def calc_days(date_str):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return (datetime.now() - datetime.strptime(date_str.strip(), fmt)).days
        except:
            continue
    return 0


def normalize_rows(rows, n=6):
    result = []
    for r in rows:
        r = list(r)[:n]
        r += ["—"] * (n - len(r))
        result.append(r)
    return result


def append_in_batches(sheet, rows, batch_size=20):
    for i in range(0, len(rows), batch_size):
        try:
            sheet.append_rows(rows[i:i+batch_size], value_input_option='USER_ENTERED')
        except Exception:
            for row in rows[i:i+batch_size]:
                try:
                    sheet.append_row(row, value_input_option='USER_ENTERED')
                    time.sleep(0.2)
                except Exception:
                    pass
        time.sleep(1)


def delete_policy_row_by_awb(awb):
    """حذف الصف من policy_sheet بناءً على رقم البوليصة (عمود B)."""
    try:
        all_vals = policy_sheet.get_all_values()
        for i, row in enumerate(all_vals[1:], start=2):
            if len(row) > 1 and row[1].strip() == awb.strip():
                policy_sheet.delete_rows(i)
                time.sleep(0.3)
                break
    except Exception:
        pass

# ====== تحميل البيانات ======
try:
    policy_data = policy_sheet.get_all_values()
except Exception as e:
    st.error(f"❌ خطأ في قراءة الشيت: {e}")
    st.stop()

# ====== تحديث عمودَي الأيام (E) وحالة الشحن (F) ======
if len(policy_data) >= 2:
    cells_e = policy_sheet.range(f'E2:E{len(policy_data)}')
    cells_f = policy_sheet.range(f'F2:F{len(policy_data)}')
    for idx, row in enumerate(policy_data[1:]):
        if len(row) < 6:
            row += [""] * (6 - len(row))
        days = calc_days(row[2]) if len(row) > 2 else 0
        row[4] = days
        cells_e[idx].value = days
        # المطابقة بـ Policy Number (عمود B = index 1)
        row[5] = "مشحون" if str(row[1]).strip() in order_dict else "غير مشحون"
        cells_f[idx].value = row[5]
    try:
        policy_sheet.update_cells(cells_e)
        policy_sheet.update_cells(cells_f)
    except Exception as e:
        st.warning(f"تحذير: لم يتم تحديث الأعمدة: {e}")

# ====== واجهة البحث ======
st.header("🔍 البحث عن شحنة")
search_order = st.text_input("أدخل رقم الطلب للبحث", placeholder="مثال: 12345")
if search_order.strip():
    found = next((r for r in policy_data[1:] if len(r) >= 1 and str(r[0]) == search_order.strip()), None)
    if found:
        while len(found) < 6:
            found.append("—")
        col1, col2, col3 = st.columns(3)
        col1.success(f"✅ رقم الطلب: {found[0]}")
        col2.info(f"📦 رقم الشحنة: {found[1]}")
        col3.write(f"📅 التاريخ: {found[2]}")
        st.write(f"🔄 الحالة: {found[3]}")
        st.write(f"⏳ أيام منذ الشحن: {found[4]}")
        st.write(f"🚚 حالة الشحن: {found[5]}")
    else:
        st.error("⚠️ لم يتم العثور على الطلب")

st.markdown("---")

# ====== زر تحديث الحالات ======
if st.button("🔄 تحديث جميع الحالات الآن", use_container_width=True):
    progress   = st.progress(0)
    status_msg = st.empty()
    total      = max(len(policy_data) - 1, 1)

    for idx, row in enumerate(policy_data[1:], start=2):
        if len(row) < 4:
            row += [""] * (4 - len(row))
        if row[1].strip() and check_status(row[3]) == "other":
            status_msg.info(f"جاري تحديث {idx-1}/{total}: {row[1]}")
            row[3] = get_aramex_status(row[1])
        progress.progress((idx - 1) / total)

    # حفظ عمود D
    try:
        cells_d = policy_sheet.range(f'D2:D{len(policy_data)}')
        for i, row in enumerate(policy_data[1:]):
            cells_d[i].value = row[3] if len(row) > 3 else "—"
        policy_sheet.update_cells(cells_d)
    except Exception as e:
        st.warning(f"تحذير: لم يتم حفظ الحالات: {e}")

    # نقل المُسلَّم والمُرجَع
    try:
        delivered_existing = {r[1] for r in delivered_sheet.get_all_values()[1:] if len(r) > 1}
        returned_existing  = {r[1] for r in returned_sheet.get_all_values()[1:]  if len(r) > 1}
    except Exception:
        delivered_existing, returned_existing = set(), set()

    new_delivered, new_returned = [], []
    for row in policy_data[1:]:
        if len(row) < 2 or not row[1].strip():
            continue
        flag = check_status(row[3] if len(row) > 3 else "")
        if flag == "delivered" and row[1] not in delivered_existing:
            new_delivered.append(row[:6])
        elif flag == "returned" and row[1] not in returned_existing:
            new_returned.append(row[:6])

    if new_delivered:
        append_in_batches(delivered_sheet, new_delivered)
        append_in_batches(delivered_archive_sheet, new_delivered)
        for r in new_delivered:
            delete_policy_row_by_awb(r[1])

    if new_returned:
        append_in_batches(returned_sheet, new_returned)
        append_in_batches(returned_archive_sheet, new_returned)
        for r in new_returned:
            delete_policy_row_by_awb(r[1])

    # إعادة تحميل
    try:
        policy_data = policy_sheet.get_all_values()
    except Exception:
        pass

    status_msg.empty()
    st.success(f"✅ تم التحديث | نُقل للتسليم: {len(new_delivered)} | نُقل للإرجاع: {len(new_returned)}")

# ====== إحصائيات ======
st.markdown("---")

def get_days_val(r):
    return int(str(r[4]).strip()) if len(r) > 4 and str(r[4]).strip().lstrip('-').isdigit() else 0

all_active      = [r for r in policy_data[1:] if check_status(r[3] if len(r) > 3 else "") == "other"]
delayed_display = [r for r in all_active if get_days_val(r) > 3]
current_display = [r for r in all_active if get_days_val(r) <= 3]

# غير مشحون من عندنا وعليه 3 أيام فأكثر (بيتفحص على كل الشحنات النشطة مش بس المتأخرة حسب حالة أرامكس)
not_shipped_display = [
    r for r in policy_data[1:]
    if len(r) >= 6 and r[5].strip() == "غير مشحون" and get_days_val(r) >= 3
]

# مشحون عندنا لكن لسه واقف/متحركش فى أرامكس (حالته "other") وعليه 3 أيام فأكثر
stuck_display = [
    r for r in all_active
    if len(r) >= 6 and r[5].strip() == "مشحون" and get_days_val(r) >= 3
]

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("📦 إجمالي النشطة", len(all_active))
col2.metric("⚠️ متأخرة (+3 أيام)", len(delayed_display))
col3.metric("✅ في الوقت",         len(current_display))
col4.metric("🚫 غير مشحون (+3 أيام)", len(not_shipped_display))
col5.metric("🐌 عالقة بأرامكس (+3 أيام)", len(stuck_display))

COLS = ["Order Number", "Policy Number", "Date", "Status", "Days Since Shipment", "حالة الشحن"]

st.markdown("---")
tab_main, tab_not_shipped, tab_stuck = st.tabs([
    "🏠 الرئيسية",
    "🚫 غير مشحون (+3 أيام)",
    "🐌 عالقة بأرامكس (+3 أيام)"
])

with tab_main:
    st.subheader("⚠️ الشحنات المتأخرة (أكثر من 3 أيام)")
    if delayed_display:
        st.dataframe(
            pd.DataFrame(normalize_rows(delayed_display), columns=COLS),
            use_container_width=True, height=400
        )
    else:
        st.success("✅ لا توجد شحنات متأخرة!")

    st.markdown("---")
    st.subheader("📦 الشحنات الحالية")
    if current_display:
        st.dataframe(
            pd.DataFrame(normalize_rows(current_display), columns=COLS),
            use_container_width=True, height=400
        )
    else:
        st.info("لا توجد شحنات حالياً.")

with tab_not_shipped:
    st.subheader("🚫 شحنات لم تُشحن من عندنا بعد (3 أيام فأكثر)")
    if not_shipped_display:
        st.dataframe(
            pd.DataFrame(normalize_rows(not_shipped_display), columns=COLS),
            use_container_width=True, height=400
        )
    else:
        st.success("✅ لا توجد شحنات متأخرة فى الشحن!")

with tab_stuck:
    st.subheader("🐌 شحنات مشحونة ولم تتحرك فى أرامكس (3 أيام فأكثر)")
    if stuck_display:
        st.dataframe(
            pd.DataFrame(normalize_rows(stuck_display), columns=COLS),
            use_container_width=True, height=400
        )
    else:
        st.success("✅ لا توجد شحنات عالقة!")

st.caption(f"آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
