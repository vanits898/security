import os

# --- ซ่อน Log ข้อความแจ้งเตือน C++ จาก OpenCV / TensorFlow / MediaPipe ---
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["GLOG_minloglevel"] = "3"

import sys
import asyncio
import concurrent.futures
import gc
import json
import logging
import queue
import re
import sqlite3
import ssl
import subprocess
import threading
import time
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
import cv2
import easyocr
import qrcode
import requests
import torch
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, request, render_template_string, jsonify
from pyngrok import ngrok, conf
from ultralytics import YOLO

# --- นำเข้า MediaPipe ---
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# --- นำเข้า Telegram Bot SDK ---
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

warnings.filterwarnings("ignore")
requests.packages.urllib3.disable_warnings()
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ปรับ Timeouts และ Buffer สำหรับ RTSP
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000|max_delay;500000"
os.environ["TORCHVISION_DISABLE_NMS"] = "1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR_ID_CARD = os.path.join(BASE_DIR, "id_cards")
SAVE_DIR_QR_CODE = os.path.join(BASE_DIR, "qr_codes")
DB_FILE = os.path.join(BASE_DIR, "id_cards.db")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

os.makedirs(SAVE_DIR_ID_CARD, exist_ok=True)
os.makedirs(SAVE_DIR_QR_CODE, exist_ok=True)

# ==========================================
# --- CONFIG PERSISTENCE (LOAD / SAVE JSON) ---
# ==========================================
DEFAULT_CONFIG = {
    "enable_attendance": True,
    "enable_fall_alert": True,
    "enable_fire_alert": True
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                for k, v in DEFAULT_CONFIG.items():
                    if k not in config:
                        config[k] = v
                return config
        except Exception as e:
            print(f"⚠️ อ่านไฟล์ config.json ล้มเหลว ใช้ค่าเริ่มต้นแทน: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ บันทึก config.json ล้มเหลว: {e}")


SYSTEM_CONFIG = load_config()

# ==========================================
# --- CONFIGURATION & CREDENTIALS ---
# ==========================================
TELEGRAM_BOT_TOKEN = "8781168264:AAEEJ0Wmm-KztXk6_vPj5G2s9JjKW8-DALM"
TELEGRAM_CHAT_ID = "-5323214630"
TELEGRAM_CHAT_ID_TOUR = "-1003902676802"

SERVER_PUBLIC_URL = "http://127.0.0.1:5000"

# --- รายชื่อกล้องชุดที่ 1 ---
CAMERAS_GROUP1_MOTION = [
    {"id": 101, "name": "กล้อง 01 - ทางออกหลัก", "rtsp": "rtsp://admin:admin123@172.16.3.42:554/stream1"},
    {"id": 103, "name": "กล้อง 03 - ทางออกwideangle 1", "rtsp": "rtsp://admin:admin123@172.16.3.44:554/stream1"}
]

CAMERAS_GROUP2_IDCARD = [
    {"id": 102, "name": "กล้อง 02 - ทางเข้าหลัก (เช็คดวงตา+บัตรประชาชนข้างตา)",
     "rtsp": "rtsp://admin:admin123@172.16.3.43:554/stream1"}
]

CAMERAS_GROUP3_SCHEDULED = [
    {"id": 104, "name": "กล้อง 04 - ห้อง รปภ 1", "ip": "172.16.3.41",
     "rtsp": "rtsp://admin:admin123@172.16.3.41:554/stream1"}
]

# --- รายชื่อกล้องชุดที่ 2 (การ์ดทัวร์ + จุดตรวจพิเศษ + Guardhouse) ---
PRIORITY_CAMERAS = [
    {"id": 1, "name": "จุดที่ 01 - รั้วติดถนน1", "rtsp": "rtsp://admin:admin123@172.16.3.24:554/stream1"},
    {"id": 2, "name": "จุดที่ 02 - รั้วติดถนน2 ", "rtsp": "rtsp://admin:admin123@172.16.3.25:554/stream1"},
    {"id": 3, "name": "จุดที่ 03 - รั้วติดถนน3", "rtsp": "rtsp://admin:admin123@172.16.3.26:554/stream1"},
]

NORMAL_CAMERAS = [
    {"id": 4, "name": "จุดที่ 04 - ซอยข้างโรงสีN", "rtsp": "rtsp://admin:admin123@172.16.3.27:554/stream1"},
    {"id": 5, "name": "จุดที่ 05 - ข้างบ้านพักคนงาน", "rtsp": "rtsp://admin:admin123@172.16.3.28:554/stream1"},
    {"id": 6, "name": "จุดที่ 06 - โรยรั้ง2", "rtsp": "rtsp://admin:admin123@172.16.3.29:554/stream1"},
    {"id": 7, "name": "จุดที่ 07 - โกดัง", "rtsp": "rtsp://admin:admin123@172.16.3.30:554/stream1"},
    {"id": 8, "name": "จุดที่ 08 - ถนนระหว่างโกดัง", "rtsp": "rtsp://admin:admin123@172.16.3.31:554/stream1"},
    {"id": 9, "name": "จุดที่ 09 - แนวรั้วสโกดัง C", "rtsp": "rtsp://admin:admin123@172.16.3.38:554/stream1"},
    {"id": 10, "name": "จุดที่ 10 - แนวรั้ว.", "rtsp": "rtsp://admin:admin123@172.16.3.40:554/stream1"},
    {"id": 11, "name": "จุดที่ 11 - 3แยกโกดัง5ลูก", "rtsp": "rtsp://admin:admin123@172.16.3.53:554/stream1"},
    {"id": 12, "name": "จุดที่ 12 - ชอปช่างไฟ", "rtsp": "rtsp://admin:admin123@172.16.3.54:554/stream1"},
    {"id": 13, "name": "จุดที่ 13 - ข้างอู่", "rtsp": "rtsp://admin:admin123@172.16.3.55:554/stream1"},
    {"id": 14, "name": "จุดที่ 14 - หน้าสโตร์", "rtsp": "rtsp://admin:admin123@172.16.3.57:554/stream1"},
    {"id": 15, "name": "จุดที่ 15 - ทางโหลดน้ำมัน", "rtsp": "rtsp://admin:admin123@172.16.3.58:554/stream1"},
    {"id": 16, "name": "จุดที่ 16 - ลานจอดหน้าโรงสี1", "rtsp": "rtsp://admin:admin123@172.16.3.63:554/stream1"},
    {"id": 17, "name": "จุดที่ 17 - ลานจอดรถหน้าสระ1", "rtsp": "rtsp://admin:admin123@172.16.3.64:554/stream1"},
    {"id": 18, "name": "จุดที่ 18 - ลานจอดโกดัง1-2", "rtsp": "rtsp://admin:admin123@172.16.3.65:554/stream1"},
    {"id": 20, "name": "จุดที่ 20 - หลังโกดัง5ลูก", "rtsp": "rtsp://admin:T%24g339339@172.16.3.80:554/stream1"},
    {"id": 21, "name": "จุดที่ 21 - จุดตรวจพิเศษ 21", "rtsp": None},
    {"id": 22, "name": "จุดที่ 22 - จุดตรวจพิเศษ 22", "rtsp": None},
    {"id": 23, "name": "จุดที่ 23 - จุดตรวจพิเศษ 23", "rtsp": None},
    {"id": 24, "name": "จุดที่ 24 - จุดตรวจพิเศษ 24", "rtsp": None},
    {"id": 25, "name": "จุดที่ 25 - จุดตรวจพิเศษ 25", "rtsp": None},
    {"id": 26, "name": "จุดที่ 26 - จุดตรวจพิเศษ 26", "rtsp": None},
    {"id": 27, "name": "จุดที่ 27 - จุดตรวจพิเศษ 27", "rtsp": None},
    {"id": 28, "name": "จุดที่ 28 - จุดตรวจพิเศษ 28", "rtsp": None},
    {"id": 29, "name": "จุดที่ 29 - จุดตรวจพิเศษ 29", "rtsp": None},
    {"id": 30, "name": "จุดที่ 30 - จุดตรวจพิเศษ 30", "rtsp": None},
    {"id": 31, "name": "Guardhouse 1 (จุดลงเวลาทำงาน)", "rtsp": None},
    {"id": 32, "name": "Guardhouse 2", "rtsp": None},
    {"id": 33, "name": "Guardhouse 3", "rtsp": None}
]

PERSON_ONLY_CAM_IDS = [1, 2, 3]
PERSON_VEHICLE_CLASSES = [0, 2, 3, 5, 7]

MIN_CONTOUR_AREA = 1500
COOLDOWN_SECONDS = 10
SLEEP_INTERVAL = 300
MAX_SLEEP_COUNT = 3
STATIONARY_THRESHOLD_PIXELS = 50

model_lock = threading.Lock()
state_lock = threading.Lock()

ALL_CAMERAS_SET1 = CAMERAS_GROUP1_MOTION + CAMERAS_GROUP2_IDCARD
ALL_CAMERAS_SET2 = PRIORITY_CAMERAS + NORMAL_CAMERAS
ALL_CAMERAS = ALL_CAMERAS_SET1 + ALL_CAMERAS_SET2

last_alert_time = {cam['id']: 0 for cam in ALL_CAMERAS}
sleeping_counters = {cam['id']: 0 for cam in CAMERAS_GROUP3_SCHEDULED}
no_person_counters = {cam['id']: 0 for cam in CAMERAS_GROUP3_SCHEDULED}

telegram_queue = queue.Queue()
ocr_reader = None
detector = None


# ==========================================
# --- DATABASE FUNCTIONS (THREAD SAFE) ---
# ==========================================
def get_db_connection():
    return sqlite3.connect(DB_FILE, timeout=30.0, check_same_thread=False)


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS id_card_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT,
            citizen_id TEXT,
            name_th TEXT,
            name_en TEXT,
            date_of_birth TEXT,
            address TEXT,
            scanned_by TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guard_tour_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT,
            point_id INTEGER,
            point_name TEXT,
            latitude REAL,
            longitude REAL,
            user_id TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            user_id TEXT,
            action_type TEXT,
            work_date TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guards (
            user_id TEXT PRIMARY KEY,
            guard_name TEXT NOT NULL
        )
    ''')

    try:
        cursor.execute("ALTER TABLE guard_tour_records ADD COLUMN user_id TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


def set_guard_name(user_id, guard_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO guards (user_id, guard_name) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET guard_name=excluded.guard_name
    ''', (str(user_id).strip(), guard_name.strip()))
    conn.commit()
    conn.close()


def delete_guard(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM guards WHERE user_id = ?', (str(user_id).strip(),))
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    return rows_affected > 0


def get_all_guards():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, guard_name FROM guards ORDER BY user_id ASC')
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_guard_display_name(user_id):
    clean_uid = str(user_id).strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT guard_name FROM guards WHERE user_id = ?', (clean_uid,))
    row = cursor.fetchone()
    conn.close()

    if row and row[0]:
        return f"{clean_uid} ({row[0]})"
    return clean_uid


def save_to_db(data_dict, scanned_by, current_time_str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO id_card_records (scanned_at, citizen_id, name_th, name_en, date_of_birth, address, scanned_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        current_time_str,
        data_dict['cid'],
        data_dict['th_name'],
        data_dict['en_name'],
        data_dict['dob'],
        data_dict['address'],
        scanned_by
    ))
    conn.commit()
    conn.close()


def get_recent_records(limit=100):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT scanned_at, citizen_id, name_th, scanned_by
        FROM id_card_records
        ORDER BY id DESC LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_latest_day_records(limit=100):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT DATE(scanned_at) FROM id_card_records ORDER BY id DESC LIMIT 1')
    latest_date_row = cursor.fetchone()

    if not latest_date_row or not latest_date_row[0]:
        conn.close()
        return None, []

    latest_date = latest_date_row[0]
    cursor.execute('''
        SELECT scanned_at, citizen_id, name_th, scanned_by
        FROM id_card_records
        WHERE DATE (scanned_at) = ?
        ORDER BY id DESC LIMIT ?
    ''', (latest_date, limit))
    rows = cursor.fetchall()
    conn.close()
    return latest_date, rows


def get_records_by_date(target_date_str, limit=100):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT scanned_at, citizen_id, name_th, scanned_by 
        FROM id_card_records 
        WHERE DATE(scanned_at) = ?
        ORDER BY id ASC LIMIT ?
    ''', (target_date_str, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_tour_records_by_date(target_date_str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT scanned_at, point_name, latitude, longitude, user_id
        FROM guard_tour_records
        WHERE DATE(scanned_at) = ?
        ORDER BY id ASC
    ''', (target_date_str,))
    rows = cursor.fetchall()
    conn.close()
    return rows


