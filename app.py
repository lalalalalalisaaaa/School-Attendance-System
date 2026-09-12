import os, cv2, sqlite3, requests, webbrowser, threading, csv, io
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, jsonify, send_file, session, make_response
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "attendance-system-secure-key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "attendance.db")
FACES_DIR = os.path.join(BASE_DIR, "student_faces")
os.makedirs(FACES_DIR, exist_ok=True)

TEXTBEE_API_KEY = "txb_t18Sw5sCFGC6J8XkiNmpJUwIIgNflo2t"
TEXTBEE_DEVICE_ID = "6a9c04daccb6c72709bab159"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS students (student_id TEXT PRIMARY KEY, name TEXT, grade TEXT, section TEXT, parent TEXT, phone TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY, student_id TEXT, name TEXT, grade TEXT, section TEXT, kind TEXT, timestamp TEXT)')
    conn.commit()
    conn.close()

init_db()

def get_greeting():
    h = datetime.now(ZoneInfo("Asia/Manila")).hour
    return "Good morning" if h < 12 else ("Good afternoon" if h < 18 else "Good evening")

def send_sms(phone, parent, name, grade, section, kind, ts):
    if not phone: return False
    phone = phone.strip().replace("-", "").replace(" ", "")
    if phone.startswith("0"): phone = "+63" + phone[1:]
    
    greeting = get_greeting()
    parent_part = f" {parent}" if parent else ""
    msg = f"{greeting}{parent_part}, your child {name} ({grade} - {section}) has recorded {kind} at Payatas B. Elementary School on {ts}."
    
    try:
        r = requests.post(f"https://api.textbee.dev/api/v1/gateway/devices/{TEXTBEE_DEVICE_ID}/send-sms",
                          json={"recipients": [phone], "message": msg}, headers={"x-api-key": TEXTBEE_API_KEY}, timeout=10)
        return r.status_code in [200, 201]
    except: return False

@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/auth_status')
def auth_status(): return jsonify({'logged_in': 'user' in session})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json or {}
    conn = sqlite3.connect(DB_PATH)
    row = conn.cursor().execute("SELECT password FROM users WHERE username=?", (d.get('username'),)).fetchone()
    conn.close()
    if row and check_password_hash(row[0], d.get('password')):
        session['user'] = d.get('username')
        return jsonify({'ok': True, 'message': 'Success'})
    return jsonify({'ok': False, 'message': 'Invalid credentials.'})

@app.route('/api/logout')
def logout():
    session.pop('user', None)
    return jsonify({'ok': True})

@app.route('/api/register_user', methods=['POST'])
def register_user():
    d = request.json or {}
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.cursor().execute("INSERT INTO users (username, password) VALUES (?, ?)", 
                              (d.get('username'), generate_password_hash(d.get('password'))))
        conn.commit()
        return jsonify({'ok': True, 'message': 'Account created. Please login.'})
    except: return jsonify({'ok': False, 'message': 'Username already exists.'})
    finally: conn.close()

@app.route('/api/register', methods=['POST'])
def register_student():
    f = request.form
    if not all([f.get('student_id'), f.get('name'), f.get('grade'), f.get('section'), f.get('parent'), f.get('phone'), request.files.get('face_image')]):
        return jsonify({'ok': False, 'message': 'All fields are required.'})
    
    sid = f.get('student_id')
    img_file = request.files.get('face_image')
    temp_reg_path = os.path.join(BASE_DIR, f"temp_reg_{sid}.jpg")
    img_file.save(temp_reg_path)
    
    k_img = cv2.imread(temp_reg_path, cv2.IMREAD_GRAYSCALE)
    if os.path.exists(temp_reg_path): os.remove(temp_reg_path)
    if k_img is None: return jsonify({'ok': False, 'message': 'Invalid image file.'})
    
    face_cas = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    kf = face_cas.detectMultiScale(k_img, 1.1, 4)
    
    if len(kf) > 0:
        x, y, w, h = kf[0]
        face_roi = k_img[y:y+h, x:x+w]
    else:
        face_roi = k_img
        
    face_roi = cv2.resize(face_roi, (200, 200))
    face_roi = cv2.equalizeHist(face_roi)
    cv2.imwrite(os.path.join(FACES_DIR, f"{sid}.jpg"), face_roi)
    
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute("REPLACE INTO students VALUES (?, ?, ?, ?, ?, ?)", (sid, f.get('name'), f.get('grade'), f.get('section'), f.get('parent'), f.get('phone')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'message': f"Student {f.get('name')} registered!"})

