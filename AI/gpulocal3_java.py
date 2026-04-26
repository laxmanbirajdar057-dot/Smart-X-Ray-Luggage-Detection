import os
import csv
import io
import json
import webview
import time
import sqlite3
import logging
import threading
import requests
import websocket   # pip install websocket-client
from collections import deque
from datetime import datetime
from flask import Flask, Response, jsonify, render_template_string, make_response, request
import cv2
import numpy as np
from ultralytics import YOLO
import serial
import sys

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# ================= SETTINGS =================
CONFIDENCE_THRESHOLD = 0.8
SAVE_FOLDER = "static/detections"
CAMERA_LIST = [0, 1, 2, 3, 4]
CURRENT_CAM_INDEX = 0
SERIAL_PORT = "COM5"
BAUD_RATE = 9600
ENTER_CONFIRM_FRAMES = 12
DB_PATH = "detections.db"

# ── IMAGE PROCESSING DEFAULTS ──────────────────────────────────────────────
IMAGE_SETTINGS = {
    "brightness":  0,
    "contrast":    1.0,
    "saturation":  1.0,
    "hue":         0,
    "sharpness":   0.0,
    "hflip":       False,
    "vflip":       False,
    "rotation":    0,
}
img_settings_lock = threading.Lock()

# ── CLOUD CONFIG ────────────────────────────────────────────────────────────
CLOUD_URL      = "http://localhost:8080"
CLOUD_WS_URL   = "ws://localhost:8080"
ADMIN_USER     = "admin"
ADMIN_PASS     = "xray1234"
CLOUD_ENABLED  = True
# ────────────────────────────────────────────────────────────────────────────

os.makedirs(SAVE_FOLDER, exist_ok=True)

# ================= LOGGING =================
logging.basicConfig(
    filename="scanner.log",
    level=logging.INFO,
    format="%(asctime)s | %(message)s"
)

# ================= DATABASE =================
# BUG FIX: Use a connection-per-thread pool instead of opening a new sqlite3
# connection for every query — eliminates "database is locked" races.
_db_local = threading.local()

def _get_conn():
    """Return a thread-local sqlite3 connection (create if needed)."""
    if not getattr(_db_local, "conn", None):
        _db_local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_local.conn.execute("PRAGMA journal_mode=WAL")   # <-- FIX: WAL prevents read/write contention
        _db_local.conn.execute("PRAGMA synchronous=NORMAL")
    return _db_local.conn