init_db()


# ==========================================
# --- HELPER: SHIFT & ATTENDANCE CALCULATIONS ---
# ==========================================
def calculate_checkin_status(dt):
    t = dt.time()
    hour = t.hour
    minute = t.minute

    if 3 <= hour < 10:
        shift_name = "กะกลางวันปกติ (06:00 - 18:00 น.)"
        start_dt = dt.replace(hour=6, minute=0, second=0, microsecond=0)
        late_threshold_dt = dt.replace(hour=6, minute=5, second=0, microsecond=0)

    elif 10 <= hour < 14:
        shift_name = "กะครึ่งวันกลางวัน (12:00 - 18:00 น.)"
        start_dt = dt.replace(hour=12, minute=0, second=0, microsecond=0)
        late_threshold_dt = dt.replace(hour=12, minute=5, second=0, microsecond=0)

    elif 14 <= hour < 17 or (hour == 17 and minute < 30):
        shift_name = "กะกลางคืนปกติ (18:00 - 06:00 น.)"
        start_dt = dt.replace(hour=18, minute=0, second=0, microsecond=0)
        late_threshold_dt = dt.replace(hour=18, minute=5, second=0, microsecond=0)

    elif (hour == 17 and minute >= 30) or (18 <= hour < 21):
        shift_name = "ควงกะหัวค่ำ (18:00 - 00:00 น.)"
        start_dt = dt.replace(hour=18, minute=0, second=0, microsecond=0)
        late_threshold_dt = dt.replace(hour=18, minute=5, second=0, microsecond=0)

    elif hour >= 21 or hour < 2:
        shift_name = "กะครึ่งวันกลางคืน (00:00 - 06:00 น.)"
        if hour >= 21:
            start_dt = (dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            late_threshold_dt = (dt + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        else:
            start_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            late_threshold_dt = dt.replace(hour=0, minute=5, second=0, microsecond=0)

    else:
        shift_name = "ควงกะเช้า (06:00 - 12:00 น.)"
        start_dt = dt.replace(hour=6, minute=0, second=0, microsecond=0)
        late_threshold_dt = dt.replace(hour=6, minute=5, second=0, microsecond=0)

    if dt < start_dt:
        early_mins = int((start_dt - dt).total_seconds() // 60)
        status_str = f"🟢 เข้าก่อนเวลา {early_mins} นาที"
        late_mins = 0
    elif dt <= late_threshold_dt:
        status_str = "🟢 เข้างานตรงเวลา (ไม่เกิน 5 นาที)"
        early_mins = 0
        late_mins = 0
    else:
        late_mins = int((dt - late_threshold_dt).total_seconds() // 60)
        status_str = f"🔴 เข้าสาย {late_mins} นาที"
        early_mins = 0

    return shift_name, status_str, early_mins, late_mins


# ==========================================
# --- HELPER: AUTO-RESTART AT 08:00 AM ---
# ==========================================
def auto_restart_scheduler():
    while True:
        now = datetime.now()
        if now.hour == 8 and now.minute == 0 and now.second < 10:
            print("🔄 ถึงเวลา 08:00 น. กำลังทำการรีสตาร์ทระบบอัตโนมัติ...")
            try:
                msg = f"🔄 **[แจ้งเตือนระบบ]**\nโปรแกรมกำลังทำการรีสตาร์ทตัวเองตามเวลาที่กำหนด (08:00 น.)"
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'},
                    timeout=5
                )
            except Exception as e:
                print(f"⚠️ ไม่สามารถส่งข้อความแจ้งเตือนรีสตาร์ทได้: {e}")

            try:
                if os.name == 'nt':
                    os.system("taskkill /f /im ngrok.exe >nul 2>&1")
            except Exception:
                pass

            time.sleep(3)

            if os.name == 'nt':
                script_path = os.path.abspath(__file__)
                cmd = f'start cmd /k "{sys.executable} \"{script_path}\""'
                subprocess.Popen(cmd, shell=True, cwd=BASE_DIR)
            else:
                subprocess.Popen([sys.executable] + sys.argv, cwd=BASE_DIR)

            os._exit(0)

        time.sleep(5)


# ==========================================
# --- HELPER: TELEGRAM MEMBER CHECK ---
# ==========================================
def is_telegram_group_member(user_id):
    clean_uid = str(user_id).strip() if user_id else ""
    if not clean_uid or clean_uid in ["0", "None", "null", "undefined"]:
        return False, "กรุณาระบุ Telegram User ID ที่ถูกต้องก่อนส่งพิกัด"

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember"
        payload = {'chat_id': TELEGRAM_CHAT_ID_TOUR, 'user_id': clean_uid}
        res = requests.post(url, data=payload, timeout=4)

        if res.status_code == 200:
            status = res.json().get('result', {}).get('status', '')
            if status in ['creator', 'administrator', 'member', 'restricted']:
                return True, "ยืนยันสิทธิ์สำเร็จ"
            elif status in ['left', 'kicked']:
                return False, "คุณไม่ได้อยู่ในกลุ่ม Telegram การ์ดทัวร์"
    except Exception as e:
        print(f"⚠️ Telegram Check Error: {e}")

    if clean_uid.isdigit() and len(clean_uid) >= 7:
        return True, "ยืนยันสิทธิ์สำเร็จ"

    return False, "รูปแบบ Telegram User ID ไม่ถูกต้อง"


# ==========================================
# --- FLASK WEB SERVER FOR QR CODE & CONTROL PANEL ---
# ==========================================
app_flask = Flask(__name__)

HTML_GPS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>สแกนจุดตรวจการ์ดทัวร์</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding: 20px; background-color: #f0f2f5; }
        .card { background: white; padding: 25px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); max-width: 400px; margin: 0 auto; }
        h2 { color: #1a73e8; margin-bottom: 10px; }
        p { font-size: 16px; color: #333; }
        input { width: 90%; padding: 12px; font-size: 16px; margin-top: 10px; border: 1px solid #ccc; border-radius: 8px; text-align: center; box-sizing: border-box; }
        .btn { border: none; padding: 15px 20px; font-size: 18px; font-weight: bold; border-radius: 10px; cursor: pointer; width: 100%; margin-top: 15px; box-shadow: 0 3px 6px rgba(0,0,0,0.2); color: white; }
        .btn-green { background-color: #28a745; }
        .btn-red { background-color: #dc3545; }
        .btn:disabled { background-color: #cccccc; cursor: not-allowed; }
        #status { margin-top: 20px; font-size: 15px; font-weight: bold; }
        .input-group { display: none; margin-top: 15px; background: #fff8e1; padding: 12px; border-radius: 8px; border: 1px solid #ffe082; }
    </style>
</head>
<body>
    <div class="card">
        <h2>📍 เช็คอินจุดตรวจการ์ดทัวร์</h2>
        <p><b>จุดตรวจ:</b> <span id="point_name">{{ point_name }}</span></p>

        <div id="uid-container" class="input-group">
            <p style="font-size:13px; color:#b78103; margin:0 0 8px 0; font-weight:bold;">⚠️ สแกนผ่านกล้องมือถือทั่วไป:</p>
            <p style="font-size:12px; color:#555; margin:0 0 8px 0;">กรุณาระบุ Telegram User ID ของคุณเพื่อยืนยันสิทธิ์:</p>
            <input type="number" id="input_user_id" placeholder="ใส่ Telegram User ID ของคุณ">
        </div>

        {% if is_attendance %}
            {% if attendance_enabled %}
                <p style="color:#0d6efd; font-weight:bold; margin-top:15px;">🕒 ระบบลงเวลาปฏิบัติงาน รปภ.</p>
                <button id="btn-in" class="btn btn-green" onclick="sendAttendance('IN')">🟢 สแกนเข้างาน (Check-In)</button>
                <button id="btn-out" class="btn btn-red" onclick="sendAttendance('OUT')">🔴 สแกนออกงาน (Check-Out)</button>
            {% else %}
                <p style="color:#dc3545; font-weight:bold; margin-top:20px; padding:15px; background:#f8d7da; border-radius:10px;">🔴 ระบบลงเวลาเข้า-ออกงาน ปิดใช้งานอยู่ชั่วคราว</p>
            {% endif %}
        {% else %}
            <button id="btn-send" class="btn btn-green" onclick="sendLocation()">📌 ส่งพิกัด GPS ยืนยันการตรวจ</button>
        {% endif %}

        <p id="status"></p>
    </div>

    <script>
        function getTelegramWebAppUserId() {
            if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user) {
                return window.Telegram.WebApp.initDataUnsafe.user.id;
            }
            return null;
        }

        window.onload = function() {
            const autoUid = getTelegramWebAppUserId();
            if (!autoUid) {
                document.getElementById("uid-container").style.display = "block";
                const savedUid = localStorage.getItem("telegram_guard_user_id");
                if (savedUid) {
                    document.getElementById("input_user_id").value = savedUid;
                }
            }
        };

        function getUserId() {
            let userId = getTelegramWebAppUserId();
            if (!userId) {
                const inputEl = document.getElementById("input_user_id");
                userId = inputEl ? inputEl.value.trim() : "";
            }
            if (userId && userId !== "0") {
                localStorage.setItem("telegram_guard_user_id", userId);
            }
            return userId;
        }

        function sendLocation() {
            const btn = document.getElementById("btn-send");
            const status = document.getElementById("status");
            const userId = getUserId();

            if (!userId || userId === "0") {
                status.style.color = "red";
                status.innerText = "❌ กรุณากรอก Telegram User ID ของคุณก่อนส่งพิกัด";
                return;
            }

            btn.disabled = true;
            status.style.color = "#333";
            status.innerText = "⏳ กำลังค้นหาพิกัด GPS...";

            if ("geolocation" in navigator) {
                navigator.geolocation.getCurrentPosition(function(position) {
                    const lat = position.coords.latitude;
                    const lon = position.coords.longitude;
                    status.innerText = "⏳ กำลังตรวจสอบสิทธิ์สมาชิกระบบ และส่งพิกัด...";

                    fetch('/submit_location', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            point_id: "{{ point_id }}",
                            point_name: "{{ point_name }}",
                            lat: lat,
                            lon: lon,
                            user_id: userId
                        })
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            status.style.color = "green";
                            status.innerText = "✅ ส่งพิกัดเช็คอินเข้ากลุ่ม Telegram เรียบร้อยแล้ว!";
                        } else {
                            status.style.color = "red";
                            status.innerText = "❌ " + (data.error || "เกิดข้อผิดพลาดในการส่งข้อมูล");
                            btn.disabled = false;
                        }
                    })
                    .catch(err => {
                        status.style.color = "red";
                        status.innerText = "❌ เกิดข้อผิดพลาดในการเชื่อมต่อเครือข่าย";
                        btn.disabled = false;
                    });
                }, function(error) {
                    status.style.color = "red";
                    status.innerText = "⚠️ ไม่สามารถอ่านพิกัดได้ กรุณาเปิดระบบ GPS บนมือถือ";
                    btn.disabled = false;
                }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
            } else {
                status.style.color = "red";
                status.innerText = "⚠️ อุปกรณ์นี้ไม่รองรับการดึงพิกัด GPS";
            }
        }

        function sendAttendance(actionType) {
            const btnIn = document.getElementById("btn-in");
            const btnOut = document.getElementById("btn-out");
            const status = document.getElementById("status");
            const userId = getUserId();

            if (!userId || userId === "0") {
                status.style.color = "red";
                status.innerText = "❌ กรุณากรอก Telegram User ID ของคุณก่อนส่งข้อมูล";
                return;
            }

            btnIn.disabled = true;
            btnOut.disabled = true;
            status.style.color = "#333";
            status.innerText = "⏳ กำลังรับตำแหน่ง GPS สำหรับลงเวลา...";

            if ("geolocation" in navigator) {
                navigator.geolocation.getCurrentPosition(function(position) {
                    const lat = position.coords.latitude;
                    const lon = position.coords.longitude;

                    fetch('/submit_attendance', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            action_type: actionType,
                            user_id: userId,
                            lat: lat,
                            lon: lon
                        })
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            status.style.color = "green";
                            status.innerText = actionType === 'IN' ? "✅ บันทึกเวลาเข้างานสำเร็จ!" : "✅ บันทึกเวลาออกงานเรียบร้อย!";
                        } else {
                            status.style.color = "red";
                            status.innerText = "❌ " + (data.error || "เกิดข้อผิดพลาดในการลงเวลา");
                            btnIn.disabled = false;
                            btnOut.disabled = false;
                        }
                    })
                    .catch(err => {
                        status.style.color = "red";
                        status.innerText = "❌ เกิดข้อผิดพลาดในการเชื่อมต่อเครือข่าย";
                        btnIn.disabled = false;
                        btnOut.disabled = false;
                    });
                }, function(error) {
                    status.style.color = "red";
                    status.innerText = "⚠️ ไม่สามารถอ่านพิกัดได้ กรุณาเปิดระบบ GPS บนมือถือ";
                    btnIn.disabled = false;
                    btnOut.disabled = false;
                }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 });
            } else {
                status.style.color = "red";
                status.innerText = "⚠️ อุปกรณ์นี้ไม่รองรับการดึงพิกัด GPS";
            }
        }
    </script>
