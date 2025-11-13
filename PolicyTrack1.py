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

# ====== إعداد صفحة Streamlit أولاً ======
st.set_page_config(page_title="📦 تتبع الشحنات", page_icon="🚚", layout="wide")
st.title("🚚 نظام تتبع الشحنات (Policy number)")

# ====== تحديث تلقائي كل 10 دقائق ======
st_autorefresh(interval=600000, key="auto_refresh")

# ====== إعداد الاتصال بجوجل شيت ======
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds_dict = st.secrets["gcp_service_account"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# ====== شيتات Google ======
SHEET_NAME = "Complaints"
POLICY_SHEET = "bulkyfitnessnoon"
DELIVERED_SHEET = "تم التسليم بلكي نون"
RETURNED_SHEET = " تم الارجاع بلكي نون"
ORDERS_SHEET = "Delivery of shipments"  # ← تم التعديل هنا
DELIVERED_ARCHIVE = "Delivered Archive bulky noon"
RETURNED_ARCHIVE = "Returned Archive bulky noon"

# ====== تحميل أو إنشاء أوراق Google ======
def get_or_create_sheet(sheet_name):
    try:
        sheet = client.open(SHEET_NAME).worksheet(sheet_name)
        return sheet
    except gspread.exceptions.WorksheetNotFound:
        sheet = client.open(SHEET_NAME).add_worksheet(title=sheet_name, rows="100", cols="10")
        sheet.append_row(["Order Number", "Policy Number", "Date", "Status", "Days Since Shipment"])
        return sheet

policy_sheet = client.open(SHEET_NAME).worksheet(POLICY_SHEET)
delivered_sheet = get_or_create_sheet(DELIVERED_SHEET)
returned_sheet = get_or_create_sheet(RETURNED_SHEET)
delivered_archive_sheet = get_or_create_sheet(DELIVERED_ARCHIVE)
returned_archive_sheet = get_or_create_sheet(RETURNED_ARCHIVE)

# ====== تحميل شيت Delivery of shipments ======
order_sheet = client.open(SHEET_NAME).worksheet(ORDERS_SHEET)
order_data = order_sheet.get_all_values()

# نفترض أن العمود الثاني يحتوي على "رقم الشحنة"
order_awb_col_index = 1  # ← رقم العمود (0 = أول عمود، 1 = ثاني عمود)
order_dict = {row[order_awb_col_index].strip(): True for row in order_data[1:] if len(row) > order_awb_col_index and row[order_awb_col_index].strip()}

# ====== بيانات Aramex ======
client_info = {
    "UserName": "homeentryh5556@gmail.com",
    "Password": "Aa12345678@",
    "Version": "v1",
    "AccountNumber": "4004297",
    "AccountPin": "216216",
    "AccountEntity": "RUH",
    "AccountCountryCode": "SA"
}

# ====== دوال مساعدة ======
def remove_xml_namespaces(xml_str):
    xml_str = re.sub(r'xmlns(:\w+)?="[^"]+"', '', xml_str)
    xml_str = re.sub(r'(<\/?)(\w+:)', r'\1', xml_str)
    return xml_str

def get_aramex_status(awb_number):
    try:
        headers = {"Content-Type": "application/json"}
        payload = {
            "ClientInfo": client_info,
            "Shipments": [awb_number],
            "Transaction": {"Reference1": "", "Reference2": "", "Reference3": "", "Reference4": "", "Reference5": ""},
            "LabelInfo": None
        }
        url = "https://ws.aramex.net/ShippingAPI.V2/Tracking/Service_1_0.svc/json/TrackShipments"
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            return f"❌ فشل الاتصال ({response.status_code})"

        xml_content = response.content.decode('utf-8')
        xml_content = remove_xml_namespaces(xml_content)
        root = ET.fromstring(xml_content)
        tracking_results = root.find('TrackingResults')
        if tracking_results is None or len(tracking_results) == 0:
            return "❌ لا توجد حالة متاحة"

        keyvalue = tracking_results.find('KeyValueOfstringArrayOfTrackingResultmFAkxlpY')
        if keyvalue is not None:
            tracking_array = keyvalue.find('Value')
            if tracking_array is not None:
                tracks = tracking_array.findall('TrackingResult')
                if tracks:
                    last_track = sorted(tracks, key=lambda tr: tr.find('UpdateDateTime').text if tr.find('UpdateDateTime') is not None else '', reverse=True)[0]
                    desc_en = last_track.find('UpdateDescription').text if last_track.find('UpdateDescription') is not None else "—"
                    try:
                        desc_ar = GoogleTranslator(source='en', target='ar').translate(desc_en)
                    except:
                        desc_ar = "—"
                    return f"{desc_en} - {desc_ar}"
        return "❌ لا توجد حالة متاحة"
    except Exception as e:
        return f"⚠️ خطأ في جلب الحالة: {e}"

# ====== تحميل بيانات الشيت ======
policy_data = policy_sheet.get_all_values()

# ====== تحديث أيام الشحن وحالة الشحن ======
cells = policy_sheet.range(f'E2:E{len(policy_data)}')
for idx, row in enumerate(policy_data[1:]):
    if len(row) < 6:
        row += ["0", "غير معروف"] * (6 - len(row))
    date_added_str = row[2] if len(row) > 2 else None
    days_diff = 0
    if date_added_str and date_added_str.strip():
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                date_added = datetime.strptime(date_added_str, fmt)
                days_diff = (datetime.now() - date_added).days
                break
            except:
                continue
    row[4] = days_diff
    cells[idx].value = days_diff
    policy_num = str(row[1])  # ← تعديل: نستخدم Policy Number للمطابقة
    row[5] = "مشحون" if policy_num in order_dict else "غير مشحون"
policy_sheet.update_cells(cells)

# ====== البحث عن شحنة ======
st.header("🔍 البحث عن شحنة")
search_order = st.text_input("أدخل رقم الطلب للبحث")
if search_order.strip():
    found = False
    for row in policy_data[1:]:
        if len(row) >= 2 and str(row[0]) == search_order:
            found = True
            st.success(f"✅ تم العثور على الطلب رقم: {search_order}")
            st.info(f"📦 رقم الشحنة: {row[1]}")
            st.write(f"📅 التاريخ: {row[2] if len(row) > 2 else '—'}")
            st.write(f"🔄 الحالة الحالية: {row[3] if len(row) > 3 else '—'}")
            st.write(f"⏳ أيام منذ الشحن: {row[4] if len(row) > 4 else '—'}")
            st.write(f"🚚 حالة الشحن: {row[5] if len(row) > 5 else 'غير معروف'}")
            break
    if not found:
        st.error("⚠️ لم يتم العثور على الطلب في الشيت")

# ====== دالة تصنيف الحالة ======
def check_status(status_text):
    text = status_text.lower()
    delivered_conditions = ["delivered","تم التسليم","shipment charges paid","customer id received","collected by consignee"]
    returned_conditions = ["returned","تم الإرجاع","returned to shipper"]
    for cond in delivered_conditions:
        if cond in text:
            return "delivered"
    for cond in returned_conditions:
        if cond in text:
            return "returned"
    return "other"

# ====== تحديث جميع الحالات ======
if st.button("تحديث جميع الحالات الآن"):
    progress = st.progress(0)
    for idx, row in enumerate(policy_data[1:], start=2):
        if len(row) >= 2 and row[1].strip():
            if check_status(row[3]) == "other":
                new_status = get_aramex_status(row[1])
                row[3] = new_status
        progress.progress(idx / len(policy_data))
    # تحديث العمود دفعة واحدة
    cells = policy_sheet.range(f'D2:D{len(policy_data)}')
    for idx, row in enumerate(policy_data[1:]):
        cells[idx].value = row[3]
    policy_sheet.update_cells(cells)
    st.success("✅ تم تحديث جميع الحالات")

# ====== تصحيح الصفوف قبل إنشاء DataFrame ======
def normalize_rows(data, num_columns):
    normalized = []
    for row in data:
        row = row[:num_columns]
        row += ["—"] * (num_columns - len(row))
        normalized.append(row)
    return normalized

# ====== تصنيف البيانات لعرضها ======
delayed_shipments = [row for row in policy_data[1:] if int(row[4]) > 3 and check_status(row[3]) == "other"]
current_shipments = [row for row in policy_data[1:] if int(row[4]) <= 3 and check_status(row[3]) == "other"]
delayed_shipments = normalize_rows(delayed_shipments, 6)
current_shipments = normalize_rows(current_shipments, 6)

# ====== دالة إضافة الصفوف دفعة ======
def append_in_batches(sheet, rows, batch_size=20):
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        sheet.append_rows(batch, value_input_option='USER_ENTERED')
        time.sleep(1)

# ====== تحديث تبويبات التسليم والإرجاع والأرشيف ======
delivered_shipments = [row for row in delivered_sheet.get_all_values()[1:]]
returned_shipments = [row for row in returned_sheet.get_all_values()[1:]]

new_delivered = [row[:5] for row in policy_data[1:] if check_status(row[3]) == "delivered" and row[1] not in [r[1] for r in delivered_shipments]]
new_returned = [row[:5] for row in policy_data[1:] if check_status(row[3]) == "returned" and row[1] not in [r[1] for r in returned_shipments]]

if new_delivered:
    append_in_batches(delivered_sheet, new_delivered)
    append_in_batches(delivered_archive_sheet, new_delivered)
    for row in new_delivered:
        for i, r in enumerate(policy_data[1:], start=2):
            if r[1] == row[1]:
                policy_sheet.delete_rows(i)
                break

if new_returned:
    append_in_batches(returned_sheet, new_returned)
    append_in_batches(returned_archive_sheet, new_returned)
    for row in new_returned:
        for i, r in enumerate(policy_data[1:], start=2):
            if r[1] == row[1]:
                policy_sheet.delete_rows(i)
                break

# ====== عرض الجداول ======
st.markdown("---")
st.subheader("الشحنات المتأخرة")
if delayed_shipments:
    st.dataframe(pd.DataFrame(delayed_shipments, columns=["Order Number","Policy Number","Date","Status","Days Since Shipment","حالة الشحن"]), use_container_width=True)
else:
    st.info("لا توجد شحنات متأخرة حالياً.")

st.markdown("---")
st.subheader("📦 الشحنات الحالية")
if current_shipments:
    st.dataframe(pd.DataFrame(current_shipments, columns=["Order Number","Policy Number","Date","Status","Days Since Shipment","حالة الشحن"]), use_container_width=True)
else:
    st.info("لا توجد شحنات حالياً.")