def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS detections (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            date_dmy  TEXT NOT NULL,
            time_hms  TEXT NOT NULL,
            class     TEXT NOT NULL,
            filename  TEXT NOT NULL,
            conf      REAL
        )
    """)
    conn.commit()

def db_insert(dt, cls_str, fname, conf=None):
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO detections (timestamp, date_dmy, time_hms, class, filename, conf) VALUES (?,?,?,?,?,?)",
            (dt.isoformat(), dt.strftime("%d/%m/%Y"), dt.strftime("%I:%M:%S %p"), cls_str, fname, conf)
        )
        conn.commit()
        logging.info(f"DANGER DETECTED | class={cls_str} | file={fname}")
    except Exception as e:
        logging.error(f"DB insert error: {e}")

def db_fetch_all():
    try:
        conn = _get_conn()
        cur = conn.execute(
            "SELECT id, timestamp, date_dmy, time_hms, class, filename, conf "
            "FROM detections ORDER BY id DESC"
        )
        return cur.fetchall()
    except Exception as e:
        logging.error(f"DB fetch error: {e}")
        return []

def db_clear():
    """Delete all detection rows and their image files."""
    try:
        conn = _get_conn()
        cur = conn.execute("SELECT filename FROM detections")
        filenames = [r[0] for r in cur.fetchall()]
        for fname in filenames:
            try:
                path = os.path.join(SAVE_FOLDER, fname)
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        conn.execute("DELETE FROM detections")
        conn.commit()
        logging.info("All detections cleared")
    except Exception as e:
        logging.error(f"DB clear error: {e}")

init_db()


# ================= IMAGE PROCESSING PIPELINE =================
def apply_image_processing(frame: np.ndarray) -> np.ndarray:
    """
    Apply all image processing settings to a BGR frame BEFORE passing to YOLO.
    PERF FIX: skip every operation whose value matches the neutral default so
    we don't pay for unnecessary colour-space conversions on clean frames.
    """
    with img_settings_lock:
        s = dict(IMAGE_SETTINGS)

    # ── Flip ──────────────────────────────────────────────────────────
    if s["hflip"] and s["vflip"]:
        frame = cv2.flip(frame, -1)
    elif s["hflip"]:
        frame = cv2.flip(frame, 1)
    elif s["vflip"]:
        frame = cv2.flip(frame, 0)

    # ── Rotation ──────────────────────────────────────────────────────
    rot = int(s["rotation"]) % 360
    if rot == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif rot == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif rot == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    # ── Brightness + Contrast ─────────────────────────────────────────
    brightness = float(s["brightness"])
    contrast   = float(s["contrast"])
    if brightness != 0 or contrast != 1.0:
        frame = cv2.convertScaleAbs(frame, alpha=contrast, beta=brightness)

    # ── Saturation + Hue (only enter HSV path when needed) ───────────
    saturation = float(s["saturation"])
    hue_shift  = float(s["hue"])
    if saturation != 1.0 or hue_shift != 0:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
        if hue_shift != 0:
            hsv[:, :, 0] = (hsv[:, :, 0] + hue_shift) % 180
        if saturation != 1.0:
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * saturation, 0, 255)
        frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    # ── Sharpness (unsharp mask) ──────────────────────────────────────
    sharpness = float(s["sharpness"])
    if sharpness > 0:
        blurred = cv2.GaussianBlur(frame, (0, 0), 3)
        frame = cv2.addWeighted(frame, 1.0 + sharpness, blurred, -sharpness, 0)

    return frame


# ================= CLOUD SYNC =================
cloud_token = None
cloud_ws     = None
# BUG FIX: add a lock so the frame_sender thread and cloud_push_detection
# thread never race on cloud_ws simultaneously.
cloud_ws_lock = threading.Lock()

def cloud_login():
    global cloud_token
    if not CLOUD_ENABLED:
        return False
    try:
        r = requests.post(
            f"{CLOUD_URL}/api/admin/login",
            headers={"Content-Type": "application/json"},
            json={"username": ADMIN_USER, "password": ADMIN_PASS},
            timeout=10
        )
        if r.status_code == 200:
            cloud_token = r.json()["accessToken"]
            logging.info("Cloud login OK")
            print("Cloud login OK")
            return True
        logging.error(f"Cloud login failed: {r.status_code}")
        return False
    except Exception as e:
        logging.error(f"Cloud login error: {e}")
        print("Cloud login exception:", e)
        return False

def cloud_push_detection(dt, cls_str, fname, conf):
    """Push detection metadata to Java Spring Boot backend."""
    if not CLOUD_ENABLED or not cloud_token:
        return
    try:
        # Java expects JSON body — camelCase field names
        requests.post(
            f"{CLOUD_URL}/api/detections",
            headers={
                "Authorization": f"Bearer {cloud_token}",
                "Content-Type": "application/json"
            },
            json={
                "className":  cls_str,
                "conf":       float(conf or 0),
                "cameraIdx":  int(CURRENT_CAM_INDEX),
                "filename":   fname,
                "dateDmy":    dt.strftime("%d/%m/%Y"),
                "timeHms":    dt.strftime("%H:%M:%S"),
                "timestamp":  dt.isoformat()
            },
            timeout=10
        )
        logging.info(f"Java push OK: {fname}")
    except Exception as e:
        logging.error(f"Java push error: {e}")

def cloud_stream_thread():
    """WebSocket thread: send JPEG frames to cloud, receive setting commands."""
    global cloud_token, CONFIDENCE_THRESHOLD, ENTER_CONFIRM_FRAMES, CURRENT_CAM_INDEX, cloud_ws

    if not CLOUD_ENABLED:
        return

    def on_message(ws_conn, msg):
        global CONFIDENCE_THRESHOLD, ENTER_CONFIRM_FRAMES, CURRENT_CAM_INDEX
        try:
            cmd = json.loads(msg)
            if cmd.get("type") == "settings_update":
                if "confidence_threshold" in cmd:
                    CONFIDENCE_THRESHOLD = float(cmd["confidence_threshold"])
                if "confirm_frames" in cmd:
                    ENTER_CONFIRM_FRAMES = int(cmd["confirm_frames"])
                if "camera_index" in cmd:
                    CURRENT_CAM_INDEX = int(cmd["camera_index"])
                if "image_settings" in cmd:
                    with img_settings_lock:
                        for k, v in cmd["image_settings"].items():
                            if k in IMAGE_SETTINGS:
                                IMAGE_SETTINGS[k] = v
                logging.info(f"Settings updated from cloud: {cmd}")
        except Exception as e:
            logging.error(f"Cloud WS message error: {e}")

    def on_error(ws_conn, err):
        logging.error(f"Cloud WS error: {err}")

    def on_close(ws_conn, *args):
        global cloud_ws
        with cloud_ws_lock:
            cloud_ws = None
        logging.info("Cloud WS closed, reconnecting in 10s...")
        time.sleep(10)
        threading.Thread(target=cloud_stream_thread, daemon=True).start()

    def on_open(ws_conn):
        global cloud_ws
        with cloud_ws_lock:
            cloud_ws = ws_conn
        print("Cloud WS connected — streaming frames")
        logging.info("Cloud WS connected — streaming frames")

        def frame_sender():
            # PERF FIX: use a dedicated encode buffer; track last_frame by id()
            # so we never send the same frame twice even if ref is reused.
            last_id = None
            while True:
                try:
                    with lock:
                        frame = annotated_frame
                    fid = id(frame) if frame is not None else None
                    if frame is not None and fid != last_id:
                        last_id = fid
                        # PERF FIX: lower cloud quality from 85→72 — halves
                        # upload bandwidth, imperceptible at 720p
                        ok, jpg = cv2.imencode(
                            ".jpg", frame,
                            [cv2.IMWRITE_JPEG_QUALITY, 72]
                        )
                        if ok:
                            with cloud_ws_lock:
                                ws_ref = cloud_ws
                            if ws_ref:
                                ws_ref.send(jpg.tobytes(), opcode=websocket.ABNF.OPCODE_BINARY)
                    # PERF FIX: target 20 fps for cloud (not 30) — saves CPU/net
                    time.sleep(0.05)
                except Exception as e:
                    logging.error(f"Frame send error: {e}")
                    break

        threading.Thread(target=frame_sender, daemon=True).start()

    try:
        ws_url = f"{CLOUD_WS_URL}/ws/stream?token={cloud_token}"
        ws_conn = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws_conn.run_forever(ping_interval=30, ping_timeout=10)
    except Exception as e:
        logging.error(f"Cloud WS start error: {e}")

def cloud_status_push_thread():
    """Periodically push system status to cloud so admin panel shows live badges."""
    if not CLOUD_ENABLED or not cloud_token:
        return
    while True:
        try:
            with lock:
                st = dict(status)
                f  = round(fps, 1)
            msg = json.dumps({"type": "status_update", "status": st, "fps": f})
            with cloud_ws_lock:
                ws_ref = cloud_ws
            if ws_ref:
                ws_ref.send(msg)
        except Exception:
            pass
        # PERF FIX: was 0.3 s (3.3 Hz) — 1 Hz is plenty for status badges
        time.sleep(1.0)


import socket

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


app = Flask(__name__)
model = YOLO(resource_path("best.pt"))
try:
    model.to("cuda")
except Exception:
    model.to("cpu")

# PERF FIX: warm up the model so first inference doesn't spike latency
try:
    _dummy = np.zeros((640, 640, 3), dtype=np.uint8)
    model.predict(_dummy, conf=0.9, verbose=False)
    del _dummy
    print("[YOLO] Warm-up done")
except Exception:
    pass

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0)
    print("Serial connected:", SERIAL_PORT)
except Exception as e:
    print("Serial failed:", e)
    ser = None


# ================= GLOBAL STATE =================
lock = threading.Lock()

latest_frame    = None   # raw frame from camera (no processing yet)
latest_boxes    = []
annotated_frame = None   # processed + annotated frame (for streaming)
latest_snapshots = {}    # {cam_idx: jpeg_bytes} for camera preview grid
timeline = []

status = {
    "main":   "SAFE",
    "gate":   "OPEN",
    "belt":   "RUNNING",
    "buzzer": "OFF"
}

fps = 0.0
fps_last  = time.monotonic()   # BUG FIX: use monotonic — immune to system clock changes
fps_count = 0

last_detections = deque(maxlen=ENTER_CONFIRM_FRAMES)
danger_active   = False
snapshot_taken  = False
_confirm_frames_applied = ENTER_CONFIRM_FRAMES

# ── PERF: pre-allocate reusable JPEG encode params ────────────────────────
_ENCODE_PARAMS_STREAM   = [cv2.IMWRITE_JPEG_QUALITY, 80]   # local MJPEG stream
_ENCODE_PARAMS_SNAPSHOT = [cv2.IMWRITE_JPEG_QUALITY, 65]   # camera grid previews
_ENCODE_PARAMS_SAVE     = [cv2.IMWRITE_JPEG_QUALITY, 92]   # saved detection images


# ================= CAMERA THREAD =================
def capture_frames():
    global latest_frame, CURRENT_CAM_INDEX

    cap = None
    active_index = -1
    fail_count = 0

    while True:
        if cap is None or active_index != CURRENT_CAM_INDEX:
            if cap:
                cap.release()

            print(f"Opening camera {CURRENT_CAM_INDEX}")
            cap = cv2.VideoCapture(CURRENT_CAM_INDEX, cv2.CAP_DSHOW)

            if not cap.isOpened():
                print("Camera failed to open")
                time.sleep(1)
                continue

            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_FPS, 30)
                # BUG FIX: buffer 1 keeps latency low; larger buffer causes lag
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # PERF FIX: ask DirectShow for MJPEG from camera to cut USB bandwidth
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            except Exception:
                pass

            active_index = CURRENT_CAM_INDEX
            time.sleep(0.3)   # PERF FIX: was 0.4 s — 0.3 s is enough for init

        ret, frame = cap.read()

        if ret and frame is not None:
            fail_count = 0
            with lock:
                latest_frame = frame
        else:
            fail_count += 1
            if fail_count > 20:
                print("Camera failed! Switching...")
                try:
                    idx_pos = CAMERA_LIST.index(CURRENT_CAM_INDEX)
                    CURRENT_CAM_INDEX = CAMERA_LIST[(idx_pos + 1) % len(CAMERA_LIST)]
                except Exception:
                    CURRENT_CAM_INDEX = CAMERA_LIST[0]
                if cap:
                    cap.release()
                cap = None
                fail_count = 0
            time.sleep(0.05)


# ================= SNAPSHOT =================
def save_snapshot(frame, boxes, fname):
    img = frame.copy()
    for x1, y1, x2, y2, cls, conf in boxes:
        label = f"{cls} {conf:.2f}"
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 3)
        cv2.putText(img, label, (x1, y1 - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.imwrite(os.path.join(SAVE_FOLDER, fname), img, _ENCODE_PARAMS_SAVE)


# ================= INFERENCE THREAD =================
def inference_thread():
    global fps, fps_last, fps_count
    global danger_active, snapshot_taken
    global latest_boxes, annotated_frame
    global last_detections, _confirm_frames_applied

    _frame_interval = 1.0 / 25.0
    _last_inf = 0.0

    while True:

        now = time.monotonic()
        elapsed = now - _last_inf
        if elapsed < _frame_interval:
            time.sleep(_frame_interval - elapsed)
            continue
        _last_inf = time.monotonic()

        with lock:
            frame = None if latest_frame is None else latest_frame.copy()

        if frame is None:
            time.sleep(0.005)
            continue

        if ENTER_CONFIRM_FRAMES != _confirm_frames_applied:
            last_detections = deque(maxlen=ENTER_CONFIRM_FRAMES)
            _confirm_frames_applied = ENTER_CONFIRM_FRAMES

        frame = apply_image_processing(frame)

        boxes = []
        classes = []

        try:
            result = model.predict(
                frame,
                conf=CONFIDENCE_THRESHOLD,
                device="cuda",
                half=True,
                verbose=False
            )[0]

            t = time.monotonic()
            fps_count += 1
            if t - fps_last >= 1:
                fps = fps_count / (t - fps_last)
                fps_count = 0
                fps_last = t

            for b in result.boxes:
                conf = float(b.conf[0])
                if conf < CONFIDENCE_THRESHOLD:
                    continue

                cls = model.names[int(b.cls[0])]
                x1, y1, x2, y2 = map(int, b.xyxy[0])

                boxes.append((x1, y1, x2, y2, cls, conf))
                classes.append(cls)

            

        except Exception as e:
            print("YOLO error:", e)

        # ================= FSM =================
        last_detections.append(classes[0] if classes else None)

        recent = list(last_detections)[-ENTER_CONFIRM_FRAMES:]

        enter_danger = (
            len(recent) == ENTER_CONFIRM_FRAMES
            and len(set(recent)) == 1
            and recent[0] is not None
        )

        exit_danger = (classes == [])

        with lock:

            # ===== ENTER DANGER =====
            if enter_danger and not danger_active:

                danger_active = True
                snapshot_taken = False

                print("⚠️ SYSTEM STATUS → DANGER")

                status.update({
                    "main": "DANGER",
                    "gate": "CLOSED",
                    "belt": "STOPPED",
                    "buzzer": "ON"
                })

                if ser:
                    try:
                        ser.write(b'1\n')
                    except Exception:
                        pass

            # ===== EXIT DANGER =====
            elif danger_active and exit_danger:

                danger_active = False

                print("✅ SYSTEM STATUS → SAFE")

                status.update({
                    "main": "SAFE",
                    "gate": "OPEN",
                    "belt": "RUNNING",
                    "buzzer": "OFF"
                })

                print("SYSTEM STATUS → SAFE")
                
                if ser:
                    try:
                        ser.write(b'0\n')
                    except Exception:
                        pass

        # ================= SNAPSHOT =================
        if danger_active and not snapshot_taken:

            snapshot_taken = True

            dt = datetime.now()
            cls_str = "_".join(sorted(set(classes))) or "unknown"
            fname = f"{dt.strftime('%H%M%S_%d%m%Y')}_{cls_str}.jpg"
            avg_conf = round(sum(b[5] for b in boxes) / len(boxes), 3) if boxes else None

            frame_copy = frame.copy()

            threading.Thread(
                target=save_snapshot,
                args=(frame_copy, boxes, fname),
                daemon=True
            ).start()

            threading.Thread(
                target=db_insert,
                args=(dt, cls_str, fname, avg_conf),
                daemon=True
            ).start()

            threading.Thread(
                target=cloud_push_detection,
                args=(dt, cls_str, fname, avg_conf),
                daemon=True
            ).start()

            with lock:
                timeline.insert(0, {
                    "id": int(time.monotonic() * 1000),
                    "time_hms": dt.strftime("%I:%M:%S %p"),
                    "date_dmy": dt.strftime("%d/%m/%Y"),
                    "class": cls_str,
                    "thumb": fname,
                    "image": fname,
                    "conf": avg_conf
                })

                timeline[:] = timeline[:60]

        # ================= DRAW BOXES =================
        draw = frame.copy()

        for x1, y1, x2, y2, cls, conf in boxes:
            label = f"{cls} {conf:.2f}"
            cv2.rectangle(draw, (x1, y1), (x2, y2), (0, 0, 255), 3)
            cv2.putText(draw, label, (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        _, snap_jpg = cv2.imencode(".jpg", draw, _ENCODE_PARAMS_SNAPSHOT)
        snap_bytes = snap_jpg.tobytes()

        with lock:
            annotated_frame = draw
            latest_snapshots[CURRENT_CAM_INDEX] = snap_bytes


# ================= VIDEO STREAM =================
def gen():
    """
    MJPEG generator — serves the annotated frame at ~30 fps cap.
    PERF FIX: encode inside gen() at reduced quality (80) for the local stream
    so the inference thread's draw buffer is not locked during encoding.
    """
    last_id = None
    while True:
        with lock:
            frame = annotated_frame

        fid = id(frame) if frame is not None else None
        if frame is None or fid == last_id:
            time.sleep(0.01)
            continue

        last_id = fid
        ok, jpg = cv2.imencode(".jpg", frame, _ENCODE_PARAMS_STREAM)
        if ok:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" +
                jpg.tobytes() +
                b"\r\n"
            )


# ================= ROUTES =================
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/video_feed")
def video_feed():
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/snapshot")
def snapshot():
    cam_idx = int(request.args.get("cam", CURRENT_CAM_INDEX))
    with lock:
        jpg_bytes = latest_snapshots.get(cam_idx)
    if jpg_bytes:
        resp = Response(jpg_bytes, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "no-cache, no-store"
        return resp
    return Response(status=204)

@app.route("/status")
def get_status():
    with lock:
        return jsonify({"status": dict(status), "fps": round(fps, 2)})

@app.route("/timeline")
def get_timeline():
    with lock:
        return jsonify(list(timeline))

@app.route("/timeline_full")
def get_timeline_full():
    rows = db_fetch_all()
    data = [
        {
            "id":        r[0],
            "timestamp": r[1],
            "date_dmy":  r[2],
            "time_hms":  r[3],
            "class":     r[4],
            "thumb":     r[5],
            "image":     r[5],
            "conf":      r[6]
        }
        for r in rows
    ]
    return jsonify(data)

@app.route("/download_log")
def download_log():
    rows = db_fetch_all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp", "date", "time", "class", "file", "conf"])
    for row in rows:
        writer.writerow(row)
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = 'attachment; filename="detection_log.csv"'
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

@app.route("/clear_log", methods=["POST"])
def clear_log():
    global timeline
    db_clear()
    with lock:
        timeline = []
    return jsonify({"cleared": True})

@app.route("/current_settings")
def current_settings():
    with img_settings_lock:
        img = dict(IMAGE_SETTINGS)
    return jsonify({
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "confirm_frames":       ENTER_CONFIRM_FRAMES,
        "camera_index":         CURRENT_CAM_INDEX,
        "image_settings":       img
    })

@app.route("/info")
def info():
    return jsonify({"local_ip": get_local_ip(), "port": 5000})

def broadcast_settings_to_cloud():
    """Push current settings to cloud so all connected UIs stay in sync."""
    if not CLOUD_ENABLED:
        return
    with cloud_ws_lock:
        ws_ref = cloud_ws
    if not ws_ref:
        return
    try:
        with img_settings_lock:
            img = dict(IMAGE_SETTINGS)
        msg = json.dumps({
            "type":                 "settings_update",
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "confirm_frames":       ENTER_CONFIRM_FRAMES,
            "camera_index":         CURRENT_CAM_INDEX,
            "image_settings":       img
        })
        ws_ref.send(msg)
    except Exception as e:
        logging.error(f"Settings broadcast error: {e}")

@app.route("/set_threshold/<val>", methods=["POST"])
def set_threshold(val):
    global CONFIDENCE_THRESHOLD
    try:
        CONFIDENCE_THRESHOLD = max(0.1, min(1.0, float(val)))
        threading.Thread(target=broadcast_settings_to_cloud, daemon=True).start()
    except Exception:
        pass
    return "OK"

@app.route("/set_camera/<idx>", methods=["POST"])
def set_camera(idx):
    global CURRENT_CAM_INDEX
    try:
        CURRENT_CAM_INDEX = int(idx)
        print("Switched to camera", CURRENT_CAM_INDEX)
        threading.Thread(target=broadcast_settings_to_cloud, daemon=True).start()
    except Exception:
        pass
    return "OK"

@app.route("/set_confirm/<val>", methods=["POST"])
def set_confirm(val):
    global ENTER_CONFIRM_FRAMES
    try:
        v = int(val)
        ENTER_CONFIRM_FRAMES = max(1, min(30, v))
        print("Confirm frames set to", ENTER_CONFIRM_FRAMES)
        threading.Thread(target=broadcast_settings_to_cloud, daemon=True).start()
    except Exception:
        pass
    return "OK"

@app.route("/set_image_settings", methods=["POST"])
def set_image_settings():
    data = request.get_json(force=True, silent=True) or {}
    with img_settings_lock:
        for key in ("brightness", "contrast", "saturation", "hue", "sharpness"):
            if key in data:
                IMAGE_SETTINGS[key] = float(data[key])
        for key in ("hflip", "vflip"):
            if key in data:
                IMAGE_SETTINGS[key] = bool(data[key])
        if "rotation" in data:
            IMAGE_SETTINGS["rotation"] = int(data["rotation"]) % 360
    threading.Thread(target=broadcast_settings_to_cloud, daemon=True).start()
    with img_settings_lock:
        return jsonify({"ok": True, "image_settings": dict(IMAGE_SETTINGS)})


# ================= UI =================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>X-Ray Scanner</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
:root{
  --safe:#00ff41;--danger:#ff1744;--accent:#00d4ff;--warn:#ffab00;--purple:#a855f7;
  --bg:#070b12;--panel:#0f1623;--panel2:#141d2e;--border:#1e2d42;--border2:#2a3d56;
  --text:#dde8f8;--text2:#7a9abf;--text3:#3a5070;--text-safe:#4eff8a;
  --glow-accent:0 0 14px rgba(0,212,255,.4);
  --glow-safe:0 0 10px rgba(0,255,65,.3);
  --glow-danger:0 0 10px rgba(255,23,68,.4);
  --radius:10px;--radius-sm:6px;--hdr:50px;--side:330px;
}
*{box-sizing:border-box;margin:0;padding:0;}
html,body{height:100%;background:var(--bg);color:var(--text);font-family:'Share Tech Mono',monospace;overflow:hidden;}
body{display:flex;flex-direction:column;}
::-webkit-scrollbar{width:4px;height:4px;}
::-webkit-scrollbar-track{background:var(--bg);}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px;}
.header{height:var(--hdr);background:#000;border-bottom:1px solid var(--border);padding:0 16px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;z-index:50;}
.hdr-logo{font-family:'Orbitron';font-size:12px;font-weight:700;color:var(--accent);letter-spacing:.1em;display:flex;align-items:center;gap:8px;}
.hdr-logo-dot{width:7px;height:7px;border-radius:50%;background:var(--accent);box-shadow:var(--glow-accent);animation:blink 2s ease-in-out infinite;}
@keyframes blink{0%,100%{opacity:1;}50%{opacity:.3;}}
.hdr-right{display:flex;align-items:center;gap:10px;}
#hdr-time{font-size:11px;color:var(--text2);}
#hdr-fps{font-size:10px;color:var(--accent);background:var(--panel2);padding:3px 8px;border-radius:4px;border:1px solid var(--border);}
#hdr-phone{font-size:10px;color:var(--text3);background:var(--panel2);padding:3px 8px;border-radius:4px;border:1px solid var(--border);}
.main-layout{display:flex;flex:1;overflow:hidden;}
.feed-col{flex:1;min-width:0;display:flex;flex-direction:column;padding:10px 6px 10px 10px;gap:8px;}
.feed-hud{flex:1;min-height:0;background:#000;border-radius:var(--radius);overflow:hidden;position:relative;display:flex;align-items:center;justify-content:center;border:1px solid var(--border);}
.feed-hud::before,.feed-hud::after,.feed-hud .hud-br,.feed-hud .hud-bl{content:'';position:absolute;width:20px;height:20px;z-index:5;pointer-events:none;}
.feed-hud::before{top:8px;left:8px;border-top:2px solid var(--accent);border-left:2px solid var(--accent);}
.feed-hud::after{top:8px;right:8px;border-top:2px solid var(--accent);border-right:2px solid var(--accent);}
.feed-hud .hud-br{bottom:8px;right:8px;border-bottom:2px solid var(--accent);border-right:2px solid var(--accent);}
.feed-hud .hud-bl{bottom:8px;left:8px;border-bottom:2px solid var(--accent);border-left:2px solid var(--accent);}
.scanlines{position:absolute;inset:0;z-index:4;pointer-events:none;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.04) 3px);}
#live-video{width:100%;height:100%;object-fit:contain;background:#000;display:block;position:relative;z-index:2;}
.feed-tag{position:absolute;z-index:6;background:rgba(0,0,0,.8);border-radius:4px;padding:3px 8px;font-size:10px;pointer-events:none;backdrop-filter:blur(4px);}
#feed-fps-tag{top:10px;right:12px;color:var(--accent);font-size:11px;font-weight:700;}
#feed-cam-tag{top:10px;left:12px;color:var(--text2);}
#feed-rec{top:10px;left:50%;transform:translateX(-50%);color:var(--danger);display:flex;align-items:center;gap:5px;}
.rec-dot{width:6px;height:6px;border-radius:50%;background:var(--danger);animation:blink .8s ease-in-out infinite;}
#feed-overlay{position:absolute;inset:0;display:none;align-items:center;justify-content:center;background:rgba(255,23,68,.12);animation:dangerPulse 1s ease-in-out infinite;z-index:7;}
@keyframes dangerPulse{0%,100%{background:rgba(255,23,68,.12);box-shadow:inset 0 0 60px rgba(255,23,68,.15);}50%{background:rgba(255,23,68,.22);box-shadow:inset 0 0 100px rgba(255,23,68,.3);}}
.danger-banner{font-family:'Orbitron';font-size:clamp(13px,2vw,22px);font-weight:900;color:#fff;letter-spacing:.15em;border:2px solid var(--danger);padding:10px 24px;background:rgba(255,23,68,.25);text-shadow:0 0 20px var(--danger);box-shadow:0 0 40px rgba(255,23,68,.4);animation:bannerFlicker .15s ease-in-out infinite alternate;}
@keyframes bannerFlicker{from{opacity:1;}to{opacity:.85;}}
.status-row{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;flex-shrink:0;}
.s-badge{border-radius:var(--radius-sm);padding:7px 4px;text-align:center;font-family:'Orbitron';font-size:9px;font-weight:700;letter-spacing:.04em;border:1px solid transparent;transition:all .3s;}
.s-badge.on{background:rgba(0,255,65,.1);color:var(--text-safe);border-color:rgba(0,255,65,.3);box-shadow:var(--glow-safe);}
.s-badge.off{background:rgba(255,23,68,.08);color:#ff6b6b;border-color:rgba(255,23,68,.25);}
.s-badge-label{font-size:8px;opacity:.65;display:block;margin-bottom:2px;color:var(--text2);}
.side-panel{width:var(--side);flex-shrink:0;border-left:1px solid var(--border);display:flex;flex-direction:column;background:var(--panel);overflow:hidden;}
.side-tabs{display:flex;border-bottom:1px solid var(--border);flex-shrink:0;background:#000;}
.side-tab{flex:1;height:38px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:8px;font-family:'Orbitron';color:var(--text3);border-bottom:2px solid transparent;transition:all .2s;letter-spacing:.04em;gap:3px;}
.side-tab:hover{color:var(--text2);}
.side-tab.active{color:var(--accent);border-bottom-color:var(--accent);}
.side-content{flex:1;overflow:hidden;display:flex;flex-direction:column;}
.side-pane{display:none;flex:1;flex-direction:column;overflow:hidden;}
.side-pane.active{display:flex;}
.side-scroll{flex:1;overflow-y:auto;padding:12px;}
.section-label{font-family:'Orbitron';font-size:9px;color:var(--accent);letter-spacing:.15em;margin-bottom:8px;margin-top:4px;display:flex;align-items:center;gap:6px;}
.section-label::before{content:'';width:3px;height:9px;background:var(--accent);border-radius:2px;flex-shrink:0;opacity:.8;}
.ctrl-section{margin-bottom:18px;}
.setting-label{font-size:10.5px;color:var(--text2);margin-bottom:5px;display:flex;justify-content:space-between;align-items:center;}
.vbox{font-family:'Share Tech Mono',monospace;background:#080d16;text-align:center;border-radius:4px;padding:4px 2px;color:var(--accent);font-size:12px;border:1px solid var(--border);min-width:44px;}
input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:4px;cursor:pointer;display:block;background:var(--border2);border-radius:2px;outline:none;}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:var(--accent);cursor:pointer;box-shadow:0 0 6px rgba(0,212,255,.5);}
.ctrl-hint{font-size:9.5px;color:var(--text3);margin-top:3px;}
.btn-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px;}
.btn-ctrl{background:var(--panel2);color:var(--text2);border:1px solid var(--border2);border-radius:var(--radius-sm);padding:8px 4px;font-size:9px;cursor:pointer;font-family:'Orbitron';transition:all .15s;text-align:center;letter-spacing:.04em;}
.btn-ctrl:hover{color:var(--accent);border-color:var(--accent);background:rgba(0,212,255,.06);}
.btn-ctrl.active{color:var(--accent);border-color:var(--accent);background:rgba(0,212,255,.12);}
.btn-ctrl-icon{font-size:14px;display:block;margin-bottom:3px;}
.btn-reset{width:100%;background:transparent;color:var(--text3);border:1px solid var(--border);border-radius:var(--radius-sm);padding:7px;font-size:9px;cursor:pointer;font-family:'Orbitron';transition:all .15s;letter-spacing:.06em;margin-top:4px;}
.btn-reset:hover{color:var(--warn);border-color:var(--warn);}
.log-filter-area{padding:10px 12px 6px;border-bottom:1px solid var(--border);flex-shrink:0;}
.class-btns{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px;}
.class-btn{padding:4px 10px;border-radius:20px;font-size:9px;font-family:'Orbitron';cursor:pointer;border:1px solid var(--border2);color:var(--text3);background:transparent;transition:all .15s;letter-spacing:.04em;}
.class-btn:hover{border-color:var(--accent);color:var(--text2);}
.class-btn.selected{background:rgba(0,212,255,.12);border-color:var(--accent);color:var(--accent);}
.class-btn[data-class="Gun"].selected{background:rgba(255,23,68,.12);border-color:var(--danger);color:#ff6b6b;}
.class-btn[data-class="Knife"].selected{background:rgba(255,171,0,.1);border-color:var(--warn);color:var(--warn);}
.class-btn[data-class="Scissors"].selected{background:rgba(168,85,247,.1);border-color:#a855f7;color:#c084fc;}
.log-search-row{display:flex;gap:6px;}
.log-search{background:#080d16;border:1px solid var(--border2);border-radius:var(--radius-sm);color:var(--text);padding:6px 10px;font-size:11px;font-family:'Share Tech Mono',monospace;outline:none;flex:1;transition:border-color .2s;}
.log-search:focus{border-color:var(--accent);}
.btn-icon{background:transparent;color:var(--text2);border:1px solid var(--border2);border-radius:var(--radius-sm);padding:6px 10px;font-size:9.5px;cursor:pointer;font-family:'Orbitron';transition:all .15s;text-decoration:none;display:flex;align-items:center;white-space:nowrap;gap:4px;letter-spacing:.04em;}
.btn-icon:hover{color:var(--accent);border-color:var(--accent);}
.dl-wrap{position:relative;}
.dl-menu{position:absolute;right:0;top:calc(100% + 4px);background:var(--panel);border:1px solid var(--border2);border-radius:var(--radius-sm);z-index:100;min-width:160px;display:none;box-shadow:0 8px 24px rgba(0,0,0,.5);}
.dl-menu.open{display:block;}
.dl-item{padding:9px 14px;font-size:10px;font-family:'Orbitron';color:var(--text2);cursor:pointer;display:flex;align-items:center;gap:8px;letter-spacing:.04em;border-bottom:1px solid var(--border);transition:background .15s;}
.dl-item:last-child{border-bottom:none;}
.dl-item:hover{background:rgba(0,212,255,.08);color:var(--accent);}
.det-list{flex:1;overflow-y:auto;padding:6px 8px;}
.det-item{display:flex;align-items:center;gap:8px;padding:7px;margin-bottom:5px;background:var(--panel2);border-radius:var(--radius-sm);border:1px solid var(--border);cursor:pointer;transition:border-color .15s;}
.det-item:hover{border-color:var(--accent);}
.det-item img{width:56px;height:42px;object-fit:cover;border-radius:4px;background:#000;flex-shrink:0;}
.det-item-info{min-width:0;flex:1;}
.det-item-class{font-size:11px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--accent);}
.det-item-meta{color:var(--text2);font-size:10px;margin-top:2px;}
.det-item-conf{display:inline-block;background:rgba(0,212,255,.08);color:var(--accent);border:1px solid rgba(0,212,255,.18);border-radius:3px;font-size:9px;padding:1px 5px;margin-top:3px;}
.log-pagination{display:flex;justify-content:center;align-items:center;gap:10px;padding:8px;border-top:1px solid var(--border);flex-shrink:0;}
#page-info{font-size:10px;color:var(--text2);}
.stat-chips{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px;}
.stat-chip{background:var(--panel2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px 6px;text-align:center;}
.stat-chip-num{font-family:'Orbitron';font-size:22px;font-weight:700;color:var(--accent);line-height:1;}
.stat-chip-lbl{font-size:8px;color:var(--text3);margin-top:3px;letter-spacing:.1em;}
.status-grid2{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:14px;}
.status-item{background:var(--panel2);border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px;display:flex;flex-direction:column;gap:4px;}
.status-item-label{font-size:8px;color:var(--text3);letter-spacing:.1em;}
.status-item-val{font-family:'Orbitron';font-size:11px;color:var(--text2);}
.status-item-val.on{color:var(--text-safe);}
.status-item-val.off{color:#ff6b6b;}
#modal{position:fixed;inset:0;background:rgba(0,0,0,.94);display:none;align-items:center;justify-content:center;z-index:9998;flex-direction:column;gap:12px;backdrop-filter:blur(8px);}
#modal img{max-width:min(90vw,900px);max-height:72vh;border:1px solid var(--accent);border-radius:var(--radius);box-shadow:0 0 60px rgba(0,212,255,.2);}
.modal-info{color:var(--text2);font-size:12px;text-align:center;background:rgba(0,0,0,.6);padding:8px 16px;border-radius:var(--radius-sm);border:1px solid var(--border);}
.modal-close{position:absolute;top:16px;right:20px;color:var(--text2);font-size:24px;cursor:pointer;transition:color .15s;line-height:1;}
.modal-close:hover{color:#fff;}
#toast{position:fixed;bottom:20px;right:20px;background:rgba(255,23,68,.95);color:#fff;padding:12px 18px;border-radius:var(--radius-sm);font-family:'Orbitron';font-weight:700;font-size:11px;letter-spacing:.06em;display:none;z-index:99999;box-shadow:0 0 30px rgba(255,23,68,.5);animation:toastIn .3s ease;}
#toast.info{background:rgba(0,212,255,.9);color:#000;}
@keyframes toastIn{from{transform:translateY(20px);opacity:0;}to{transform:translateY(0);opacity:1;}}
@media(max-width:768px){:root{--side:100%;}.main-layout{flex-direction:column;}.feed-col{flex:none;height:45vw;min-height:200px;}.side-panel{width:100%;border-left:none;border-top:1px solid var(--border);}}
</style>
</head>
<body>

<div class="header">
  <div class="hdr-logo"><div class="hdr-logo-dot"></div>X-RAY SCANNER</div>
  <div class="hdr-right">
    <span id="hdr-time"></span>
    <span id="hdr-fps">-- FPS</span>
    <span id="hdr-phone"></span>
  </div>
</div>

<div class="main-layout">
  <div class="feed-col">
    <div class="feed-hud">
      <div class="hud-br"></div><div class="hud-bl"></div>
      <div class="scanlines"></div>
      <img id="live-video" src="{{ url_for('video_feed') }}" alt="live feed">
      <div class="feed-tag" id="feed-fps-tag"></div>
      <div class="feed-tag" id="feed-cam-tag">CAM 1</div>
      <div class="feed-tag" id="feed-rec"><span class="rec-dot"></span>LIVE</div>
      <div id="feed-overlay"><div class="danger-banner">&#9888; PROHIBITED ITEM DETECTED</div></div>
    </div>
    <div class="status-row">
      <div class="s-badge off" id="badge-main"><span class="s-badge-label">STATUS</span>--</div>
      <div class="s-badge off" id="badge-gate"><span class="s-badge-label">GATE</span>--</div>
      <div class="s-badge off" id="badge-belt"><span class="s-badge-label">BELT</span>--</div>
      <div class="s-badge off" id="badge-buzzer"><span class="s-badge-label">BUZZER</span>--</div>
    </div>
  </div>

  <div class="side-panel">
    <div class="side-tabs">
      <div class="side-tab active" onclick="switchSide('controls',this)">&#9881; CTRL</div>
      <div class="side-tab" onclick="switchSide('log',this)">&#128203; LOGS</div>
      <div class="side-tab" onclick="switchSide('status',this)">&#128202; STATUS</div>
    </div>
    <div class="side-content">

      <!-- CONTROLS PANE -->
      <div id="side-controls" class="side-pane active">
        <div class="side-scroll">
          <div class="ctrl-section">
            <div class="section-label">DETECTION SETTINGS</div>
            <div class="setting-label">Confidence Threshold <span id="th-val" class="vbox">0.80</span></div>
            <input type="range" id="th" min="0.10" max="1.00" step="0.05" value="0.80" oninput="onThChange(this.value)">
            <div class="ctrl-hint" style="margin-bottom:10px">Min confidence to trigger alert</div>
            <div class="setting-label">Confirm Frames <span id="cf-val" class="vbox">12</span></div>
            <input type="range" id="cf" min="1" max="30" step="1" value="12" oninput="onCfChange(this.value)">
            <div class="ctrl-hint">Consecutive frames before alert fires</div>
          </div>

          <div class="ctrl-section">
            <div class="section-label">IMAGE PROCESSING</div>
            <div class="ctrl-hint" style="margin-bottom:10px;color:var(--text2)">Applied to frames before YOLO inference</div>
            <div class="setting-label">Brightness <span id="bright-val" class="vbox">0</span></div>
            <input type="range" id="bright" min="-100" max="100" step="5" value="0"
              oninput="onImgChange('brightness',this.value,'bright-val',0)">
            <div class="setting-label" style="margin-top:8px">Contrast <span id="contrast-val" class="vbox">1.00</span></div>
            <input type="range" id="contrast" min="0.5" max="3.0" step="0.05" value="1.0"
              oninput="onImgChange('contrast',this.value,'contrast-val',2)">
            <div class="setting-label" style="margin-top:8px">Saturation <span id="sat-val" class="vbox">1.00</span></div>
            <input type="range" id="sat" min="0" max="3.0" step="0.05" value="1.0"
              oninput="onImgChange('saturation',this.value,'sat-val',2)">
            <div class="setting-label" style="margin-top:8px">Hue Shift <span id="hue-val" class="vbox">0</span></div>
            <input type="range" id="hue" min="-90" max="90" step="5" value="0"
              oninput="onImgChange('hue',this.value,'hue-val',0)">
            <div class="setting-label" style="margin-top:8px">Sharpness <span id="sharp-val" class="vbox">0.0</span></div>
            <input type="range" id="sharp" min="0" max="5" step="0.5" value="0"
              oninput="onImgChange('sharpness',this.value,'sharp-val',1)">
          </div>

          <div class="ctrl-section">
            <div class="section-label">ORIENTATION</div>
            <div class="btn-grid">
              <button class="btn-ctrl" id="btn-hflip" onclick="toggleFlip('h')">
                <span class="btn-ctrl-icon">&#8596;</span>H-FLIP
              </button>
              <button class="btn-ctrl" id="btn-vflip" onclick="toggleFlip('v')">
                <span class="btn-ctrl-icon">&#8597;</span>V-FLIP
              </button>
              <button class="btn-ctrl" onclick="doRotate(-90)">
                <span class="btn-ctrl-icon">&#8634;</span>ROT -90&#176;
              </button>
              <button class="btn-ctrl" onclick="doRotate(90)">
                <span class="btn-ctrl-icon">&#8635;</span>ROT +90&#176;
              </button>
            </div>
            <div class="ctrl-hint">Rotation: <span id="rot-val" style="color:var(--accent)">0&#176;</span></div>
          </div>

          <div class="ctrl-section">
            <button class="btn-reset" onclick="resetImageSettings()">&#8634; RESET IMAGE SETTINGS</button>
          </div>

          <div class="ctrl-section">
            <div class="section-label">CAMERA</div>
            <button class="btn-ctrl" id="btn-next-cam" onclick="nextCamera()" style="width:100%;padding:10px 4px;">
              <span class="btn-ctrl-icon">&#127909;</span>CHANGE CAMERA
            </button>
            <div class="ctrl-hint" style="margin-top:6px">Active: <span id="cam-display" style="color:var(--accent)">CAM 1</span> &nbsp;|&nbsp; Auto-scans for working index</div>
          </div>

        </div>
      </div>

      <!-- LOG PANE -->
      <div id="side-log" class="side-pane">
        <div class="log-filter-area">
          <div class="class-btns">
            <button class="class-btn selected" data-class="ALL" onclick="toggleClassFilter(this)">ALL</button>
            <button class="class-btn selected" data-class="Gun" onclick="toggleClassFilter(this)">GUN</button>
            <button class="class-btn selected" data-class="Knife" onclick="toggleClassFilter(this)">KNIFE</button>
            <button class="class-btn selected" data-class="Pliers" onclick="toggleClassFilter(this)">PLIERS</button>
            <button class="class-btn selected" data-class="Scissors" onclick="toggleClassFilter(this)">SCISSORS</button>
            <button class="class-btn selected" data-class="Wrench" onclick="toggleClassFilter(this)">WRENCH</button>
          </div>
          <div class="log-search-row">
            <input id="log-filter" class="log-search" placeholder="Search class / time...">
            <div class="dl-wrap">
              <button class="btn-icon" onclick="toggleDlMenu(event)">&#8595; EXPORT</button>
              <div class="dl-menu" id="dl-menu">
                <div class="dl-item" onclick="exportCSV()">&#128196; CSV</div>
                <div class="dl-item" onclick="exportHTML()">&#128444; HTML Report</div>
              </div>
            </div>
            <button class="btn-icon" onclick="clearAllData()" style="color:var(--danger);border-color:rgba(255,23,68,.4);" title="Delete all detections">&#128465; CLEAR</button>
          </div>
        </div>
        <div class="det-list" id="det-list">
          <div style="color:var(--text3);font-size:11px;padding:16px">Loading...</div>
        </div>
        <div class="log-pagination">
          <button class="btn-icon" id="prev-btn" onclick="changePage(-1)" style="display:none">&#9664;</button>
          <span id="page-info"></span>
          <button class="btn-icon" id="next-btn" onclick="changePage(1)" style="display:none">&#9654;</button>
        </div>
      </div>

      <!-- STATUS PANE -->
      <div id="side-status" class="side-pane">
        <div class="side-scroll">
          <div class="stat-chips">
            <div class="stat-chip"><div class="stat-chip-num" id="s-total">0</div><div class="stat-chip-lbl">TOTAL</div></div>
            <div class="stat-chip"><div class="stat-chip-num" id="s-today">0</div><div class="stat-chip-lbl">TODAY</div></div>
          </div>
          <div class="section-label">SYSTEM STATUS</div>
          <div class="status-grid2">
            <div class="status-item"><div class="status-item-label">MAIN STATUS</div><div class="status-item-val" id="si-main">--</div></div>
            <div class="status-item"><div class="status-item-label">GATE</div><div class="status-item-val" id="si-gate">--</div></div>
            <div class="status-item"><div class="status-item-label">BELT</div><div class="status-item-val" id="si-belt">--</div></div>
            <div class="status-item"><div class="status-item-label">BUZZER</div><div class="status-item-val" id="si-buzzer">--</div></div>
          </div>
          <div class="section-label">RECENT DETECTIONS</div>
          <div id="mini-det-list">
            <div style="color:var(--text3);font-size:11px;text-align:center;padding:20px 0">No detections yet</div>
          </div>
        </div>
      </div>

    </div>
  </div>
</div>

<div id="modal" onclick="closeModal()">
  <span class="modal-close" onclick="closeModal()">&#10005;</span>
  <img id="modal-img" src="" onclick="event.stopPropagation()">
  <div class="modal-info" id="modal-info"></div>
</div>
<div id="toast"></div>

<script>
let allDetections = [];
let logPage = 1;
let selectedClasses = new Set(["Gun","Knife","Pliers","Scissors","Wrench"]);
const NUM_CAMERAS = 4;
let activeCamIdx = 0;
let imgSettings = {brightness:0,contrast:1.0,saturation:1.0,hue:0,sharpness:0,hflip:false,vflip:false,rotation:0};

// ── PERF FIX: debounce status polls — don't pile up overlapping fetches ────
let _pollPending = false;
let _detPending  = false;

function switchSide(name, el){
  document.querySelectorAll(".side-pane").forEach(p=>p.classList.remove("active"));
  document.querySelectorAll(".side-tab").forEach(t=>t.classList.remove("active"));
  document.getElementById("side-"+name).classList.add("active");
  el.classList.add("active");
  if(name==="log")    renderLog();
}

function openModal(src,info){
  document.getElementById("modal-img").src=src;
  document.getElementById("modal-info").innerText=info||"";
  document.getElementById("modal").style.display="flex";
}
function closeModal(){document.getElementById("modal").style.display="none";}
document.addEventListener("keydown",e=>{if(e.key==="Escape")closeModal();});

function showToast(msg,type){
  const t=document.getElementById("toast");t.innerText=msg;
  t.className=type==="info"?"info":"";
  t.style.display="block";clearTimeout(t._t);
  t._t=setTimeout(()=>t.style.display="none",4000);
}
function updateClock(){document.getElementById("hdr-time").innerText=new Date().toLocaleTimeString();}

// ── FPS counter ───────────────────────────────────────────────────────────
let _fpsCnt=0,_fpsLast=Date.now();
const liveVideo=document.getElementById("live-video");
liveVideo.addEventListener("load",()=>{
  _fpsCnt++;
  const now=Date.now();
  if(now-_fpsLast>=1000){
    const fps=(_fpsCnt*1000/(now-_fpsLast)).toFixed(1)+" FPS";
    document.getElementById("feed-fps-tag").innerText=fps;
    document.getElementById("hdr-fps").innerText=fps;
    _fpsCnt=0;_fpsLast=now;
  }
});
// BUG FIX: reload feed if it goes dead (browser may stall MJPEG on tab switch)
liveVideo.addEventListener("error",()=>{
  setTimeout(()=>{
    liveVideo.src="/video_feed?_t="+Date.now();
  },2000);
});

// ── POLL STATUS ────────────────────────────────────────────────────────────
// PERF FIX: skip fetch if previous one hasn't finished
async function pollStatus(){
  if(_pollPending)return;
  _pollPending=true;
  try{
    const d=await fetch("/status").then(r=>r.json());
    const st=d.status;
    [["badge-main","STATUS",st.main,"si-main"],
     ["badge-gate","GATE",st.gate,"si-gate"],
     ["badge-belt","BELT",st.belt,"si-belt"],
     ["badge-buzzer","BUZZER",st.buzzer,"si-buzzer"]].forEach(([id,lbl,val,sid])=>{
      const el=document.getElementById(id);const sel=document.getElementById(sid);
      if(!el||!val)return;
      const on=["OPEN","RUNNING","ON","SAFE"].includes(val);
      el.className="s-badge "+(on?"on":"off");
      el.innerHTML=`<span class="s-badge-label">${lbl}</span>${val}`;
      if(sel){sel.className="status-item-val "+(on?"on":"off");sel.innerText=val;}
    });
    document.getElementById("feed-overlay").style.display=st.main==="DANGER"?"flex":"none";
  }catch(e){}
  finally{_pollPending=false;}
}

// ── SETTINGS ──────────────────────────────────────────────────────────────
let _settingTimer=null;
// BUG FIX: syncSettings was overwriting user slider values every 3 s mid-drag.
// Only sync when user is NOT actively editing.
let _userEditing=false;
async function syncSettings(){
  if(_userEditing)return;
  try{
    const d=await fetch("/current_settings").then(r=>r.json());
    document.getElementById("th").value=d.confidence_threshold;
    document.getElementById("th-val").innerText=parseFloat(d.confidence_threshold).toFixed(2);
    document.getElementById("cf").value=d.confirm_frames;
    document.getElementById("cf-val").innerText=d.confirm_frames;
    activeCamIdx=d.camera_index||0;
    document.getElementById("feed-cam-tag").innerText="CAM "+(activeCamIdx+1);
    const cd=document.getElementById("cam-display");if(cd)cd.innerText="CAM "+(activeCamIdx+1);
    if(d.image_settings) applyImgSettingsToUI(d.image_settings);
  }catch(e){}
}
function applyImgSettingsToUI(s){
  imgSettings=Object.assign(imgSettings,s);
  const setSlider=(id,valId,val,dec)=>{
    const el=document.getElementById(id);if(el)el.value=val;
    const vEl=document.getElementById(valId);if(vEl)vEl.innerText=dec!=null?parseFloat(val).toFixed(dec):val;
  };
  setSlider("bright","bright-val",s.brightness,0);
  setSlider("contrast","contrast-val",s.contrast,2);
  setSlider("sat","sat-val",s.saturation,2);
  setSlider("hue","hue-val",s.hue,0);
  setSlider("sharp","sharp-val",s.sharpness,1);
  document.getElementById("btn-hflip").classList.toggle("active",!!s.hflip);
  document.getElementById("btn-vflip").classList.toggle("active",!!s.vflip);
  document.getElementById("rot-val").innerText=(s.rotation||0)+"°";
}
function _markEditing(){_userEditing=true;clearTimeout(_editTimer);_editTimer=setTimeout(()=>_userEditing=false,2000);}
let _editTimer=null;
function onThChange(v){_markEditing();document.getElementById("th-val").innerText=parseFloat(v).toFixed(2);clearTimeout(_settingTimer);_settingTimer=setTimeout(saveDetSettings,400);}
function onCfChange(v){_markEditing();document.getElementById("cf-val").innerText=v;clearTimeout(_settingTimer);_settingTimer=setTimeout(saveDetSettings,400);}
function saveDetSettings(){
  fetch("/set_threshold/"+document.getElementById("th").value,{method:"POST"});
  fetch("/set_confirm/"+document.getElementById("cf").value,{method:"POST"});
}
let _imgTimer=null;
function onImgChange(key,val,displayId,decimals){
  _markEditing();
  const v=decimals===0?parseInt(val):parseFloat(val);
  document.getElementById(displayId).innerText=decimals!=null?(v.toFixed?v.toFixed(decimals):v):v;
  imgSettings[key]=v;
  clearTimeout(_imgTimer);_imgTimer=setTimeout(saveImgSettings,350);
}
function saveImgSettings(){
  fetch("/set_image_settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(imgSettings)});
}
function toggleFlip(axis){
  if(axis==="h"){imgSettings.hflip=!imgSettings.hflip;document.getElementById("btn-hflip").classList.toggle("active",imgSettings.hflip);}
  else{imgSettings.vflip=!imgSettings.vflip;document.getElementById("btn-vflip").classList.toggle("active",imgSettings.vflip);}
  saveImgSettings();
}
function doRotate(deg){
  imgSettings.rotation=(imgSettings.rotation+deg+360)%360;
  document.getElementById("rot-val").innerText=imgSettings.rotation+"°";
  saveImgSettings();
}
function resetImageSettings(){
  imgSettings={brightness:0,contrast:1.0,saturation:1.0,hue:0,sharpness:0,hflip:false,vflip:false,rotation:0};
  applyImgSettingsToUI(imgSettings);
  saveImgSettings();
  showToast("Image settings reset","info");
}

// ── CAMERA — cycle with auto-scan for working index ───────────────────────
let _camSwitching = false;
async function nextCamera(){
  if(_camSwitching)return;
  _camSwitching=true;
  const btn=document.getElementById("btn-next-cam");
  if(btn)btn.disabled=true;

  const start=activeCamIdx;
  let tried=0, found=false;

  while(tried<NUM_CAMERAS){
    const next=(activeCamIdx+1+tried)%NUM_CAMERAS;
    const ok=await checkCamIndex(next);
    if(ok){activeCamIdx=next;found=true;break;}
    tried++;
  }
  if(!found) activeCamIdx=(start+1)%NUM_CAMERAS;

  document.getElementById("feed-cam-tag").innerText="CAM "+(activeCamIdx+1);
  const cd=document.getElementById("cam-display");if(cd)cd.innerText="CAM "+(activeCamIdx+1);

  fetch("/set_camera/"+activeCamIdx,{method:"POST"});
  showToast("Switched to CAM "+(activeCamIdx+1),"info");

  _camSwitching=false;
  if(btn)btn.disabled=false;
}

function checkCamIndex(idx){
  return new Promise(resolve=>{
    const xhr=new XMLHttpRequest();
    xhr.open("GET","/snapshot?cam="+idx+"&t="+Date.now(),true);
    xhr.responseType="blob";
    xhr.timeout=2000;
    xhr.onload=()=>resolve(xhr.status===200&&xhr.response&&xhr.response.size>0);
    xhr.onerror=()=>resolve(false);
    xhr.ontimeout=()=>resolve(false);
    xhr.send();
  });
}

// ── CLEAR ALL DATA ────────────────────────────────────────────────────────
async function clearAllData(){
  if(!confirm("DELETE ALL detections from the database?\\n\\nThis will permanently erase all logs, images, and reset the counter to 0. This cannot be undone."))return;
  try{
    const r=await fetch("/clear_log",{method:"POST"});
    if(r.ok){
      allDetections=[];logPage=1;
      updateCounts();renderLog();
      document.getElementById("mini-det-list").innerHTML=`<div style="color:var(--text3);font-size:11px;text-align:center;padding:20px 0">No detections yet</div>`;
      showToast("All detections cleared","info");
    }else{showToast("Clear failed: "+r.status);}
  }catch(e){showToast("Clear failed");}
}



// ── CLASS FILTER ──────────────────────────────────────────────────────────
function toggleClassFilter(btn){
  const cls=btn.dataset.class;
  if(cls==="ALL"){
    selectedClasses=new Set(["Gun","Knife","Pliers","Scissors","Wrench"]);
    document.querySelectorAll(".class-btn").forEach(b=>b.classList.add("selected"));
  }else{
    btn.classList.toggle("selected");
    if(btn.classList.contains("selected")) selectedClasses.add(cls);
    else selectedClasses.delete(cls);
    const all=document.querySelector(".class-btn[data-class='ALL']");
    if(selectedClasses.size===5) all.classList.add("selected");else all.classList.remove("selected");
  }
  logPage=1;renderLog();
}

// ── DOWNLOAD ──────────────────────────────────────────────────────────────
function toggleDlMenu(e){e.stopPropagation();document.getElementById("dl-menu").classList.toggle("open");}
document.addEventListener("click",()=>document.getElementById("dl-menu").classList.remove("open"));
function exportCSV(){
  document.getElementById("dl-menu").classList.remove("open");
  window.open("/download_log","_blank");
}
function exportHTML(){
  document.getElementById("dl-menu").classList.remove("open");
  const filtered=getFilteredDets();
  const rows=filtered.map(d=>{
    const conf=d.conf?parseFloat(d.conf).toFixed(2):"—";
    const imgSrc=`/static/detections/${d.thumb}`;
    return `<tr><td>${d.id}</td><td><span class="cls-${d.class}">${d.class}</span></td>
      <td>${d.date_dmy}</td><td>${d.time_hms}</td><td>${conf}</td>
      <td>${imgSrc?`<img src="${imgSrc}" width="80" height="60" style="border-radius:4px;object-fit:cover">`:""}</td>
    </tr>`;
  }).join("");
  const html=`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Detection Report</title>
<style>body{font-family:monospace;background:#070b12;color:#c8d8f0;padding:24px;}h1{color:#00d4ff;}
table{border-collapse:collapse;width:100%;font-size:12px;margin-top:16px;}
th{background:#0f1623;color:#00d4ff;padding:8px 10px;border:1px solid #1e2d42;text-align:left;}
td{padding:7px 10px;border:1px solid #1e2d42;vertical-align:middle;}tr:nth-child(even){background:#0c1520;}
.Gun{color:#ff6b6b;font-weight:700;}.Knife{color:#ffab00;font-weight:700;}
.Pliers{color:#4eff8a;font-weight:700;}.Scissors{color:#c084fc;font-weight:700;}
.Wrench{color:#00d4ff;font-weight:700;}
</style></head><body><h1>X-Ray Detection Report</h1>
<p style="color:#6a8aaa">Generated: ${new Date().toLocaleString()} · Records: ${filtered.length}</p>
<table><thead><tr><th>#</th><th>Class</th><th>Date</th><th>Time</th><th>Conf</th><th>Image</th></tr></thead>
<tbody>${rows}</tbody></table></body></html>`;
  const blob=new Blob([html],{type:"text/html"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");a.href=url;a.download="detection_report.html";a.click();
  URL.revokeObjectURL(url);
}

// ── LOG ───────────────────────────────────────────────────────────────────
function getFilteredDets(){
  const tf=(document.getElementById("log-filter").value||"").toLowerCase();
  return allDetections.filter(d=>{
    const cMatch=selectedClasses.size===0||selectedClasses.has(d.class);
    const tMatch=!tf||d.class.toLowerCase().includes(tf)||(d.time_hms&&d.time_hms.includes(tf))||(d.date_dmy&&d.date_dmy.includes(tf));
    return cMatch&&tMatch;
  });
}
function renderLog(){
  const filtered=getFilteredDets();
  const perPage=30;const start=(logPage-1)*perPage;
  const page=filtered.slice(start,start+perPage);
  const list=document.getElementById("det-list");
  if(!page.length){
    list.innerHTML=`<div style="color:var(--text3);font-size:11px;padding:16px;text-align:center">No detections found</div>`;
    document.getElementById("prev-btn").style.display="none";
    document.getElementById("next-btn").style.display="none";
    document.getElementById("page-info").innerText="";return;
  }
  list.innerHTML=page.map(d=>{
    const img=`/static/detections/${d.thumb}`;
    const conf=d.conf?parseFloat(d.conf).toFixed(2):null;
    const info=`${d.class} | ${d.time_hms} ${d.date_dmy}${conf?" | conf "+conf:""}`;
    return `<div class="det-item" onclick="openModal('${img}','${info}')">
      <img src="${img}" loading="lazy" onerror="this.style.opacity='.15'">
      <div class="det-item-info">
        <div class="det-item-class">${d.class}</div>
        <div class="det-item-meta">${d.time_hms} · ${d.date_dmy}</div>
        ${conf?`<span class="det-item-conf">${conf}</span>`:""}
      </div></div>`;
  }).join("");
  document.getElementById("prev-btn").style.display=logPage>1?"flex":"none";
  document.getElementById("next-btn").style.display=page.length===perPage?"flex":"none";
  document.getElementById("page-info").innerText=`PG ${logPage} / ${Math.ceil(filtered.length/perPage)||1}`;
}
function changePage(dir){logPage=Math.max(1,logPage+dir);renderLog();}
document.addEventListener("DOMContentLoaded",()=>{
  const f=document.getElementById("log-filter");if(f)f.oninput=()=>{logPage=1;renderLog();};
});

// ── MINI DETS ─────────────────────────────────────────────────────────────
function updateCounts(){
  const today=new Date().toLocaleDateString("en-GB");
  document.getElementById("s-total").innerText=allDetections.length;
  document.getElementById("s-today").innerText=allDetections.filter(d=>d.date_dmy===today).length;
}
let lastDetLen=-1;
async function updateMiniDets(){
  if(_detPending)return;
  _detPending=true;
  try{
    const data=await fetch("/timeline_full").then(r=>r.json());
    allDetections=data;updateCounts();
    if(data.length===lastDetLen){_detPending=false;return;}
    lastDetLen=data.length;
    const list=document.getElementById("mini-det-list");
    const recent=data.slice(0,10);
    if(!recent.length){list.innerHTML=`<div style="color:var(--text3);font-size:11px;text-align:center;padding:20px 0">No detections yet</div>`;_detPending=false;return;}
    list.innerHTML=recent.map(d=>{
      const img=`/static/detections/${d.thumb}`;
      const info=`${d.class} | ${d.time_hms} ${d.date_dmy}`;
      return `<div class="det-item" onclick="openModal('${img}','${info}')">
        <img src="${img}" loading="lazy" onerror="this.style.opacity='.15'">
        <div class="det-item-info"><div class="det-item-class">${d.class}</div>
        <div class="det-item-meta">${d.time_hms} · ${d.date_dmy}</div></div>
      </div>`;
    }).join("");
  }catch(e){}
  finally{_detPending=false;}
}

fetch("/info").then(r=>r.json()).then(d=>{
  document.getElementById("hdr-phone").innerText="📱 "+d.local_ip+":"+d.port;
}).catch(()=>{});

// PERF FIX: spread out timers so they don't all fire simultaneously
setInterval(pollStatus,   1000);   // was 800ms — 1 s is plenty
setInterval(syncSettings, 5000);   // was 3 s — 5 s prevents slider jitter
setInterval(updateMiniDets,3000);  // was 1.5 s — 3 s enough for a log panel
setInterval(updateClock,  1000);

pollStatus();syncSettings();updateMiniDets();updateClock();
</script>
</body>
</html>
"""


# ================= MAIN =================
if __name__ == "__main__":
    local_ip = get_local_ip()
    print("\n" + "="*50)
    print(f"  Desktop UI  : http://127.0.0.1:5000")
    print(f"  Phone/Tablet: http://{local_ip}:5000")
    print("="*50 + "\n")

    threading.Thread(target=capture_frames,  daemon=True).start()
    threading.Thread(target=inference_thread, daemon=True).start()

    if CLOUD_ENABLED:
        if cloud_login():
            threading.Thread(target=cloud_stream_thread,      daemon=True).start()
            threading.Thread(target=cloud_status_push_thread, daemon=True).start()
        else:
            print("[CLOUD] Login failed — running local only")

    def start_flask():
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)

    threading.Thread(target=start_flask, daemon=True).start()

    webview.create_window(
        "X-Ray Scanner",
        "http://127.0.0.1:5000",
        width=1280,
        height=820
    )
    webview.start()