def process_face(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = face_cascade.detectMultiScale(gray, 1.1, 4)
    if len(faces) == 0:
        return None
    (x, y, w, h) = max(faces, key=lambda f: f[2] * f[3])
    face_roi = gray[y:y+h, x:x+w]
    face_resized = cv2.resize(face_roi, (200, 200))
    return cv2.equalizeHist(face_resized)

@app.route('/api/verify_face', methods=['POST'])
def verify_face():
    img = request.files.get('face_scan')
    if not img: return jsonify({'ok': False, 'message': 'No image.'})
    temp_path = os.path.join(BASE_DIR, "temp_scan.jpg")
    img.save(temp_path)
    target_img = cv2.imread(temp_path)
    if os.path.exists(temp_path): os.remove(temp_path)
    if target_img is None: return jsonify({'ok': False, 'message': 'Image error.'})
    
    processed_incoming = process_face(target_img)
    if processed_incoming is None:
        return jsonify({'ok': False, 'message': 'No face detected.'})
        
    best_sid, highest_score = None, -1.0
    hist_incoming = cv2.calcHist([processed_incoming], [0], None, [256], [0, 256])
    cv2.normalize(hist_incoming, hist_incoming, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
    
    if not os.path.exists(FACES_DIR):
        return jsonify({'ok': False, 'message': 'Face not recognized.'})
        
    for filename in os.listdir(FACES_DIR):
        if not filename.endswith(".jpg"): continue
        student_id = filename.split(".")[0]
        reg_img = cv2.imread(os.path.join(FACES_DIR, filename), cv2.IMREAD_GRAYSCALE)
        if reg_img is None: continue
        
        reg_resized = cv2.resize(reg_img, (200, 200))
        hist_reg = cv2.calcHist([reg_resized], [0], None, [256], [0, 256])
        cv2.normalize(hist_reg, hist_reg, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        
        similarity = cv2.compareHist(hist_incoming, hist_reg, cv2.HISTCMP_CORREL)
        if similarity > highest_score and similarity > 0.60:
            highest_score = similarity
            best_sid = student_id
            
    if highest_score < 0.60 or not best_sid:
        return jsonify({'ok': False, 'message': 'Face not recognized.'})
        
    conn = sqlite3.connect(DB_PATH)
    row = conn.cursor().execute("SELECT student_id, name, grade, section FROM students WHERE student_id=?", (best_sid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'ok': False, 'message': 'Face not recognized.'})
    return jsonify({'ok': True, 'message': f'Verified: {row[1]}', 'student_id': row[0], 'name': row[1], 'grade': row[2], 'section': row[3]})

@app.route('/api/qr/<sid>')
def get_qr(sid):
    import qrcode
    buf = io.BytesIO()
    qrcode.make(sid).save(buf, 'PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/api/verify', methods=['POST'])
def verify():
    sid, kind = request.form.get('student_id'), request.form.get('kind', 'Time In')
    conn = sqlite3.connect(DB_PATH)
    student = conn.cursor().execute("SELECT name, grade, section, parent, phone FROM students WHERE student_id=?", (sid,)).fetchone()
    if not student:
        conn.close()
        return jsonify({'ok': False, 'message': 'Student not found.'})
    
    name, grade, section, parent, phone = student
    ts = datetime.now(ZoneInfo("Asia/Manila")).strftime("%Y-%m-%d %I:%M %p")
    
    conn.cursor().execute("INSERT INTO attendance (student_id, name, grade, section, kind, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (sid, name, grade, section, kind, ts))
    conn.commit()
    conn.close()
    
    sms = send_sms(phone, parent, name, grade, section, kind, ts)
    return jsonify({'ok': True, 'message': f'{kind} Success: {name}', 'student': f'{name} ({sid})', 'grade': grade, 'section': section, 'timestamp': ts, 'sms_status': 'SMS Sent' if sms else 'SMS Failed'})

@app.route('/api/attendance')
def get_attendance():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.cursor().execute("SELECT student_id, name, grade, section, kind, timestamp FROM attendance ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([{'student_id': r[0], 'name': r[1], 'grade': r[2], 'section': r[3], 'kind': r[4], 'timestamp': r[5]} for r in rows])

@app.route('/api/clear_attendance', methods=['POST'])
def clear_attendance():
    conn = sqlite3.connect(DB_PATH)
    conn.cursor().execute("DELETE FROM attendance")
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/export_attendance')
def export_attendance():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.cursor().execute("SELECT student_id, name, grade, section, kind, timestamp FROM attendance ORDER BY id DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Student ID', 'Name', 'Grade', 'Section', 'Type', 'Timestamp'])
    writer.writerows(rows)
    res = make_response(output.getvalue())
    res.headers["Content-Disposition"] = "attachment; filename=attendance_logs.csv"
    res.headers["Content-type"] = "text/csv"
    return res

if __name__ == '__main__':
    threading.Timer(1.2, lambda: webbrowser.open_new("http://127.0.0.1:5000")).start()
    app.run(debug=False, port=5000)
