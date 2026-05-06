import os
import sqlite3
import io
import base64
import mimetypes
from datetime import datetime, date
from flask import Flask, request, jsonify, session, send_file, redirect, url_for, render_template, send_from_directory
from werkzeug.utils import secure_filename
import openpyxl
import requests as _requests
import json as _json
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Telegram config ──────────────────────────────────────────────────────────
TG_BOT_TOKEN     = os.environ.get('TG_BOT_TOKEN')
TG_CHAT_ID       = os.environ.get('TG_CHAT_ID')
TG_THREAD_ORDERS = 4   # топик "Заказы"
TG_THREAD_STATS  = 8   # топик "Статистика"

# ── Gemini config ─────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GEMINI_MODEL   = 'gemini-3-flash-preview'

def _tg_api(method, **kwargs):
    try:
        _requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/{method}',
            timeout=15, **kwargs
        )
    except Exception as e:
        print(f'TG {method} error:', e)

def tg_send_message(text, thread_id):
    _tg_api('sendMessage', json={
        'chat_id': TG_CHAT_ID,
        'message_thread_id': thread_id,
        'text': text
    })

def tg_send_order_photos(photo_filenames, caption):
    """Отправляет чеки в топик Заказы. Без фото — просто текст."""
    if not photo_filenames:
        tg_send_message(caption, TG_THREAD_ORDERS)
        return
    try:
        if len(photo_filenames) == 1:
            with open(os.path.join(UPLOAD_FOLDER, photo_filenames[0]), 'rb') as f:
                _tg_api('sendPhoto',
                    data={'chat_id': TG_CHAT_ID,
                          'message_thread_id': TG_THREAD_ORDERS,
                          'caption': caption},
                    files={'photo': f}
                )
        else:
            media = []
            files = {}
            for i, fn in enumerate(photo_filenames):
                key = f'photo{i}'
                item = {'type': 'photo', 'media': f'attach://{key}'}
                if i == 0:
                    item['caption'] = caption
                media.append(item)
                files[key] = open(os.path.join(UPLOAD_FOLDER, fn), 'rb')
            try:
                _tg_api('sendMediaGroup',
                    data={'chat_id': TG_CHAT_ID,
                          'message_thread_id': TG_THREAD_ORDERS,
                          'media': _json.dumps(media)},
                    files=files
                )
            finally:
                for f in files.values():
                    f.close()
    except Exception as e:
        print('TG send_order_photos error:', e)

app = Flask(__name__,
            template_folder=os.path.join(BASE_DIR, 'templates'),
            static_folder=os.path.join(BASE_DIR, 'static'))
app.secret_key = os.environ.get('SECRET_KEY')