</body>
</html>
"""

HTML_CONTROL_PANEL = """
<!DOCTYPE html>
<html>
<head>
    <title>⚙️ แผงควบคุมระบบรักษาความปลอดภัย</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 20px; background-color: #f0f2f5; text-align: center; }
        .card { background: white; padding: 25px; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); max-width: 450px; margin: 0 auto; }
        h2 { color: #1a73e8; margin-bottom: 20px; }
        .item { display: flex; justify-content: space-between; align-items: center; padding: 15px 0; border-bottom: 1px solid #eee; }
        .item-title { font-size: 16px; font-weight: bold; text-align: left; }
        .switch { position: relative; display: inline-block; width: 50px; height: 26px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 34px; }
        .slider:before { position: absolute; content: ""; height: 20px; width: 20px; left: 3px; bottom: 3px; background-color: white; transition: .4s; border-radius: 50%; }
        input:checked + .slider { background-color: #28a745; }
        input:checked + .slider:before { transform: translateX(23px); }
    </style>
</head>
<body>
    <div class="card">
        <h2>⚙️ แผงตั้งค่า เปิด/ปิด ระบบ</h2>

        <div class="item">
            <div class="item-title">🕒 ลงเวลาเข้า-ออกงาน (รปภ.)</div>
            <label class="switch">
                <input type="checkbox" id="sw-attendance" {% if config.enable_attendance %}checked{% endif %} onchange="toggleConfig('attendance')">
                <span class="slider"></span>
            </label>
        </div>

        <div class="item">
            <div class="item-title">🚨 แจ้งเตือนคนล้ม (Fall Detection)</div>
            <label class="switch">
                <input type="checkbox" id="sw-fall" {% if config.enable_fall_alert %}checked{% endif %} onchange="toggleConfig('fall')">
                <span class="slider"></span>
            </label>
        </div>

        <div class="item">
            <div class="item-title">🔥🚨 แจ้งเตือนไฟ / ควัน (Fire & Smoke)</div>
            <label class="switch">
                <input type="checkbox" id="sw-fire" {% if config.enable_fire_alert %}checked{% endif %} onchange="toggleConfig('fire')">
                <span class="slider"></span>
            </label>
        </div>
    </div>

    <script>
        function toggleConfig(key) {
            fetch('/api/toggle_config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key: key })
            })
            .then(res => res.json())
            .then(data => {
                if (!data.success) {
                    alert("เกิดข้อผิดพลาดในการเปลี่ยนสวิตช์");
                }
            });
        }
    </script>
</body>
</html>
"""


@app_flask.route('/scan/<int:point_id>')
def scan_point(point_id):
    cam_info = next((c for c in ALL_CAMERAS_SET2 if c['id'] == point_id), None)
    point_name = cam_info['name'] if cam_info else f"จุดตรวจที่ {point_id}"
    is_attendance = (point_id == 31)

    return render_template_string(
        HTML_GPS_TEMPLATE,
        point_id=point_id,
        point_name=point_name,
        is_attendance=is_attendance,
        attendance_enabled=SYSTEM_CONFIG["enable_attendance"]
    )


@app_flask.route('/settings')
def settings_page():
    return render_template_string(HTML_CONTROL_PANEL, config=SYSTEM_CONFIG)


@app_flask.route('/api/toggle_config', methods=['POST'])
def toggle_config_api():
    data = request.get_json()
    key = data.get('key')
    if key == 'attendance':
        SYSTEM_CONFIG["enable_attendance"] = not SYSTEM_CONFIG["enable_attendance"]
    elif key == 'fall':
        SYSTEM_CONFIG["enable_fall_alert"] = not SYSTEM_CONFIG["enable_fall_alert"]
    elif key == 'fire':
        SYSTEM_CONFIG["enable_fire_alert"] = not SYSTEM_CONFIG["enable_fire_alert"]

    save_config(SYSTEM_CONFIG)
    return jsonify({"success": True, "config": SYSTEM_CONFIG})


@app_flask.route('/submit_location', methods=['POST'])
def submit_location():
    try:
        data = request.get_json()
        point_id = data.get('point_id')
        point_name = data.get('point_name')
        lat = data.get('lat')
        lon = data.get('lon')
        user_id = data.get('user_id')

        is_member, reason_msg = is_telegram_group_member(user_id)
        if not is_member:
            return {"success": False, "error": reason_msg}, 403

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT guard_name FROM guards WHERE user_id = ?', (str(user_id).strip(),))
        guard_row = cursor.fetchone()

        if not guard_row:
            conn.close()
            return {
                "success": False,
                "error": f"❌ User ID ({user_id}) ยังไม่ได้ลงทะเบียนในระบบ! กรุณาติดต่อหัวหน้าเพื่อลงทะเบียนชื่อก่อนสแกนครับ"
            }, 403

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute('''
            INSERT INTO guard_tour_records (scanned_at, point_id, point_name, latitude, longitude, user_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (timestamp_str, point_id, point_name, lat, lon, str(user_id)))
        conn.commit()
        conn.close()

        user_display = get_guard_display_name(user_id)
        maps_url = f"https://www.google.com/maps?q={lat},{lon}"
        caption = (f"📲 **[เช็คอินการ์ดทัวร์ - สแกน QR Code]**\n"
                   f"📌 **จุดตรวจ:** {point_name}\n"
                   f"👤 **ผู้เช็คอิน (ID):** `{user_display}`\n"
                   f"🌐 **พิกัด GPS:** `{lat}, {lon}`\n"
                   f"🗺️ **Google Maps:** [เปิดดูตำแหน่งบนแผนที่]({maps_url})\n"
                   f"🕒 **เวลา:** {timestamp_str}")

        telegram_queue.put((None, caption, TELEGRAM_CHAT_ID_TOUR))
        return {"success": True}
    except Exception as e:
        print(f"⚠️ Submit Location Error: {e}")
        return {"success": False, "error": "เกิดข้อผิดพลาดจากเซิร์ฟเวอร์"}, 500


@app_flask.route('/submit_attendance', methods=['POST'])
def submit_attendance():
    if not SYSTEM_CONFIG["enable_attendance"]:
        return {"success": False, "error": "ระบบลงเวลาเข้า-ออกงาน ถูกปิดใช้งานชั่วคราว"}, 403

    try:
        data = request.get_json()
        action_type = data.get('action_type')
        user_id = data.get('user_id')
        lat = data.get('lat')
        lon = data.get('lon')

        is_member, reason_msg = is_telegram_group_member(user_id)
        if not is_member:
            return {"success": False, "error": reason_msg}, 403

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT guard_name FROM guards WHERE user_id = ?', (str(user_id).strip(),))
        guard_row = cursor.fetchone()

        if not guard_row:
            conn.close()
            return {
                "success": False,
                "error": f"❌ User ID ({user_id}) ยังไม่ได้ลงทะเบียนในระบบ! กรุณาติดต่อหัวหน้าเพื่อลงทะเบียนชื่อก่อนสแกนครับ"
            }, 403

        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        cursor.execute('''
            SELECT action_type FROM attendance_records 
            WHERE user_id = ? 
            ORDER BY id DESC LIMIT 1
        ''', (str(user_id),))
        last_record = cursor.fetchone()
        last_action = last_record[0] if last_record else None

        user_display = get_guard_display_name(user_id)

        if action_type == 'IN':
            if last_action == 'IN':
                conn.close()
                return {
                    "success": False,
                    "error": "คุณมีกะที่ยังไม่ได้สแกนออกงาน! กรุณากด 'สแกนออกงาน (Check-Out)' ของกะเดิมก่อนครับ"
                }, 400

            shift_name, status_str, early_m, late_m = calculate_checkin_status(now)

            cursor.execute('''
                INSERT INTO attendance_records (timestamp, user_id, action_type, work_date)
                VALUES (?, ?, 'IN', ?)
            ''', (timestamp_str, str(user_id), today_str))
            conn.commit()
            conn.close()

            maps_url = f"https://www.google.com/maps?q={lat},{lon}"
            caption = (f"🟢 **[ลงเวลาเข้างาน / ควงกะ - Guardhouse 1]**\n"
                       f"👤 **รปภ. (ID):** `{user_display}`\n"
                       f"🏷️ **กะปฏิบัติงาน:** {shift_name}\n"
                       f"⏰ **เวลาเข้างาน:** {timestamp_str}\n"
                       f"📊 **สถานะ:** {status_str}\n"
                       f"🌐 **พิกัด GPS:** `{lat}, {lon}`\n"
                       f"🗺️ **Google Maps:** [ดูตำแหน่ง]({maps_url})")

        elif action_type == 'OUT':
            if last_action != 'IN':
                conn.close()
                return {
                    "success": False,
                    "error": "ไม่พบประวัติการสแกนเข้างาน! คุณจำเป็นต้องสแกนเข้างาน (Check-In) ก่อน"
                }, 400

            cursor.execute('''
                SELECT timestamp FROM attendance_records 
                WHERE user_id = ? AND action_type = 'IN' 
                ORDER BY id DESC LIMIT 1
            ''', (str(user_id),))
            in_record = cursor.fetchone()

            calc_msg = ""
            if in_record:
                in_time = datetime.strptime(in_record[0], "%Y-%m-%d %H:%M:%S")
                duration = now - in_time
                hours = int(duration.total_seconds() // 3600)
                minutes = int((duration.total_seconds() % 3600) // 60)
                calc_msg = f"⏱️ **ระยะเวลาทำงานกะนี้:** {hours} ชั่วโมง {minutes} นาที"

            cursor.execute('''
                INSERT INTO attendance_records (timestamp, user_id, action_type, work_date)
                VALUES (?, ?, 'OUT', ?)
            ''', (timestamp_str, str(user_id), today_str))
            conn.commit()
            conn.close()

            maps_url = f"https://www.google.com/maps?q={lat},{lon}"
            caption = (f"🔴 **[ลงเวลาออกงาน / จบกะ - Guardhouse 1]**\n"
                       f"👤 **รปภ. (ID):** `{user_display}`\n"
                       f"⏰ **เวลาออกงาน:** {timestamp_str}\n"
                       f"{calc_msg}\n"
                       f"🌐 **พิกัด GPS:** `{lat}, {lon}`\n"
                       f"🗺️ **Google Maps:** [ดูตำแหน่ง]({maps_url})")

        telegram_queue.put((None, caption, TELEGRAM_CHAT_ID_TOUR))
        return {"success": True}

    except Exception as e:
        print(f"⚠️ Attendance Error: {e}")
        return {"success": False, "error": "เกิดข้อผิดพลาดในการบันทึกเวลาทำงาน"}, 500


def run_flask():
    app_flask.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


# ==========================================
# --- INITIALIZE MEDIAPIPE LAZY LOAD ---
# ==========================================
def get_face_detector():
    global detector
    if detector is None:
        MODEL_PATH = os.path.join(BASE_DIR, "face_landmarker.task")
        if not os.path.exists(MODEL_PATH):
            print("⏳ กำลังดาวน์โหลด MediaPipe Face Landmarker Model...")
            model_url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
            try:
                response = requests.get(model_url, timeout=30)
                with open(MODEL_PATH, "wb") as f:
                    f.write(response.content)
                print("✅ ดาวน์โหลด Face Landmarker Model สำเร็จ")
            except Exception as e:
                print(f"⚠️ ไม่สามารถดาวน์โหลดโมเดลได้: {e}")

        base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            num_faces=2
        )
        detector = vision.FaceLandmarker.create_from_options(options)
    return detector


def init_ocr():
    global ocr_reader
    if ocr_reader is None:
        print("⏳ กำลังโหลด EasyOCR...")
        ocr_reader = easyocr.Reader(['th', 'en'], gpu=False)


@contextmanager
def get_video_capture(rtsp_url):
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    try:
        yield cap
    finally:
        cap.release()


# ==========================================
# --- TELEGRAM WORKER SYSTEM ---
# ==========================================
def telegram_worker():
    while True:
        try:
            item = telegram_queue.get()
            if item is None:
                break

            if len(item) == 2:
                image_frame, caption = item
                chat_id = TELEGRAM_CHAT_ID
                file_path = None
            elif len(item) == 3:
                image_frame, caption, chat_id = item
                file_path = None
            else:
                image_frame, caption, chat_id, file_path = item

            REQ_TIMEOUT = (10, 30)
            MAX_RETRIES = 3

            if image_frame is None and not file_path:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {'chat_id': chat_id, 'text': caption, 'parse_mode': 'Markdown'}
                for attempt in range(MAX_RETRIES):
                    try:
                        res = requests.post(url, data=payload, timeout=REQ_TIMEOUT)
                        if res.status_code == 200:
                            break
                    except requests.exceptions.RequestException as req_err:
                        print(f"⚠️ [Telegram Retry {attempt + 1}/{MAX_RETRIES}] Message Send Error: {req_err}")
                        time.sleep(2 ** attempt)

            elif file_path and os.path.exists(file_path):
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}

                for attempt in range(MAX_RETRIES):
                    try:
                        with open(file_path, 'rb') as photo_file:
                            files = {'photo': photo_file}
                            res = requests.post(url, data=payload, files=files, timeout=REQ_TIMEOUT)
                            if res.status_code == 200:
                                break
                    except requests.exceptions.RequestException as req_err:
                        print(f"⚠️ [Telegram Retry {attempt + 1}/{MAX_RETRIES}] File Send Error: {req_err}")
                        time.sleep(2 ** attempt)

            elif image_frame is not None:
                success, encoded_image = cv2.imencode('.jpg', image_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if success:
                    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
                    payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
                    files = {'photo': ('alert.jpg', encoded_image.tobytes(), 'image/jpeg')}

                    for attempt in range(MAX_RETRIES):
                        try:
                            res = requests.post(url, data=payload, files=files, timeout=REQ_TIMEOUT)
                            if res.status_code == 200:
                                break
                            else:
                                print(f"⚠️ [Telegram Error Status]: {res.status_code} {res.text}")
                        except requests.exceptions.RequestException as req_err:
                            print(f"⚠️ [Telegram Retry {attempt + 1}/{MAX_RETRIES}] Frame Send Error: {req_err}")
                            time.sleep(2 ** attempt)

            telegram_queue.task_done()
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️ [Telegram Worker Unexpected Error]: {e}")
            time.sleep(1)


# ==========================================
# --- HELPER: FALL DETECTION FUNCTION ---
# ==========================================
def detect_fall_in_frame(frame, fall_model):
    if not SYSTEM_CONFIG["enable_fall_alert"]:
        return False

    if fall_model is None or frame is None:
        return False
    try:
        with model_lock:
            fall_results = fall_model(frame, conf=0.75, verbose=False)
        if len(fall_results) > 0 and len(fall_results[0].boxes) > 0:
            for fbox in fall_results[0].boxes:
                fx1, fy1, fx2, fy2 = map(int, fbox.xyxy[0])
                cv2.rectangle(frame, (fx1, fy1), (fx2, fy2), (0, 0, 255), 3)
                cv2.putText(frame, "FALL DETECTED!!", (fx1, fy1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            return True
    except Exception as e:
        print(f"⚠️ Fall Detection Error: {e}")
    return False


# ==========================================
# --- OCR EXTRACTION HELPER ---
# ==========================================
def extract_id_card_data(raw_text_list):
    full_text = " ".join(raw_text_list)

    id_pattern = re.search(r'\b\d\s?\d{4}\s?\d{5}\s?\d{2}\s?\d\b', full_text)
    cid = id_pattern.group(0) if id_pattern else "ไม่พบข้อมูล"

    name_pattern = re.search(r'(นาย|นาง|นางสาว)\s*([ก-๙]+)\s+([ก-๙]+)', full_text)
    th_name = f"{name_pattern.group(1)} {name_pattern.group(2)} {name_pattern.group(3)}" if name_pattern else "ไม่พบข้อมูล"

    en_name_pattern = re.search(r'(Mr\.|Mrs\.|Miss)\s*([A-Za-z]+)', full_text, re.IGNORECASE)
    en_surname_pattern = re.search(r'Last name\s*([A-Za-z]+)', full_text, re.IGNORECASE)
    en_first = en_name_pattern.group(2) if en_name_pattern else ""
    en_last = en_surname_pattern.group(1) if en_surname_pattern else ""
    en_name = f"{en_first} {en_last}".strip() if (en_first or en_last) else "ไม่พบข้อมูล"

    dob_pattern = re.search(r'เกิดวันที่\s*(\d{1,2}\s+[ก-๙\.]+\s+\d{4})', full_text)
    dob = dob_pattern.group(1) if dob_pattern else "ไม่พบข้อมูล"

    address_pattern = re.search(r'ที่อยู่\s*(.+?)(?=วันออกบัตร|วันบัตรหมดอายุ|เจ้าพนักงาน|$)', full_text)
    address = address_pattern.group(1).strip() if address_pattern else "ไม่พบข้อมูล"

    return {
        "cid": cid,
        "th_name": th_name,
        "en_name": en_name,
        "dob": dob,
        "address": address
    }


# ==========================================
# --- TELEGRAM BOT HANDLERS ---
# ==========================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📷 ถ่ายรูป / ส่งรูปบัตรประชาชน")],
        [KeyboardButton("⏱️ สรุปเวลาทำงานวันนี้"), KeyboardButton("📅 สรุปเวลาทำงานเดือนนี้")],
        [KeyboardButton("⚙️ สถานะ/เปิด-ปิด ระบบ"), KeyboardButton("📊 รายงานการ์ดทัวร์วันนี้")],
        [KeyboardButton("📜 ประวัติ 100 รายการ"), KeyboardButton("ℹ️ วิธีใช้งาน")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"สวัสดีครับคุณ {update.effective_user.first_name}! (Telegram User ID ของคุณคือ: `{update.effective_user.id}`)\n\n"
        "เลือกเมนูด้านล่าง หรือพิมพ์คำสั่งจัดการรายชื่อ รปภ. ได้เลยครับ\n\n"
        "💡 **การจัดการรายชื่อ รปภ.:**\n"
        "• `/register_guard <ID> <ชื่อ>` : ลงทะเบียน / แก้ไขชื่อ\n"
        "• `/list_guards` : ดูรายชื่อ รปภ. ที่ลงทะเบียนทั้งหมด\n"
        "• `/delete_guard <ID>` : ลบรายชื่อ รปภ.\n\n"
        "💡 **การสร้าง QR Code:**\n"
        "• `/genqr_point <ID>` : สร้าง QR Code เฉพาะจุด\n"
        "• `/genqr_tour` : สร้าง QR Code ครบทุกจุด",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def register_guard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ **รูปแบบคำสั่งไม่ถูกต้อง**\n"
            "ตัวอย่างการใช้งาน: `/register_guard 6728708745 นายสมชาย ใจดี`",
            parse_mode='Markdown'
        )
        return

    target_uid = args[0].strip()
    guard_name = " ".join(args[1:]).strip()

    set_guard_name(target_uid, guard_name)
    await update.message.reply_text(
        f"✅ **บันทึก / แก้ไขชื่อ รปภ. เรียบร้อยแล้ว!**\n"
        f"🆔 **User ID:** `{target_uid}`\n"
        f"👤 **ชื่อ-นามสกุล:** {guard_name}",
        parse_mode='Markdown'
    )


async def list_guards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    guards = get_all_guards()
    if not guards:
        await update.message.reply_text("📂 ยังไม่มีการลงทะเบียนรายชื่อ รปภ. ในระบบ")
        return

    msg = f"📋 **รายชื่อ รปภ. ที่ลงทะเบียนไว้ทั้งหมด ({len(guards)} คน)**\n"
    msg += "──────────────────\n"
    for idx, (uid, gname) in enumerate(guards, 1):
        msg += f"**{idx}.** 🆔 `{uid}` ➔ 👤 **{gname}**\n"

    msg += "──────────────────\n"
    msg += "💡 **แก้ไขชื่อ:** `/register_guard <ID> <ชื่อใหม่>`\n"
    msg += "💡 **ลบรายชื่อ:** `/delete_guard <ID>`"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def delete_guard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ **กรุณาระบุ Telegram User ID ที่ต้องการลบ**\n"
            "ตัวอย่างการใช้งาน: `/delete_guard 6728708745`",
            parse_mode='Markdown'
        )
        return

    target_uid = args[0].strip()
    success = delete_guard(target_uid)

    if success:
        await update.message.reply_text(f"✅ ลบข้อมูล รปภ. User ID: `{target_uid}` เรียบร้อยแล้ว", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"❌ ไม่พบข้อมูล รปภ. User ID: `{target_uid}` ในระบบ", parse_mode='Markdown')


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st_att = "🟢 เปิดใช้งาน" if SYSTEM_CONFIG["enable_attendance"] else "🔴 ปิดใช้งาน"
    st_fall = "🟢 เปิดใช้งาน" if SYSTEM_CONFIG["enable_fall_alert"] else "🔴 ปิดใช้งาน"
    st_fire = "🟢 เปิดใช้งาน" if SYSTEM_CONFIG["enable_fire_alert"] else "🔴 ปิดใช้งาน"

    msg = (f"⚙️ **สถานะเปิด/ปิด ระบบปัจจุบัน**\n"
           f"──────────────────\n"
           f"🕒 **ลงเวลาเข้า-ออกงาน:** {st_att}\n"
           f"🚨 **แจ้งเตือนคนล้ม:** {st_fall}\n"
           f"🔥🚨 **แจ้งเตือนไฟ/ควัน:** {st_fire}\n"
           f"──────────────────\n"
           f"💡 **คำสั่งจัดการเปิด/ปิด:**\n"
           f"• `/toggle attendance` : เปิด/ปิด สแกนเข้า-ออกงาน\n"
           f"• `/toggle fall` : เปิด/ปิด แจ้งเตือนคนล้ม\n"
           f"• `/toggle fire` : เปิด/ปิด แจ้งเตือนไฟ/ควัน\n\n"
           f"💡 **คำสั่งจัดการรายชื่อ รปภ.:**\n"
           f"• `/list_guards` : ดูรายชื่อ รปภ. ทั้งหมด\n"
           f"• `/register_guard <ID> <ชื่อ>` : บันทึก/แก้ไขชื่อ\n"
           f"• `/delete_guard <ID>` : ลบรายชื่อ\n"
           f"🌐 **หน้าเว็บตั้งค่า:** `{SERVER_PUBLIC_URL}/settings`")

    await update.message.reply_text(msg, parse_mode='Markdown')


async def toggle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ กรุณาระบุระบบที่ต้องการเปิด/ปิด เช่น: `/toggle attendance`, `/toggle fall`, `/toggle fire`",
            parse_mode='Markdown')
        return

    target = args[0].lower().strip()
    if target == 'attendance':
        SYSTEM_CONFIG["enable_attendance"] = not SYSTEM_CONFIG["enable_attendance"]
        status = "เปิดใช้งาน 🟢" if SYSTEM_CONFIG["enable_attendance"] else "ปิดใช้งาน 🔴"
        save_config(SYSTEM_CONFIG)
        await update.message.reply_text(f"✅ เปลี่ยนสถานะ **ระบบลงเวลาเข้า-ออกงาน** เป็น: {status}",
                                        parse_mode='Markdown')

    elif target == 'fall':
        SYSTEM_CONFIG["enable_fall_alert"] = not SYSTEM_CONFIG["enable_fall_alert"]
        status = "เปิดใช้งาน 🟢" if SYSTEM_CONFIG["enable_fall_alert"] else "ปิดใช้งาน 🔴"
        save_config(SYSTEM_CONFIG)
        await update.message.reply_text(f"✅ เปลี่ยนสถานะ **ระบบแจ้งเตือนคนล้ม** เป็น: {status}", parse_mode='Markdown')

    elif target == 'fire':
        SYSTEM_CONFIG["enable_fire_alert"] = not SYSTEM_CONFIG["enable_fire_alert"]
        status = "เปิดใช้งาน 🟢" if SYSTEM_CONFIG["enable_fire_alert"] else "ปิดใช้งาน 🔴"
        save_config(SYSTEM_CONFIG)
        await update.message.reply_text(f"✅ เปลี่ยนสถานะ **ระบบแจ้งเตือนไฟ/ควัน** เป็น: {status}",
                                        parse_mode='Markdown')

    else:
        await update.message.reply_text("❌ ไม่พบระบบที่ระบุ กรุณาใช้คำสั่ง: `attendance`, `fall`, หรือ `fire`",
                                        parse_mode='Markdown')


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    records = get_recent_records(limit=100)

    if not records:
        await update.message.reply_text("📂 ยังไม่มีข้อมูลการบันทึกในฐานข้อมูล")
        return

    response = f"📜 **ประวัติการอ่านบัตรประชาชนล่าสุด ({len(records)} รายการ):**\n"
    response += "──────────────────\n"

    for idx, row in enumerate(records, 1):
        scanned_at, cid, name_th, scanned_by = row
        item_text = (
            f"**{idx}. เวลา:** {scanned_at}\n"
            f"🆔 **เลขบัตร:** {cid}\n"
            f"👤 **ชื่อ:** {name_th}\n"
            f"📌 **ผู้ส่ง:** {scanned_by}\n"
            "──────────────────\n"
        )
        if len(response) + len(item_text) > 4000:
            await update.message.reply_text(response, parse_mode='Markdown')
            response = ""
        response += item_text

    if response:
        await update.message.reply_text(response, parse_mode='Markdown')


async def show_latest_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    latest_date, records = get_latest_day_records(limit=100)

    if not records:
        await update.message.reply_text("📂 ยังไม่มีข้อมูลการบันทึกในฐานข้อมูล")
        return

    response = f"📅 **ประวัติสแกนของวันล่าสุด ({latest_date})**\n"
    response += f"📊 จำนวนทั้งหมด: {len(records)} รายการ\n"
    response += "──────────────────\n"

    for idx, row in enumerate(records, 1):
        scanned_at, cid, name_th, scanned_by = row
        item_text = (
            f"**{idx}. เวลา:** {scanned_at}\n"
            f"🆔 **เลขบัตร:** {cid}\n"
            f"👤 **ชื่อ:** {name_th}\n"
            f"📌 **ผู้ส่ง:** {scanned_by}\n"
            "──────────────────\n"
        )
        if len(response) + len(item_text) > 4000:
            await update.message.reply_text(response, parse_mode='Markdown')
            response = ""
        response += item_text

    if response:
        await update.message.reply_text(response, parse_mode='Markdown')


async def tour_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.now().strftime("%Y-%m-%d")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT scanned_at, point_name, latitude, longitude, user_id
        FROM guard_tour_records
        WHERE DATE (scanned_at) = ?
        ORDER BY id ASC
    ''', (today_str,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(f"📂 ยังไม่มีข้อมูลการเช็คอินการ์ดทัวร์ของวันนี้ ({today_str})")
        return

    msg = f"📊 **รายงานการเช็คอินการ์ดทัวร์ประจำวันที่ {today_str}**\n"
    msg += f"🔢 จำนวนการเช็คอินทั้งหมด: {len(rows)} ครั้ง\n"
    msg += "──────────────────\n"

    for idx, (scanned_at, point_name, lat, lon, user_id) in enumerate(rows, 1):
        time_only = scanned_at.split(" ")[1] if " " in scanned_at else scanned_at
        user_display = get_guard_display_name(user_id)
        msg += f"**{idx}.** เวลา `{time_only}` - {point_name} (โดย: `{user_display}`)\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def attendance_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today_str = datetime.now().strftime("%Y-%m-%d")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, action_type, timestamp 
        FROM attendance_records 
        WHERE work_date = ? 
        ORDER BY user_id, id ASC
    ''', (today_str,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(f"📂 ยังไม่มีประวัติการลงเวลาเข้า-ออกงานในวันนี้ ({today_str})")
        return

    report = {}
    for user_id, action, ts in rows:
        if user_id not in report:
            report[user_id] = []
        report[user_id].append((action, ts))

    msg = f"📋 **สรุปเวลาปฏิบัติงาน รปภ. ประจำวันที่ {today_str}**\n"
    msg += "──────────────────\n"

    for uid, records in report.items():
        user_display = get_guard_display_name(uid)
        msg += f"👤 **รปภ. ID:** `{user_display}`\n"
        in_time = None
        for action, ts in records:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            time_str = dt.strftime("%H:%M:%S")
            if action == 'IN':
                in_time = dt
                shift_n, status_info, _, _ = calculate_checkin_status(dt)
                msg += f"  🟢 **เข้า:** `{time_str}` | {shift_n} ({status_info})\n"
            elif action == 'OUT':
                dur_str = ""
                if in_time:
                    dur = dt - in_time
                    hrs = int(dur.total_seconds() // 3600)
                    mins = int((dur.total_seconds() % 3600) // 60)
                    dur_str = f" (ทำกะนี้ไป {hrs} ชม. {mins} นาที)"
                    in_time = None
                msg += f"  🔴 **ออก:** `{time_str}`{dur_str}\n"
        msg += "──────────────────\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def monthly_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if args:
        target_month = args[0].strip()
    else:
        target_month = datetime.now().strftime("%Y-%m")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, action_type, timestamp 
        FROM attendance_records 
        WHERE strftime('%Y-%m', timestamp) = ? 
        ORDER BY user_id, timestamp ASC
    ''', (target_month,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(f"📂 ไม่พบข้อมูลการลงเวลาของ รปภ. ในเดือน {target_month}")
        return

    user_summary = {}

    for uid, action, ts in rows:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        if uid not in user_summary:
            user_summary[uid] = {
                'on_time_count': 0,
                'late_count': 0,
                'total_late_mins': 0,
                'early_count': 0,
                'total_early_mins': 0,
                'checkin_count': 0,
                'checkout_count': 0,
                'total_work_seconds': 0,
                'last_in_time': None
            }

        stats = user_summary[uid]
        if action == 'IN':
            stats['checkin_count'] += 1
            stats['last_in_time'] = dt
            _, _, early_m, late_m = calculate_checkin_status(dt)
            if late_m > 0:
                stats['late_count'] += 1
                stats['total_late_mins'] += late_m
            elif early_m > 0:
                stats['early_count'] += 1
                stats['total_early_mins'] += early_m
            else:
                stats['on_time_count'] += 1

        elif action == 'OUT':
            stats['checkout_count'] += 1
            if stats['last_in_time']:
                work_sec = (dt - stats['last_in_time']).total_seconds()
                if work_sec > 0:
                    stats['total_work_seconds'] += work_sec
                stats['last_in_time'] = None

    msg = f"📊 **สรุปรายงานการลงเวลา รปภ. ประจำเดือน {target_month}**\n"
    msg += "──────────────────\n"

    for uid, stats in user_summary.items():
        tot_hrs = int(stats['total_work_seconds'] // 3600)
        tot_mins = int((stats['total_work_seconds'] % 3600) // 60)
        user_display = get_guard_display_name(uid)

        msg += (f"👤 **รปภ. (ID):** `{user_display}`\n"
                f"🟢 เข้าตรงเวลา: {stats['on_time_count']} ครั้ง\n"
                f"⏩ เข้าก่อนเวลา: {stats['early_count']} ครั้ง (รวม {stats['total_early_mins']} นาที)\n"
                f"🔴 เข้าสาย: {stats['late_count']} ครั้ง (รวมสาย {stats['total_late_mins']} นาที)\n"
                f"📥 เข้างานรวม: {stats['checkin_count']} ครั้ง | 📤 ออกงานรวม: {stats['checkout_count']} ครั้ง\n"
                f"⏱️ **เวลารวมสุทธิ:** {tot_hrs} ชม. {tot_mins} นาที\n"
                "──────────────────\n")

    await update.message.reply_text(msg, parse_mode='Markdown')


async def attendance_report_by_date_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "📅 **กรุณาระบุวันที่ที่ต้องการดูรายงานเวลาทำงาน**\n"
            "ตัวอย่างการพิมพ์: `/attendance_report_date 2026-10-22`",
            parse_mode='Markdown'
        )
        return

    target_date = args[0].strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, action_type, timestamp 
        FROM attendance_records 
        WHERE work_date = ? 
        ORDER BY user_id, id ASC
    ''', (target_date,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(f"📂 ไม่พบประวัติการลงเวลาเข้า-ออกงานในวันที่ {target_date}")
        return

    report = {}
    for user_id, action, ts in rows:
        if user_id not in report:
            report[user_id] = []
        report[user_id].append((action, ts))

    msg = f"📋 **สรุปเวลาปฏิบัติงาน รปภ. ประจำวันที่ {target_date}**\n"
    msg += "──────────────────\n"

    for uid, records in report.items():
        user_display = get_guard_display_name(uid)
        msg += f"👤 **รปภ. ID:** `{user_display}`\n"
        in_time = None
        for action, ts in records:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            time_str = dt.strftime("%H:%M:%S")
            if action == 'IN':
                in_time = dt
                shift_n, status_info, _, _ = calculate_checkin_status(dt)
                msg += f"  🟢 **เข้า:** `{time_str}` | {shift_n} ({status_info})\n"
            elif action == 'OUT':
                dur_str = ""
                if in_time:
                    dur = dt - in_time
                    hrs = int(dur.total_seconds() // 3600)
                    mins = int((dur.total_seconds() % 3600) // 60)
                    dur_str = f" (ทำกะนี้ไป {hrs} ชม. {mins} นาที)"
                    in_time = None
                msg += f"  🔴 **ออก:** `{time_str}`{dur_str}\n"
        msg += "──────────────────\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def history_by_date_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "📅 **กรุณาระบุวันที่ที่ต้องการดูรายงานย้อนหลัง**\n"
            "ตัวอย่างการพิมพ์: `/history_date 2026-10-22`",
            parse_mode='Markdown'
        )
        return

    target_date = args[0].strip()
    records = get_records_by_date(target_date)

    if not records:
        await update.message.reply_text(f"📂 ไม่พบข้อมูลการสแกนบัตรประชาชนในวันที่ {target_date}")
        return

    response = f"📜 **ประวัติการอ่านบัตรประชาชนประจำวันที่ {target_date} ({len(records)} รายการ):**\n"
    response += "──────────────────\n"

    for idx, row in enumerate(records, 1):
        scanned_at, cid, name_th, scanned_by = row
        time_only = scanned_at.split(" ")[1] if " " in scanned_at else scanned_at
        item_text = (
            f"**{idx}. เวลา:** `{time_only}`\n"
            f"🆔 **เลขบัตร:** {cid}\n"
            f"👤 **ชื่อ:** {name_th}\n"
            f"📌 **ผู้ส่ง:** {scanned_by}\n"
            "──────────────────\n"
        )
        if len(response) + len(item_text) > 4000:
            await update.message.reply_text(response, parse_mode='Markdown')
            response = ""
        response += item_text

    if response:
        await update.message.reply_text(response, parse_mode='Markdown')


async def tour_report_by_date_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "📅 **กรุณาระบุวันที่ที่ต้องการดูรายงานการ์ดทัวร์**\n"
            "ตัวอย่างการพิมพ์: `/tour_report_date 2026-10-22`",
            parse_mode='Markdown'
        )
        return

    target_date = args[0].strip()
    rows = get_tour_records_by_date(target_date)

    if not rows:
        await update.message.reply_text(f"📂 ไม่พบข้อมูลการเช็คอินการ์ดทัวร์ในวันที่ {target_date}")
        return

    msg = f"📊 **รายงานการเช็คอินการ์ดทัวร์ประจำวันที่ {target_date}**\n"
    msg += f"🔢 จำนวนการเช็คอินทั้งหมด: {len(rows)} ครั้ง\n"
    msg += "──────────────────\n"

    for idx, (scanned_at, point_name, lat, lon, user_id) in enumerate(rows, 1):
        time_only = scanned_at.split(" ")[1] if " " in scanned_at else scanned_at
        user_display = get_guard_display_name(user_id)
        msg += f"**{idx}.** เวลา `{time_only}` - {point_name} (โดย: `{user_display}`)\n"

    await update.message.reply_text(msg, parse_mode='Markdown')


async def genqr_point_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "⚠️ **กรุณาระบุ ID จุดตรวจที่ต้องการสร้าง QR Code**\n"
            "ตัวอย่างการใช้งาน:\n"
            "• `/genqr_point 15` (สร้างเฉพาะจุดที่ 15)\n"
            "• `/genqr_point 31` (สร้างเฉพาะจุด Guardhouse 1)",
            parse_mode='Markdown'
        )
        return

    try:
        target_id = int(args[0].strip())
    except ValueError:
        await update.message.reply_text("❌ กรุณาระบุตัวเลข ID จุดตรวจให้ถูกต้องครับ")
        return

    cam = next((c for c in ALL_CAMERAS_SET2 if c['id'] == target_id), None)
    if not cam:
        await update.message.reply_text(f"❌ ไม่พบจุดตรวจ ID: `{target_id}` ในระบบ", parse_mode='Markdown')
        return

    point_id = cam['id']
    point_name = cam['name']
    target_url = f"{SERVER_PUBLIC_URL}/scan/{point_id}"

    await update.message.reply_text(f"⏳ กำลังสร้าง QR Code สำหรับ **{point_name}**...", parse_mode='Markdown')

    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=3
        )
        qr.add_data(target_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')

        qr_w, qr_h = qr_img.size
        extra_h = 70

        new_img = Image.new('RGB', (qr_w, qr_h + extra_h), color=(255, 255, 255))
        new_img.paste(qr_img, (0, 0))

        draw = ImageDraw.Draw(new_img)

        font = None
        font_paths = [
            "C:\\Windows\\Fonts\\tahoma.ttf",
            "C:\\Windows\\Fonts\\THSarabunNew.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ]
        for font_p in font_paths:
            if os.path.exists(font_p):
                try:
                    font = ImageFont.truetype(font_p, 20)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()

        draw.text((15, qr_h + 20), point_name, fill=(0, 0, 0), font=font)

        filename = f"point_{point_id:02d}.png"
        save_path = os.path.join(SAVE_DIR_QR_CODE, filename)
        new_img.save(save_path)

        bio = BytesIO()
        new_img.save(bio, 'PNG')
        bio.seek(0)

        caption = f"📌 **QR Code จุดตรวจ:** {point_name}\n💾 บันทึกไฟล์: `qr_codes/{filename}`"
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=bio.getvalue(),
            caption=caption,
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ เกิดข้อผิดพลาดในการสร้าง QR Code: {str(e)}")


async def generate_tour_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"⏳ กำลังเริ่มสร้าง QR Code จุดตรวจรวมถึง Guardhouse1-3 ทั้งหมด ({len(ALL_CAMERAS_SET2)} จุด)...")

    def build_and_save_qr(target_url, point_id, point_name):
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=3
            )
            qr.add_data(target_url)
            qr.make(fit=True)
            qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')

            qr_w, qr_h = qr_img.size
            extra_h = 70

            new_img = Image.new('RGB', (qr_w, qr_h + extra_h), color=(255, 255, 255))
            new_img.paste(qr_img, (0, 0))

            draw = ImageDraw.Draw(new_img)

            font = None
            font_paths = [
                "C:\\Windows\\Fonts\\tahoma.ttf",
                "C:\\Windows\\Fonts\\THSarabunNew.ttf",
                "C:\\Windows\\Fonts\\arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ]
            for font_p in font_paths:
                if os.path.exists(font_p):
                    try:
                        font = ImageFont.truetype(font_p, 20)
                        break
                    except Exception:
                        pass
            if font is None:
                font = ImageFont.load_default()

            draw.text((15, qr_h + 20), point_name, fill=(0, 0, 0), font=font)

            filename = f"point_{point_id:02d}.png"
            save_path = os.path.join(SAVE_DIR_QR_CODE, filename)
            new_img.save(save_path)

            bio = BytesIO()
            new_img.save(bio, 'PNG')
            bio.seek(0)
            return bio.getvalue(), save_path
        except Exception as err:
            print(f"⚠️ Error Build QR Point {point_id}: {err}")
            return None, None

    for cam in ALL_CAMERAS_SET2:
        point_id = cam['id']
        point_name = cam['name']
        target_url = f"{SERVER_PUBLIC_URL}/scan/{point_id}"

        try:
            qr_bytes, saved_file_path = await asyncio.to_thread(build_and_save_qr, target_url, point_id, point_name)

            if qr_bytes and saved_file_path:
                caption = f"📌 **QR Code จุดตรวจ:** {point_name}\n💾 บันทึกไฟล์: `qr_codes/{os.path.basename(saved_file_path)}`"
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=qr_bytes,
                    caption=caption,
                    parse_mode='Markdown'
                )
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f"⚠️ เกิดข้อผิดพลาดในการส่ง QR จุดที่ {point_id}: {e}")

    await update.message.reply_text("✅ สร้างและบันทึก QR Code การ์ดทัวร์และ Guardhouse ครบทุกจุดเรียบร้อยแล้ว!")


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "⏱️ สรุปเวลาทำงานวันนี้" in text:
        await attendance_report_cmd(update, context)
    elif "📅 สรุปเวลาทำงานเดือนนี้" in text:
        await monthly_report_cmd(update, context)
    elif "⚙️ สถานะ/เปิด-ปิด ระบบ" in text:
        await status_cmd(update, context)
    elif "📷 ถ่ายรูป" in text:
        await update.message.reply_text(
            "📸 วิธีส่งรูปในกลุ่ม:\n"
            "1. กด Reply (ตอบกลับ) ข้อความนี้\n"
            "2. แนบรูปถ่ายบัตรประชาชนแล้วกดส่งได้เลยครับ"
        )
    elif "📜 ประวัติ 100 รายการ" in text:
        await show_history(update, context)
    elif "📅 ข้อมูลวันล่าสุด" in text:
        await show_latest_day(update, context)
    elif "📊 รายงานการ์ดทัวร์วันนี้" in text:
        await tour_report_cmd(update, context)
    elif "ℹ️ วิธีใช้งาน" in text:
        await update.message.reply_text(
            "ℹ️ **แนะนำคำสั่งระบบลงเวลา รปภ.:**\n"
            "• `/status` : เช็คสถานะ เปิด/ปิด ระบบ\n"
            "• `/toggle attendance` : เปิด/ปิด ลงเวลาเข้า-ออกงาน\n"
            "• `/toggle fall` : เปิด/ปิด แจ้งเตือนคนล้ม\n"
            "• `/toggle fire` : เปิด/ปิด แจ้งเตือนไฟ/ควัน\n"
            "• `/list_guards` : ดูรายชื่อ รปภ. ทั้งหมด\n"
            "• `/register_guard <ID> <ชื่อ>` : ลงทะเบียน/แก้ไขชื่อ รปภ.\n"
            "• `/delete_guard <ID>` : ลบรายชื่อ รปภ.\n"
            "• `/genqr_point <ID>` : สร้าง QR Code เฉพาะจุด\n"
            "• `/attendance_report` : ดูเวลาทำงาน รปภ. ประจำวัน\n"
            "• `/monthly_report YYYY-MM` : ดูสรุปเวลาทำงานรายเดือน"
        )


async def process_and_send_image(update: Update, context: ContextTypes.DEFAULT_TYPE, file_obj):
    status_message = await update.message.reply_text("⏳ กำลังอ่านข้อมูลและบันทึกลงฐานข้อมูล...")
    try:
        image_bytes = await file_obj.download_as_bytearray()
        if ocr_reader is not None:
            results = ocr_reader.readtext(bytes(image_bytes), detail=0)

            if results:
                extracted = extract_id_card_data(results)
                user_name = update.effective_user.first_name if update.effective_user else "Unknown"
                current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                save_to_db(extracted, user_name, current_time_str)

                message_body = (
                    "🪪 สรุปข้อมูลบัตรประชาชน (บันทึกข้อมูลเรียบร้อย ✅)\n"
                    "──────────────────\n"
                    f"⏰ เวลาที่อ่านบัตร: {current_time_str}\n"
                    f"🆔 เลขบัตรประชาชน: {extracted['cid']}\n"
                    f"👤 ชื่อ-นามสกุล (TH): {extracted['th_name']}\n"
                    f"👤 Name-Surname (EN): {extracted['en_name']}\n"
                    f"🎂 วันเกิด: {extracted['dob']}\n"
                    f"🏠 ที่อยู่: {extracted['address']}\n"
                    "──────────────────"
                )

                await status_message.edit_text(message_body)

                if str(update.effective_chat.id) != TELEGRAM_CHAT_ID:
                    await context.bot.send_message(
                        chat_id=TELEGRAM_CHAT_ID,
                        text=f"🔔 มีการสแกนบัตรประชาชนใหม่\nส่งโดย: {user_name}\n\n{message_body}"
                    )
            else:
                await status_message.edit_text("❌ ไม่สามารถอ่านข้อความได้ กรุณาส่งรูปที่ชัดเจนกว่านี้ครับ")
        else:
            await status_message.edit_text("⚠️ ระบบ OCR กำลังเริ่มต้น กรุณาลองใหม่อีกครั้ง")

    except Exception as e:
        await status_message.edit_text(f"⚠️ เกิดข้อผิดพลาด: {str(e)}")


def is_meant_for_bot(update: Update, bot_username: str) -> bool:
    message = update.message
    if not message:
        return False
    if message.chat.type == "private":
        return True
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == update.get_bot().id:
        return True
    if message.caption and f"@{bot_username}" in message.caption:
        return True
    return False


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_info = await context.bot.get_me()
    if is_meant_for_bot(update, bot_info.username):
        photo_file = await update.message.photo[-1].get_file()
        await process_and_send_image(update, context, photo_file)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_info = await context.bot.get_me()
    doc = update.message.document
    if doc.mime_type and doc.mime_type.startswith('image/') and is_meant_for_bot(update, bot_info.username):
        doc_file = await doc.get_file()
        await process_and_send_image(update, context, doc_file)


def run_telegram_bot():
    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            app = (
                Application.builder()
                .token(TELEGRAM_BOT_TOKEN)
                .read_timeout(30)
                .connect_timeout(30)
                .write_timeout(30)
                .build()
            )

            app.add_handler(CommandHandler("start", start_cmd))
            app.add_handler(CommandHandler("status", status_cmd))
            app.add_handler(CommandHandler("toggle", toggle_cmd))
            app.add_handler(CommandHandler("register_guard", register_guard_cmd))
            app.add_handler(CommandHandler("list_guards", list_guards_cmd))
            app.add_handler(CommandHandler("delete_guard", delete_guard_cmd))
            app.add_handler(CommandHandler("history", show_history))
            app.add_handler(CommandHandler("history_date", history_by_date_cmd))
            app.add_handler(CommandHandler("day", show_latest_day))
            app.add_handler(CommandHandler("tour_report", tour_report_cmd))
            app.add_handler(CommandHandler("tour_report_date", tour_report_by_date_cmd))
            app.add_handler(CommandHandler("attendance_report", attendance_report_cmd))
            app.add_handler(CommandHandler("attendance_report_date", attendance_report_by_date_cmd))
            app.add_handler(CommandHandler("monthly_report", monthly_report_cmd))
            app.add_handler(CommandHandler("genqr_tour", generate_tour_qr))
            app.add_handler(CommandHandler("genqr_point", genqr_point_cmd))

            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
            app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
            app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

            print("🤖 บอท Telegram สำหรับสแกนบัตรและระบบ QR Code การ์ดทัวร์ เริ่มต้นทำงานเรียบร้อยแล้ว...")
            app.run_polling(drop_pending_updates=True, close_loop=False)
            break

        except Exception as e:
            print(f"⚠️ Telegram Bot Runner Exception: {e}")
            print("🔄 จะพยายามเชื่อมต่อ Telegram Bot ใหม่อีกครั้งใน 5 วินาที...")
            time.sleep(5)


# ==========================================
# --- HELPER: MOTION DETECTION & CAPTURE ---
# ==========================================
def check_motion_and_capture(cam_id, cam_name, rtsp_url, frame_count=2, delay=0.2, min_area=1500):
    if not rtsp_url:
        return False, None

    is_moving = False
    prev_gray = None
    latest_frame = None

    with get_video_capture(rtsp_url) as cap:
        if not cap.isOpened():
            return False, None

        try:
            for i in range(frame_count):
                for _ in range(2):
                    cap.grab()

                ret, frame = cap.read()
                if not ret or frame is None or frame.size == 0:
                    continue

                gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (15, 15), 0)

                if prev_gray is not None:
                    diff = cv2.absdiff(prev_gray, gray)
                    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                    contours, _ = cv2.findContours(cv2.dilate(thresh, None, iterations=1),
                                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    if any(cv2.contourArea(c) >= min_area for c in contours):
                        is_moving = True

                prev_gray = gray
                latest_frame = frame

                if i < frame_count - 1:
                    time.sleep(delay)
        except Exception as e:
            print(f"⚠️ RTSP Read Error [{cam_name}]: {e}")

    return is_moving, latest_frame


# ==========================================
# --- 1. SET 1 - GROUP 1: MOTION, OCR & FALL ---
# ==========================================
def process_group1_motion(cam_info, model, fall_model):
    cam_id = cam_info['id']
    cam_name = cam_info['name']
    rtsp_url = cam_info['rtsp']

    current_time = time.time()
    with state_lock:
        if current_time - last_alert_time[cam_id] < COOLDOWN_SECONDS:
            return

    is_moving, latest_frame = check_motion_and_capture(cam_id, cam_name, rtsp_url, frame_count=2, delay=0.5)
    if latest_frame is None or not is_moving:
        return

    timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")
    fall_detected = detect_fall_in_frame(latest_frame, fall_model)

    detected_plates = []
    detected_persons = 0

    with model_lock:
        results = model(latest_frame, classes=[0, 2, 3, 5, 7], verbose=False)

    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])

        if cls_id == 0:
            detected_persons += 1
            cv2.rectangle(latest_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        else:
            cv2.rectangle(latest_frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
            vehicle_crop = latest_frame[y1:y2, x1:x2]

            if vehicle_crop.size > 0 and ocr_reader is not None:
                try:
                    ocr_results = ocr_reader.readtext(vehicle_crop)
                    for res in ocr_results:
                        text, conf = res[1], res[2]
                        if conf > 0.35 and len(text) >= 2:
                            detected_plates.append(text)
                            cv2.putText(latest_frame, f"PLATE: {text}", (x1, y2 + 15),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                except Exception:
                    pass

    plate_info = ", ".join(detected_plates) if detected_plates else "อ่านไม่ชัด/ไม่พบป้าย"
    alert_header = "🚨🚨 [แจ้งเตือนด่วน: ตรวจพบคนล้ม!]" if fall_detected else f"📹 [{cam_name}]"

    caption = (f"{alert_header}\n"
               f"🚨 ตรวจพบการเคลื่อนไหวแบบ Real-time!\n"
               f"🚘 ทะเบียนรถ: {plate_info}\n"
               f"👤 คนที่พบ: {detected_persons} คน\n"
               f"🕒 เวลา: {timestamp_str}")

    telegram_queue.put((latest_frame.copy(), caption, TELEGRAM_CHAT_ID))

    with state_lock:
        last_alert_time[cam_id] = current_time

    print(f"🚨 [{cam_name}] แจ้งเตือนสำเร็จ")
    del results, latest_frame
    gc.collect()


# ==========================================
# --- 2. SET 1 - GROUP 2: EYE, ID CARD & FALL ---
# ==========================================
def process_group2_idcard(cam_info, idcard_model, fall_model):
    cam_id = cam_info['id']
    cam_name = cam_info['name']
    rtsp_url = cam_info['rtsp']

    current_time = time.time()
    with state_lock:
        if current_time - last_alert_time[cam_id] < COOLDOWN_SECONDS:
            return

    is_moving, latest_frame = check_motion_and_capture(cam_id, cam_name, rtsp_url, frame_count=2, delay=0.3)
    if latest_frame is None or not is_moving:
        return

    timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

    fall_detected = detect_fall_in_frame(latest_frame, fall_model)
    if fall_detected:
        alert_header = "🚨🚨 [แจ้งเตือนด่วน: ตรวจพบคนล้ม!]"
        caption = f"{alert_header}\n📹 จาก: {cam_name}\n🕒 เวลา: {timestamp_str}"
        telegram_queue.put((latest_frame.copy(), caption, TELEGRAM_CHAT_ID))
        with state_lock:
            last_alert_time[cam_id] = current_time
        return

    try:
        face_detector = get_face_detector()
        h, w, _ = latest_frame.shape

        mp_img1 = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(latest_frame, cv2.COLOR_BGR2RGB))
        res1 = face_detector.detect(mp_img1)
        if not res1.face_landmarks:
            return

        face1 = res1.face_landmarks[0]
        e1_lx, e1_ly = int(face1[33].x * w), int(face1[33].y * h)
        e1_rx, e1_ry = int(face1[263].x * w), int(face1[263].y * h)
        center1 = ((e1_lx + e1_rx) // 2, (e1_ly + e1_ry) // 2)

        with get_video_capture(rtsp_url) as cap:
            if not cap.isOpened():
                return

            time.sleep(1.0)
            ret2, frame2 = cap.read()
            if not ret2 or frame2 is None: return

            mp_img2 = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame2, cv2.COLOR_BGR2RGB))
            res2 = face_detector.detect(mp_img2)
            if not res2.face_landmarks: return

            face2 = res2.face_landmarks[0]
            center2 = ((int(face2[33].x * w) + int(face2[263].x * w)) // 2,
                       (int(face2[33].y * h) + int(face2[263].y * h)) // 2)

            if (((center2[0] - center1[0]) ** 2 + (center2[1] - center1[1]) ** 2) ** 0.5) > STATIONARY_THRESHOLD_PIXELS:
                return

            time.sleep(1.0)
            ret3, frame3 = cap.read()
            if not ret3 or frame3 is None: return

            mp_img3 = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame3, cv2.COLOR_BGR2RGB))
            res3 = face_detector.detect(mp_img3)
            if not res3.face_landmarks: return

            face3 = res3.face_landmarks[0]
            e3_lx, e3_ly = int(face3[33].x * w), int(face3[33].y * h)
            e3_rx, e3_ry = int(face3[263].x * w), int(face3[263].y * h)
            center3 = ((e3_lx + e3_rx) // 2, (e3_ly + e3_ry) // 2)

            if (((center3[0] - center2[0]) ** 2 + (center3[1] - center2[1]) ** 2) ** 0.5) > STATIONARY_THRESHOLD_PIXELS:
                return

        print(f"✅ [{cam_name}] ยืนยันใบหน้านิ่งครบ 3 เฟรมสำเร็จ!")
        target_frame = frame3.copy()

        has_card = False
        card_boxes = []

        if idcard_model is not None:
            try:
                with model_lock:
                    card_results = idcard_model(target_frame, conf=0.25, verbose=False)
                if len(card_results) > 0 and len(card_results[0].boxes) > 0:
                    card_boxes = card_results[0].boxes
                    has_card = True
            except Exception as e:
                print(f"⚠️ ID Card Model Error: {e}")

        cv2.circle(target_frame, (e3_lx, e3_ly), 4, (0, 0, 255), -1)
        cv2.circle(target_frame, (e3_rx, e3_ry), 4, (0, 0, 255), -1)

        if has_card:
            for cbox in card_boxes:
                cx1, cy1, cx2, cy2 = map(int, cbox.xyxy[0])
                cv2.rectangle(target_frame, (cx1, cy1), (cx2, cy2), (255, 255, 0), 2)
                cv2.putText(target_frame, "ID CARD", (cx1, cy1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)

        eye_dist = abs(e3_rx - e3_lx)
        crop_w = max(int(eye_dist * 6.5), 450)
        crop_h = max(int(eye_dist * 4.0), 300)

        crop_x1 = max(0, center3[0] - (crop_w // 2))
        crop_x2 = min(w, center3[0] + (crop_w // 2))
        crop_y1 = max(0, center3[1] - (crop_h // 3))
        crop_y2 = min(h, center3[1] + (2 * crop_h // 3))

        eye_crop = target_frame[crop_y1:crop_y2, crop_x1:crop_x2]

        cv2.rectangle(target_frame, (crop_x1, crop_y1), (crop_x2, crop_y2), (0, 255, 0), 2)
        cv2.putText(target_frame, "ZOOM: EYES & CARD", (crop_x1, crop_y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        safe_time = timestamp_str.replace(":", "-").replace(" ", "_")
        filename = f"cam2_EYE_CARD_{safe_time}_{int(time.time())}.jpg"
        save_path = os.path.join(SAVE_DIR_ID_CARD, filename)

        img_to_save = eye_crop if (eye_crop is not None and eye_crop.size > 0) else target_frame
        cv2.imwrite(save_path, img_to_save)

        card_status_str = "พบบัตรประชาชน + " if has_card else ""
        main_caption = (f"🪪 [{cam_name}]\n"
                        f"🆔✅ ยืนยันผ่าน 3 เฟรม ({card_status_str}คนยืนนิ่ง)!\n"
                        f"🕒 เวลา: {timestamp_str}")
        telegram_queue.put((target_frame.copy(), main_caption, TELEGRAM_CHAT_ID))

        file_caption = (f"👁️💳 [ภาพซูมเน้นดวงตาและบัตรประชาชน]\n"
                        f"📹 จาก: {cam_name}\n"
                        f"🕒 เวลา: {timestamp_str}")
        telegram_queue.put((None, file_caption, TELEGRAM_CHAT_ID, save_path))

        with state_lock:
            last_alert_time[cam_id] = current_time

    except Exception as e:
        print(f"⚠️ [Group2 Exception]: {e}")
    finally:
        gc.collect()


# ==========================================
# --- 3. SET 1 - GROUP 3: SCHEDULED GUARD & FALL ---
# ==========================================
def process_group3_scheduled(cam_info, model, fall_model):
    cam_id = cam_info['id']
    cam_name = cam_info['name']
    rtsp_url = cam_info['rtsp']

    timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

    is_moving, latest_frame = check_motion_and_capture(cam_id, cam_name, rtsp_url, frame_count=2, delay=2)
    if latest_frame is None:
        return

    fall_detected = detect_fall_in_frame(latest_frame, fall_model)

    with model_lock:
        results = model(latest_frame, classes=[0], verbose=False)

    person_boxes = results[0].boxes
    person_count = len(person_boxes)

    with state_lock:
        if fall_detected:
            caption = f"🚨🚨 [{cam_name}] ตรวจพบคนล้มในพื้นที่!\n🕒 เวลา: {timestamp_str}"
            telegram_queue.put((latest_frame.copy(), caption, TELEGRAM_CHAT_ID))

        elif person_count == 0:
            no_person_counters[cam_id] += 1
            sleeping_counters[cam_id] = 0
            count = no_person_counters[cam_id]
            caption = f"📹 [{cam_name}]\n👤❌ ไม่พบคนในพื้นที่! (ครั้งที่ {count}/{MAX_SLEEP_COUNT})\n🕒 เวลา: {timestamp_str}"
            telegram_queue.put((latest_frame.copy(), caption, TELEGRAM_CHAT_ID))

        elif person_count > 0 and not is_moving:
            sleeping_counters[cam_id] += 1
            no_person_counters[cam_id] = 0
            count = sleeping_counters[cam_id]

            for box in person_boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cv2.rectangle(latest_frame, (x1, y1), (x2, y2), (0, 0, 255), 2)

            caption = f"📹 [{cam_name}]\n😴⚠️ ไม่พบความเคลื่อนไหว (ครั้งที่ {count}/{MAX_SLEEP_COUNT})\n🕒 เวลา: {timestamp_str}"
            telegram_queue.put((latest_frame.copy(), caption, TELEGRAM_CHAT_ID))
        else:
            no_person_counters[cam_id] = 0
            sleeping_counters[cam_id] = 0

    del latest_frame, results
    gc.collect()


# ==========================================
# --- 4. SET 2 - GUARD TOUR & FALL DETECTION ---
# ==========================================
def process_guard_tour_camera(cam_info, main_model, fall_model):
    cam_id = cam_info['id']
    cam_name = cam_info['name']
    rtsp_url = cam_info.get('rtsp')

    if not rtsp_url:
        return

    current_time = time.time()
    cooldown = 3 if cam_id in PERSON_ONLY_CAM_IDS else 30

    with state_lock:
        if current_time - last_alert_time[cam_id] < cooldown:
            return

    min_area = 800 if cam_id in PERSON_ONLY_CAM_IDS else 1500
    is_moving, latest_frame = check_motion_and_capture(cam_id, cam_name, rtsp_url, min_area=min_area)
    if latest_frame is None or not is_moving:
        return

    timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")
    target_classes = [0] if cam_id in PERSON_ONLY_CAM_IDS else PERSON_VEHICLE_CLASSES

    fall_detected = detect_fall_in_frame(latest_frame, fall_model)

    with model_lock:
        results = main_model(latest_frame, classes=target_classes, conf=0.5, verbose=False)

    detected_persons = 0
    detected_vehicles = 0

    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])

        if cls_id == 0:
            detected_persons += 1
            color = (0, 255, 0)
            label = "Person"
        else:
            detected_vehicles += 1
            color = (255, 0, 0)
            label = "Vehicle"

        cv2.rectangle(latest_frame, (x1, y1), (x2, y2), color, 2)
        cv2.putText(latest_frame, label, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    if detected_persons > 0 or detected_vehicles > 0 or fall_detected:
        alert_header = "🚨🚨 [แจ้งเตือนด่วน: ตรวจพบคนล้ม!]" if fall_detected else f"🚨 [{cam_name}]"
        caption = (f"{alert_header}\n"
                   f"📹 จาก: {cam_name}\n"
                   f"👤 พบคน: {detected_persons} คน\n"
                   f"🚗 พบรถ: {detected_vehicles} คัน\n"
                   f"🕒 เวลา: {timestamp_str}")

        telegram_queue.put((latest_frame.copy(), caption, TELEGRAM_CHAT_ID_TOUR))

        with state_lock:
            last_alert_time[cam_id] = current_time

    del latest_frame, results
    gc.collect()


# ==========================================
# --- 5. FIRE & SMOKE DETECTION ---
# ==========================================
def process_fire_detection(cam_info, fire_model):
    if not SYSTEM_CONFIG["enable_fire_alert"]:
        return

    if fire_model is None:
        return

    cam_id = cam_info['id']
    cam_name = cam_info['name']
    rtsp_url = cam_info.get('rtsp')

    if not rtsp_url:
        return

    current_time = time.time()
    with state_lock:
        if current_time - last_alert_time[cam_id] < COOLDOWN_SECONDS:
            return

    is_moving, latest_frame = check_motion_and_capture(cam_id, cam_name, rtsp_url, min_area=1000)
    if latest_frame is None or not is_moving:
        return

    timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

    try:
        with model_lock:
            results = fire_model(latest_frame, conf=0.20, verbose=False)

        detected_labels = []
        if len(results) > 0 and len(results[0].boxes) > 0:
            for box in results[0].boxes:
                cls_id = int(box.cls[0])
                label_name = fire_model.names[cls_id]
                detected_labels.append(label_name)

                x1, y1, x2, y2 = map(int, box.xyxy[0])

                cv2.rectangle(latest_frame, (x1, y1), (x2, y2), (0, 0, 255), 3)

                text = f"FIRE/SMOKE DETECTED: {label_name.upper()}"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.7
                thickness = 2

                (text_w, text_h), _ = cv2.getTextSize(text, font, font_scale, thickness)
                cv2.rectangle(latest_frame, (x1, y1 - text_h - 10), (x1 + text_w + 10, y1), (0, 0, 0), -1)

                cv2.putText(latest_frame, text, (x1 + 5, y1 - 5),
                            font, font_scale, (0, 0, 255), thickness, cv2.LINE_AA)

        if detected_labels:
            labels_str = ", ".join(set(detected_labels))
            caption = (f"🔥🚨 [แจ้งเตือนด่วน: ตรวจพบไฟ/ควัน!]\n"
                       f"📹 จาก: {cam_name}\n"
                       f"⚠️ สัญญาณเตือน: {labels_str}\n"
                       f"🕒 เวลา: {timestamp_str}")

            telegram_queue.put((latest_frame.copy(), caption, TELEGRAM_CHAT_ID))

            with state_lock:
                last_alert_time[cam_id] = current_time

    except Exception as e:
        print(f"⚠️ Fire Detection Error [{cam_name}]: {e}")
    finally:
        del latest_frame
        gc.collect()


# ==========================================
# --- MAIN LOOP & THREAD MANAGEMENT ---
# ==========================================
def main():
    print("🚀 กำลังเริ่มต้นระบบความปลอดภัยและความปลอดภัยด้วยคอมพิวเตอร์วิชั่น...")

    global SERVER_PUBLIC_URL
    try:
        ssl._create_default_https_context = ssl._create_unverified_context

        local_ngrok_path = os.path.join(BASE_DIR, "ngrok.exe")
        pyngrok_config = conf.PyngrokConfig(ngrok_path=local_ngrok_path)

        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            pyngrok_config.startupinfo = startupinfo

        ngrok.set_auth_token("3KzLZu6WHtNGuZWRhlKkqYsjzKG_7jWmXnZbMaXAGq2CMfFA3", pyngrok_config=pyngrok_config)

        # เคลียร์ process ngrok.exe ในระบบ Windows ทันที เพื่อไม่ให้ค้าง Online
        try:
            if os.name == 'nt':
                os.system("taskkill /f /im ngrok.exe >nul 2>&1")
                time.sleep(1)
            tunnels = ngrok.get_tunnels(pyngrok_config=pyngrok_config)
            for t in tunnels:
                ngrok.disconnect(t.public_url, pyngrok_config=pyngrok_config)
            ngrok.kill()
        except Exception:
            pass

        public_tunnel = ngrok.connect(5000, proto="http", pyngrok_config=pyngrok_config)
        SERVER_PUBLIC_URL = public_tunnel.public_url
        print(f"🔗 [ngrok] เปิดใช้งาน Tunnel สำเร็จ! URL ปัจจุบัน: {SERVER_PUBLIC_URL}")

    except Exception as e:
        print(f"⚠️ ไม่สามารถเปิดใช้งาน ngrok ได้: {e}")

    init_ocr()

    # --- เริ่มต้น Thread ตรวจเวลาสำหรับ Auto-Restart (08:00 น.) ---
    t_restart = threading.Thread(target=auto_restart_scheduler, daemon=True)
    t_restart.start()

    t_worker = threading.Thread(target=telegram_worker, daemon=True)
    t_worker.start()

    t_bot = threading.Thread(target=run_telegram_bot, daemon=True)
    t_bot.start()

    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()
    print("🌐 เปิด Web Server สำเร็จ")

    print("⏳ กำลังโหลดโมเดลหลัก YOLOv8...")
    main_model = YOLO("yolov8n.pt")

    print("⏳ กำลังโหลด Custom Model บัตรประชาชน (idcard_detector.pt)...")
    try:
        idcard_model = YOLO("idcard_detector.pt")
        print("✅ โหลด Model บัตรประชาชน สำเร็จ!")
    except Exception as e:
        print(f"⚠️ ไม่สามารถโหลด idcard_model ได้: {e}")
        idcard_model = None

    print("⏳ กำลังโหลด Custom Model ตรวจจับคนล้ม (best.pt)...")
    try:
        fall_model = YOLO("best.pt")
        print("✅ โหลด Model ตรวจจับคนล้ม สำเร็จ!")
    except Exception as e:
        print(f"⚠️ ไม่สามารถโหลด fall_model ได้: {e}")
        fall_model = None

    print("⏳ กำลังโหลด Custom Model ตรวจจับไฟ/ควัน (fire_smoke.pt)...")
    try:
        fire_model = YOLO("fire_smoke.pt")
        print("✅ โหลด Model ตรวจจับไฟ/ควัน สำเร็จ!")
    except Exception as e:
        print(f"⚠️ ไม่สามารถโหลด fire_model ได้: {e}")
        fire_model = None

    print("🟢 ระบบพร้อมทำงานเรียบร้อยแล้ว (ตั้งเวลารีสตาร์ทอัตโนมัติทุกๆ 08:00 น.)...")

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)

    try:
        while True:
            for cam in CAMERAS_GROUP1_MOTION:
                executor.submit(process_group1_motion, cam, main_model, fall_model)

            for cam in CAMERAS_GROUP2_IDCARD:
                executor.submit(process_group2_idcard, cam, idcard_model, fall_model)

            now = datetime.now()
            if now.minute % 5 == 0 and now.second < 10:
                for cam in CAMERAS_GROUP3_SCHEDULED:
                    executor.submit(process_group3_scheduled, cam, main_model, fall_model)

            for cam in ALL_CAMERAS_SET2:
                executor.submit(process_guard_tour_camera, cam, main_model, fall_model)

            for cam in ALL_CAMERAS_SET2:
                executor.submit(process_fire_detection, cam, fire_model)

            time.sleep(1)

    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 กำลังปิดระบบ...")
    except Exception as e:
        print(f"⚠️ Main Loop Error: {e}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()