# ── Config ──────────────────────────────────────────────────────────────────
PASSWORD = os.environ.get('SPOTBOT_PASSWORD')
DB_PATH = os.path.join(BASE_DIR, 'spotbot.db')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Database ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                cost INTEGER NOT NULL,
                order_time TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
            );
        ''')

init_db()

# ── Auth ─────────────────────────────────────────────────────────────────────
def is_authed():
    return session.get('authed') is True

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    if data and data.get('password') == PASSWORD:
        session.permanent = True
        session['authed'] = True
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Неверный пароль'}), 401

@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/auth/check')
def auth_check():
    return jsonify({'authed': is_authed()})

# ── Orders ───────────────────────────────────────────────────────────────────
@app.route('/api/orders')
def get_orders():
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    day = request.args.get('date', date.today().isoformat())  # YYYY-MM-DD
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM orders WHERE date(order_time)=? ORDER BY order_time DESC",
            (day,)
        ).fetchall()
        result = []
        for r in rows:
            photos = db.execute("SELECT id, filename FROM photos WHERE order_id=?", (r['id'],)).fetchall()
            result.append({
                'id': r['id'],
                'address': r['address'],
                'cost': r['cost'],
                'order_time': r['order_time'],
                'photos': [{'id': p['id'], 'filename': p['filename']} for p in photos]
            })
        total = db.execute("SELECT COALESCE(SUM(cost),0) FROM orders WHERE date(order_time)=?", (day,)).fetchone()[0]
    return jsonify({'orders': result, 'total': total, 'date': day})

@app.route('/api/orders', methods=['POST'])
def create_order():
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO orders (address, cost, order_time) VALUES (?,?,?)",
            (data['address'], int(data['cost']), data['order_time'])
        )
        order_id = cur.lastrowid
        # attach pending photos
        for filename in data.get('photos', []):
            db.execute("INSERT INTO photos (order_id, filename) VALUES (?,?)", (order_id, filename))
        db.commit()

    # ── Telegram: отправить фото чека в топик Заказы ─────────────────────────
    caption = f"{data['address']}\n{int(data['cost'])}₽"
    tg_send_order_photos(data.get('photos', []), caption)

    return jsonify({'ok': True, 'id': order_id})

@app.route('/api/orders/<int:order_id>', methods=['GET'])
def get_order(order_id):
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as db:
        r = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not r: return jsonify({'error': 'Not found'}), 404
        photos = db.execute("SELECT id, filename FROM photos WHERE order_id=?", (order_id,)).fetchall()
        return jsonify({
            'id': r['id'], 'address': r['address'], 'cost': r['cost'],
            'order_time': r['order_time'],
            'photos': [{'id': p['id'], 'filename': p['filename']} for p in photos]
        })

@app.route('/api/orders/<int:order_id>', methods=['PUT'])
def update_order(order_id):
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    with get_db() as db:
        db.execute(
            "UPDATE orders SET address=?, cost=?, order_time=? WHERE id=?",
            (data['address'], int(data['cost']), data['order_time'], order_id)
        )
        # add new photos if any
        for filename in data.get('new_photos', []):
            db.execute("INSERT INTO photos (order_id, filename) VALUES (?,?)", (order_id, filename))
        # remove deleted photos
        for photo_id in data.get('deleted_photos', []):
            row = db.execute("SELECT filename FROM photos WHERE id=?", (photo_id,)).fetchone()
            if row:
                try: os.remove(os.path.join(UPLOAD_FOLDER, row['filename']))
                except: pass
                db.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        db.commit()
    return jsonify({'ok': True})

@app.route('/api/orders/<int:order_id>', methods=['DELETE'])
def delete_order(order_id):
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as db:
        photos = db.execute("SELECT filename FROM photos WHERE order_id=?", (order_id,)).fetchall()
        for p in photos:
            try: os.remove(os.path.join(UPLOAD_FOLDER, p['filename']))
            except: pass
        db.execute("DELETE FROM photos WHERE order_id=?", (order_id,))
        db.execute("DELETE FROM orders WHERE id=?", (order_id,))
        db.commit()
    return jsonify({'ok': True})

# ── Address suggestions ───────────────────────────────────────────────────────
@app.route('/api/addresses')
def get_addresses():
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    q = request.args.get('q', '').lower()
    with get_db() as db:
        rows = db.execute(
            "SELECT address, COUNT(*) as cnt FROM orders GROUP BY address ORDER BY cnt DESC"
        ).fetchall()
    # Python-side filtering with proper Unicode lower()
    results = [r['address'] for r in rows if q in r['address'].lower()][:10]
    return jsonify({'addresses': results})

# ── Photos ────────────────────────────────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def upload_photo():
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
    if ext not in ALLOWED_EXTENSIONS:
        ext = 'jpg'
    ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    filename = f'{ts}.{ext}'
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    return jsonify({'ok': True, 'filename': filename})

@app.route('/uploads/<filename>')
def serve_upload(filename):
    if not is_authed(): return redirect('/')
    return send_from_directory(UPLOAD_FOLDER, filename)

# ── Gemini proxy ──────────────────────────────────────────────────────────────
@app.route('/api/gemini/analyze', methods=['POST'])
def gemini_analyze():
    if not is_authed():
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    filename = data.get('filename', '').strip()
    prompt = data.get('prompt', 'Распознай чек. Верни ТОЛЬКО две строки: первая — сумма числом, вторая — время hh:mm.')

    if not filename:
        return jsonify({'error': 'filename required'}), 400

    filepath = os.path.join(UPLOAD_FOLDER, secure_filename(filename))
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404

    # Read file and encode to base64
    with open(filepath, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()

    mime = mimetypes.guess_type(filepath)[0] or 'image/jpeg'

    payload = {
        'contents': [{
            'parts': [
                {'inline_data': {'mime_type': mime, 'data': img_b64}},
                {'text': prompt}
            ]
        }]
    }

    try:
        resp = _requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}',
            json=payload,
            timeout=30
        )
        result = resp.json()

        if 'error' in result:
            return jsonify({'error': result['error'].get('message', 'Gemini error')}), 502

        text = result['candidates'][0]['content']['parts'][0]['text'].strip()
        return jsonify({'ok': True, 'text': text})

    except Exception as e:
        print('Gemini proxy error:', e)
        return jsonify({'error': str(e)}), 502

# ── Statistics ────────────────────────────────────────────────────────────────
@app.route('/api/stats')
def get_stats():
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    with get_db() as db:
        # Totals
        totals = db.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(cost),0) as total FROM orders").fetchone()

        # Top 10 addresses
        top = db.execute("""
            SELECT address, COUNT(*) as cnt, SUM(cost) as total
            FROM orders GROUP BY lower(address)
            ORDER BY total DESC LIMIT 10
        """).fetchall()

        # Hourly stats (12-21)
        # get all orders
        all_orders = db.execute("SELECT order_time, cost FROM orders").fetchall()

        # group by day and hour
        from collections import defaultdict
        hour_data = defaultdict(list)  # hour -> list of (day, cost)
        day_hours = defaultdict(set)   # day -> set of hours with orders

        for o in all_orders:
            try:
                dt = datetime.fromisoformat(o['order_time'])
                h = dt.hour
                if 12 <= h <= 21:
                    day_str = dt.date().isoformat()
                    hour_data[h].append({'day': day_str, 'cost': o['cost']})
                    day_hours[day_str].add(h)
            except:
                pass

        # total distinct days
        total_days = len(day_hours) if day_hours else 1

        hourly = []
        for h in range(12, 22):
            entries = hour_data.get(h, [])
            cnt = len(entries)
            avg_cost = round(sum(e['cost'] for e in entries) / cnt) if cnt else 0
            days_with_this_hour = len(set(e['day'] for e in entries))
            prob = round(days_with_this_hour / total_days * 100) if total_days else 0
            hourly.append({'hour': h, 'count': cnt, 'avg_cost': avg_cost, 'probability': prob})

    return jsonify({
        'totals': {'count': totals['cnt'], 'sum': totals['total']},
        'top_addresses': [{'address': r['address'], 'count': r['cnt'], 'total': r['total']} for r in top],
        'hourly': hourly
    })

# ── Telegram reports ─────────────────────────────────────────────────────────
@app.route('/api/tg/report/day', methods=['POST'])
def tg_report_day():
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    day = data.get('date', date.today().isoformat())  # YYYY-MM-DD
    with get_db() as db:
        rows = db.execute(
            "SELECT cost FROM orders WHERE date(order_time)=?", (day,)
        ).fetchall()
    if not rows:
        return jsonify({'ok': False, 'error': 'Нет заказов за этот день'}), 404
    total = sum(r['cost'] for r in rows)
    # DD.MM.YYYY
    d, m, y = day.split('-')[2], day.split('-')[1], day.split('-')[0]
    text = f"{d}.{m}.{y}\n{len(rows)} заказов\n{total}₽"
    tg_send_message(text, TG_THREAD_STATS)
    return jsonify({'ok': True})

@app.route('/api/tg/report/week', methods=['POST'])
def tg_report_week():
    if not is_authed(): return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json()
    date_from = data.get('date_from')
    date_to   = data.get('date_to')
    if not date_from or not date_to:
        return jsonify({'error': 'date_from and date_to required'}), 400
    with get_db() as db:
        rows = db.execute(
            "SELECT cost FROM orders WHERE date(order_time) BETWEEN ? AND ?",
            (date_from, date_to)
        ).fetchall()
    if not rows:
        return jsonify({'ok': False, 'error': 'Нет заказов за этот период'}), 404
    total = sum(r['cost'] for r in rows)
    def fmt(iso):
        y, m, d = iso.split('-')
        return f"{d}.{m}.{y}"
    text = f"💎{fmt(date_from)} - {fmt(date_to)}\n{len(rows)} заказов\n{total}₽"
    tg_send_message(text, TG_THREAD_STATS)
    return jsonify({'ok': True})

# ── Excel export ──────────────────────────────────────────────────────────────
@app.route('/api/export/excel')
def export_excel():
    if not is_authed(): return redirect('/')
    with get_db() as db:
        rows = db.execute("SELECT order_time, address, cost FROM orders ORDER BY order_time").fetchall()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Заказы'
    ws.append(['Дата', 'Адрес', 'Стоимость'])
    for r in rows:
        try:
            dt = datetime.fromisoformat(r['order_time'])
        except Exception:
            dt = r['order_time']
        ws.append([dt, r['address'], r['cost']])

    # format date column
    from openpyxl.styles import numbers
    for cell in ws['A'][1:]:
        cell.number_format = 'DD.MM.YYYY HH:MM'

    # auto-width
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 50)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    today = date.today().isoformat()
    return send_file(buf, as_attachment=True,
                     download_name=f'spotbot_orders_{today}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == '__main__':
    from datetime import timedelta
    app.permanent_session_lifetime = timedelta(days=90)
    app.run(host='0.0.0.0', port=5000, debug=False)