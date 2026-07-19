"""
TG Marketing Suite - Flask Backend
==================================
Full-featured Telegram marketing tool with web interface.
Modules: Accounts | Groups | Members | Bulk Send | Auto Reply | Invite | Verify | Profile
"""
import os, sys, json, asyncio, threading, time, queue, re, csv, io
from datetime import datetime
from pathlib import Path

# PyInstaller: sys._MEIPASS points to _internal dir; exe dir is one level up
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent
    BUNDLE_DIR = BASE_DIR
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
sys.path.insert(0, str(BUNDLE_DIR))

from flask import Flask, render_template, request, jsonify, Response, send_file
from flask_sock import Sock
import sqlite3

app = Flask(__name__, static_folder=str(BUNDLE_DIR / "static"), static_url_path="/static")
sock = Sock(app)

DB_PATH = str(DATA_DIR / "tg_marketing.db")

# ---- Log queue for WebSocket streaming ----
log_queue = queue.Queue()
_logs = []  # keep recent 500 log lines

def push_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _logs.append(line)
    if len(_logs) > 500:
        _logs.pop(0)
    log_queue.put(line)

# ---- DB helpers ----
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            session_name TEXT NOT NULL,
            api_id TEXT DEFAULT '2040',
            api_hash TEXT DEFAULT 'b18441a1ff607e10a989891a5462e627',
            proxy_type TEXT DEFAULT 'socks5',
            proxy_host TEXT DEFAULT '127.0.0.1',
            proxy_port TEXT DEFAULT '10808',
            status TEXT DEFAULT 'offline',
            first_name TEXT,
            username TEXT,
            daily_limit INTEGER DEFAULT 50,
            sent_today INTEGER DEFAULT 0,
            session_string TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER NOT NULL,
            title TEXT,
            username TEXT,
            participants INTEGER DEFAULT 0,
            folder TEXT DEFAULT 'default',
            source TEXT,
            account_phone TEXT,
            access_hash TEXT DEFAULT '',
            is_member INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(tg_id, account_phone)
        );

        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            source_group_id INTEGER,
            source_group_title TEXT,
            account_phone TEXT,
            is_contact INTEGER DEFAULT 0,
            can_invite INTEGER DEFAULT 1,
            invited_to_group TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(user_id, source_group_id, account_phone)
        );

        CREATE TABLE IF NOT EXISTS message_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS send_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_phone TEXT,
            target_type TEXT,
            target_id INTEGER,
            target_title TEXT,
            status TEXT,
            error_msg TEXT,
            sent_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS monitor_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            account_phone TEXT,
            group_usernames TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS verify_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_username TEXT NOT NULL,
            question_pattern TEXT,
            answer TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS auto_reply_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            account_phone TEXT,
            keywords TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            group_usernames TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            running INTEGER DEFAULT 0,
            stop_flag INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    db.commit()
    db.close()

init_db()

# Lazy import TG manager
_tg = None

def get_tg():
    global _tg
    if _tg is None:
        from modules.tg_client import get_manager
        _tg = get_manager(log_callback=push_log)
    return _tg

# ---- WebSocket log stream ----
@sock.route('/ws/logs')
def ws_logs(ws):
    # Send existing logs
    for line in _logs[-100:]:
        try:
            ws.send(line)
        except:
            return
    # Stream new logs
    while True:
        try:
            line = log_queue.get(timeout=30)
            ws.send(line)
        except queue.Empty:
            try:
                ws.send('__ping__')
            except:
                break
        except:
            break

# ---- Helper ----
def row_to_dict(row):
    return dict(row) if row else {}

def get_rows(sql, params=()):
    db = get_db()
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    db.close()
    return rows

def get_row(sql, params=()):
    db = get_db()
    r = db.execute(sql, params).fetchone()
    db.close()
    return dict(r) if r else None

def exec_sql(sql, params=()):
    db = get_db()
    db.execute(sql, params)
    db.commit()
    db.close()

# ======================== API Routes ========================

# -- Accounts --
@app.route('/api/accounts', methods=['GET'])
def api_accounts_list():
    accounts = get_rows("SELECT * FROM accounts ORDER BY created_at DESC")
    # attach live status from TG manager
    tg = get_tg()
    for a in accounts:
        a['live_status'] = tg.status.get(a['phone'], 'offline')
    return jsonify(accounts)

@app.route('/api/accounts', methods=['POST'])
def api_account_add():
    data = request.json
    phone = data.get('phone', '').strip()
    if not phone:
        return jsonify({'error': '手机号不能为空'}), 400
    session_name = data.get('session_name', phone)
    api_id = data.get('api_id', '2040')
    api_hash = data.get('api_hash', 'b18441a1ff607e10a989891a5462e627')
    proxy_type = data.get('proxy_type', 'socks5')
    proxy_host = data.get('proxy_host', '127.0.0.1')
    proxy_port = data.get('proxy_port', '10808')
    daily_limit = data.get('daily_limit', 50)

    # Check for existing
    existing = get_row("SELECT id FROM accounts WHERE phone=?", (phone,))
    if existing:
        return jsonify({'error': '该账号已存在'}), 400

    exec_sql("""
        INSERT INTO accounts (phone, session_name, api_id, api_hash, proxy_type, proxy_host, proxy_port, daily_limit)
        VALUES (?,?,?,?,?,?,?,?)
    """, (phone, session_name, api_id, api_hash, proxy_type, proxy_host, proxy_port, daily_limit))

    # Start client
    tg = get_tg()
    tg.add_account(phone, api_id, api_hash, proxy_type, proxy_host, proxy_port)
    push_log(f"账号 {phone} 已添加")

    return jsonify({'success': True, 'id': phone})

@app.route('/api/accounts/<phone>', methods=['PUT'])
def api_account_update(phone):
    data = request.json
    fields = []
    vals = []
    for k in ['proxy_type', 'proxy_host', 'proxy_port', 'daily_limit', 'session_name']:
        if k in data:
            fields.append(f"{k}=?")
            vals.append(data[k])
    if fields:
        vals.append(phone)
        exec_sql(f"UPDATE accounts SET {','.join(fields)} WHERE phone=?", vals)
    return jsonify({'success': True})

@app.route('/api/accounts/<phone>', methods=['DELETE'])
def api_account_delete(phone):
    tg = get_tg()
    tg.logout(phone)
    exec_sql("DELETE FROM accounts WHERE phone=?", (phone,))
    exec_sql("DELETE FROM groups WHERE account_phone=?", (phone,))
    exec_sql("DELETE FROM members WHERE account_phone=?", (phone,))
    push_log(f"账号 {phone} 已删除")
    return jsonify({'success': True})

@app.route('/api/accounts/<phone>/send_code', methods=['POST'])
def api_send_code(phone):
    tg = get_tg()
    if phone not in tg.clients:
        # Re-init client from DB
        acc = get_row("SELECT * FROM accounts WHERE phone=?", (phone,))
        if acc:
            tg.add_account(phone, acc['api_id'], acc['api_hash'],
                          acc['proxy_type'], acc['proxy_host'], acc['proxy_port'])
            time.sleep(2)  # wait for connection

    result = tg.send_code(phone)
    if result is None:
        return jsonify({'error': '发送验证码失败, 请检查代理或网络'}), 500
    if result.get('authorized'):
        return jsonify({'success': True, 'authorized': True})
    return jsonify({'success': True, 'phone_code_hash': result['phone_code_hash']})

@app.route('/api/accounts/<phone>/verify', methods=['POST'])
def api_verify_code(phone):
    data = request.json
    code = data.get('code', '').strip()
    phone_code_hash = data.get('phone_code_hash', '')
    password = data.get('password', '').strip() or None

    tg = get_tg()
    ok = tg.sign_in(phone, code, phone_code_hash, password)
    if ok:
        # Save session string
        ss = tg.sessions.get(phone, '')
        exec_sql("UPDATE accounts SET status='online', session_string=? WHERE phone=?", (ss, phone))
        return jsonify({'success': True})
    return jsonify({'error': '验证码错误或登录失败'}), 400

@app.route('/api/accounts/<phone>/logout', methods=['POST'])
def api_account_logout(phone):
    tg = get_tg()
    tg.logout(phone)
    exec_sql("UPDATE accounts SET status='offline', session_string='' WHERE phone=?", (phone,))
    return jsonify({'success': True})

@app.route('/api/accounts/<phone>/profile', methods=['POST'])
def api_account_profile(phone):
    """Update account profile: name, username, bio, avatar."""
    data = request.json
    tg = get_tg()
    ok = tg.set_profile(
        phone,
        first_name=data.get('first_name'),
        last_name=data.get('last_name'),
        bio=data.get('bio'),
        username=data.get('username'),
        avatar_path=data.get('avatar_path')
    )
    if ok:
        push_log(f"[{phone}] 资料更新完成")
        return jsonify({'success': True})
    return jsonify({'error': '更新失败'}), 500

# -- Groups --
@app.route('/api/groups', methods=['GET'])
def api_groups_list():
    folder = request.args.get('folder', '')
    account = request.args.get('account', '')
    search = request.args.get('search', '')

    sql = "SELECT * FROM groups WHERE 1=1"
    params = []
    if folder and folder != 'all':
        sql += " AND folder=?"
        params.append(folder)
    if account:
        sql += " AND account_phone=?"
        params.append(account)
    if search:
        sql += " AND (title LIKE ? OR username LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
    sql += " ORDER BY participants DESC"

    return jsonify(get_rows(sql, params))

@app.route('/api/groups/folders', methods=['GET'])
def api_groups_folders():
    rows = get_rows("SELECT DISTINCT folder FROM groups ORDER BY folder")
    return jsonify([r['folder'] for r in rows])

@app.route('/api/groups/scrape', methods=['POST'])
def api_groups_scrape():
    data = request.json
    phone = data.get('phone')
    limit = data.get('limit', 200)
    folder = data.get('folder', 'default')

    if not phone:
        return jsonify({'error': '请选择账号'}), 400

    tg = get_tg()
    push_log(f"[{phone}] 开始采集群组列表...")
    dialogs = tg.get_dialogs(phone, limit=int(limit))

    added = 0
    for d in dialogs:
        existing = get_row("SELECT id FROM groups WHERE tg_id=? AND account_phone=?",
                          (d['tg_id'], phone))
        if not existing:
            exec_sql("""
                INSERT INTO groups (tg_id, title, username, participants, folder, source, account_phone, access_hash)
                VALUES (?,?,?,?,?,?,?,?)
            """, (d['tg_id'], d['title'], d['username'], d['participants'],
                  folder, 'scrape', phone, d.get('access_hash', '')))
            added += 1

    push_log(f"[{phone}] 群组采集完成, 新增 {added} 个群")
    return jsonify({'success': True, 'total': len(dialogs), 'added': added})

@app.route('/api/groups/<int:gid>', methods=['PUT'])
def api_group_update(gid):
    data = request.json
    if 'folder' in data:
        exec_sql("UPDATE groups SET folder=? WHERE id=?", (data['folder'], gid))
    return jsonify({'success': True})

@app.route('/api/groups/<int:gid>', methods=['DELETE'])
def api_group_delete(gid):
    exec_sql("DELETE FROM groups WHERE id=?", (gid,))
    return jsonify({'success': True})

@app.route('/api/groups/move', methods=['POST'])
def api_groups_move():
    """Batch move groups to a folder."""
    data = request.json
    ids = data.get('ids', [])
    folder = data.get('folder', 'default')
    if ids:
        placeholders = ','.join('?' * len(ids))
        exec_sql(f"UPDATE groups SET folder=? WHERE id IN ({placeholders})", [folder] + ids)
    return jsonify({'success': True, 'moved': len(ids)})

# -- Members --
@app.route('/api/members', methods=['GET'])
def api_members_list():
    group_id = request.args.get('group_id', '')
    account = request.args.get('account', '')
    can_invite = request.args.get('can_invite', '')
    search = request.args.get('search', '')

    sql = "SELECT * FROM members WHERE 1=1"
    params = []
    if group_id:
        sql += " AND source_group_id=?"
        params.append(int(group_id))
    if account:
        sql += " AND account_phone=?"
        params.append(account)
    if can_invite:
        sql += " AND can_invite=1"
    if search:
        sql += " AND (username LIKE ? OR first_name LIKE ? OR last_name LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
    sql += " ORDER BY created_at DESC LIMIT 10000"

    return jsonify(get_rows(sql, params))

@app.route('/api/members/count', methods=['GET'])
def api_members_count():
    group_id = request.args.get('group_id', '')
    sql = "SELECT COUNT(*) as cnt FROM members"
    params = []
    if group_id:
        sql += " WHERE source_group_id=?"
        params.append(int(group_id))
    row = get_row(sql, params)
    return jsonify({'count': row['cnt'] if row else 0})

@app.route('/api/members/scrape', methods=['POST'])
def api_members_scrape():
    data = request.json
    phone = data.get('phone')
    group_tg_id = data.get('group_tg_id')
    group_title = data.get('group_title', '')
    access_hash = data.get('access_hash', '')
    limit = data.get('limit', 5000)

    if not phone or not group_tg_id:
        return jsonify({'error': '缺少参数'}), 400

    push_log(f"[{phone}] 开始采集群成员: {group_title}")

    # Run in background thread
    def _do_scrape():
        tg = get_tg()
        members = tg.get_participants(phone, int(group_tg_id), access_hash, int(limit))
        if not members:
            push_log(f"[{phone}] 获取成员失败或为空")
            return

        added = 0
        for m in members:
            existing = get_row(
                "SELECT id FROM members WHERE user_id=? AND source_group_id=? AND account_phone=?",
                (m['user_id'], int(group_tg_id), phone)
            )
            if not existing:
                exec_sql("""
                    INSERT INTO members (user_id, username, first_name, last_name, phone, source_group_id, source_group_title, account_phone)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (m['user_id'], m['username'], m['first_name'], m['last_name'], m['phone'],
                      int(group_tg_id), group_title, phone))
                added += 1

        push_log(f"[{phone}] 成员采集完成: {group_title}, 新增 {added}/{len(members)} 人")

    threading.Thread(target=_do_scrape, daemon=True).start()
    return jsonify({'success': True, 'message': '采集任务已启动, 请查看日志'})

# -- Templates --
@app.route('/api/templates', methods=['GET'])
def api_templates_list():
    return jsonify(get_rows("SELECT * FROM message_templates ORDER BY created_at DESC"))

@app.route('/api/templates', methods=['POST'])
def api_template_add():
    data = request.json
    name = data.get('name', '').strip()
    content = data.get('content', '').strip()
    if not name:
        return jsonify({'error': '名称不能为空'}), 400
    exec_sql("INSERT INTO message_templates (name, content) VALUES (?,?)", (name, content))
    return jsonify({'success': True})

@app.route('/api/templates/<int:tid>', methods=['PUT'])
def api_template_update(tid):
    data = request.json
    exec_sql("UPDATE message_templates SET name=?, content=? WHERE id=?",
             (data.get('name'), data.get('content'), tid))
    return jsonify({'success': True})

@app.route('/api/templates/<int:tid>', methods=['DELETE'])
def api_template_delete(tid):
    exec_sql("DELETE FROM message_templates WHERE id=?", (tid,))
    return jsonify({'success': True})

# -- Bulk Send --
@app.route('/api/send', methods=['POST'])
def api_send_bulk():
    data = request.json
    phone = data.get('phone')
    message = data.get('message', '')
    member_ids = data.get('member_ids', [])  # list of member row IDs
    delay = data.get('delay', 2)

    if not phone:
        return jsonify({'error': '请选择账号'}), 400
    if not message:
        return jsonify({'error': '消息不能为空'}), 400

    # Get targets
    if member_ids:
        placeholders = ','.join('?' * len(member_ids))
        targets = get_rows(f"SELECT user_id, username FROM members WHERE id IN ({placeholders})", member_ids)
    else:
        targets = get_rows("SELECT user_id, username FROM members WHERE account_phone=?", (phone,))

    if not targets:
        return jsonify({'error': '没有可发送的目标'}), 400

    push_log(f"[{phone}] 开始群发, 目标: {len(targets)} 人")

    def _do_send():
        tg = get_tg()
        def progress(done, total):
            push_log(f"[{phone}] 发送进度: {done}/{total}")
        success, fail, errors = tg.send_bulk_messages(phone, targets, message, delay, progress)
        push_log(f"[{phone}] 群发完成: 成功 {success}, 失败 {fail}")
        # Update sent_today
        exec_sql("UPDATE accounts SET sent_today=sent_today+? WHERE phone=?", (success, phone))
        # Log
        for t in targets[:success]:
            exec_sql("""
                INSERT INTO send_logs (account_phone, target_type, target_id, target_title, status)
                VALUES (?, 'user', ?, ?, 'success')
            """, (phone, t['user_id'], t.get('username') or str(t['user_id'])))

    threading.Thread(target=_do_send, daemon=True).start()
    return jsonify({'success': True, 'total': len(targets)})

@app.route('/api/send/single', methods=['POST'])
def api_send_single():
    """Send a single message to a user by username."""
    data = request.json
    phone = data.get('phone')
    target = data.get('target', '')  # @username or user_id
    message = data.get('message', '')

    if not phone or not target or not message:
        return jsonify({'error': '缺少参数'}), 400

    tg = get_tg()
    ok = tg.send_message(phone, target, message, is_username=not target.isdigit())
    if ok:
        push_log(f"[{phone}] 私信已发送 -> {target}")
        return jsonify({'success': True})
    return jsonify({'error': '发送失败'}), 500

# -- Invite --
@app.route('/api/invite', methods=['POST'])
def api_invite_to_group():
    data = request.json
    phone = data.get('phone')
    group_tg_id = data.get('group_tg_id')
    member_ids = data.get('member_ids', [])

    if not phone or not group_tg_id or not member_ids:
        return jsonify({'error': '缺少参数'}), 400

    # Get user_ids from member records
    placeholders = ','.join('?' * len(member_ids))
    rows = get_rows(f"SELECT user_id FROM members WHERE id IN ({placeholders})", member_ids)
    user_ids = [r['user_id'] for r in rows]

    group = get_row("SELECT * FROM groups WHERE tg_id=?", (int(group_tg_id),))
    group_title = group['title'] if group else str(group_tg_id)

    push_log(f"[{phone}] 开始拉人: {len(user_ids)} 人 -> {group_title}")

    def _do_invite():
        tg = get_tg()
        success, fail = tg.invite_to_group(phone, int(group_tg_id), user_ids)
        push_log(f"[{phone}] 拉人完成: 成功 {success}, 失败 {fail}")
        # Mark invited
        for mid in member_ids[:success]:
            exec_sql("UPDATE members SET invited_to_group=? WHERE id=?", (str(group_tg_id), mid))

    threading.Thread(target=_do_invite, daemon=True).start()
    return jsonify({'success': True, 'total': len(user_ids)})

# -- Auto Reply / Monitor --
@app.route('/api/monitor/keywords', methods=['GET'])
def api_monitor_keywords():
    account = request.args.get('account', '')
    sql = "SELECT * FROM monitor_keywords"
    params = []
    if account:
        sql += " WHERE account_phone=?"
        params.append(account)
    sql += " ORDER BY created_at DESC"
    return jsonify(get_rows(sql, params))

@app.route('/api/monitor/keywords', methods=['POST'])
def api_monitor_keyword_add():
    data = request.json
    exec_sql("""
        INSERT INTO monitor_keywords (keyword, reply_text, account_phone, group_usernames)
        VALUES (?,?,?,?)
    """, (data.get('keyword'), data.get('reply_text'), data.get('account_phone'), data.get('group_usernames', '')))
    return jsonify({'success': True})

@app.route('/api/monitor/keywords/<int:kid>', methods=['PUT'])
def api_monitor_keyword_update(kid):
    data = request.json
    exec_sql("""
        UPDATE monitor_keywords SET keyword=?, reply_text=?, account_phone=?, group_usernames=?, enabled=?
        WHERE id=?
    """, (data.get('keyword'), data.get('reply_text'), data.get('account_phone'),
          data.get('group_usernames', ''), data.get('enabled', 1), kid))
    return jsonify({'success': True})

@app.route('/api/monitor/keywords/<int:kid>', methods=['DELETE'])
def api_monitor_keyword_delete(kid):
    exec_sql("DELETE FROM monitor_keywords WHERE id=?", (kid,))
    return jsonify({'success': True})

# Active monitor tasks tracking
_monitor_tasks = {}  # kid -> {'stop_event': event, 'phone': phone}

@app.route('/api/monitor/start', methods=['POST'])
def api_monitor_start():
    data = request.json
    kid = data.get('keyword_id')
    kw = get_row("SELECT * FROM monitor_keywords WHERE id=?", (kid,))
    if not kw:
        return jsonify({'error': '关键词不存在'}), 404

    phone = kw['account_phone']
    keywords = [k.strip() for k in kw['keyword'].split(',') if k.strip()]
    reply_text = kw['reply_text']
    groups = [g.strip() for g in kw['group_usernames'].split(',') if g.strip()] if kw['group_usernames'] else None

    if kid in _monitor_tasks:
        return jsonify({'error': '该监听已在运行'}), 400

    push_log(f"[{phone}] 启动监听: 关键词={keywords}")

    tg = get_tg()
    stop_event = tg.listen_messages(phone, keywords, reply_text, groups,
                                     callback=lambda hit: push_log(
                                         f"[{phone}] 命中: {hit['keyword']} @ {hit['chat']}"
                                     ))
    _monitor_tasks[kid] = {'stop_event': stop_event, 'phone': phone}
    exec_sql("UPDATE monitor_keywords SET enabled=1 WHERE id=?", (kid,))
    return jsonify({'success': True})

@app.route('/api/monitor/stop', methods=['POST'])
def api_monitor_stop():
    data = request.json
    kid = data.get('keyword_id')
    task = _monitor_tasks.pop(kid, None)
    if task:
        task['stop_event'].set()
        if hasattr(task['stop_event'], '_cleanup'):
            task['stop_event']._cleanup()
        push_log(f"[{task['phone']}] 监听已停止")
        exec_sql("UPDATE monitor_keywords SET enabled=0 WHERE id=?", (kid,))
    return jsonify({'success': True})

@app.route('/api/monitor/hits', methods=['GET'])
def api_monitor_hits():
    """Get recent auto-reply hits."""
    rows = get_rows("SELECT * FROM send_logs WHERE target_type='auto_reply' ORDER BY sent_at DESC LIMIT 200")
    return jsonify(rows)

# -- Verify (过验证) --
@app.route('/api/verify/configs', methods=['GET'])
def api_verify_configs():
    return jsonify(get_rows("SELECT * FROM verify_configs ORDER BY created_at DESC"))

@app.route('/api/verify/configs', methods=['POST'])
def api_verify_config_add():
    data = request.json
    exec_sql("""
        INSERT INTO verify_configs (bot_username, question_pattern, answer)
        VALUES (?,?,?)
    """, (data.get('bot_username'), data.get('question_pattern', ''), data.get('answer')))
    return jsonify({'success': True})

@app.route('/api/verify/configs/<int:vid>', methods=['DELETE'])
def api_verify_config_delete(vid):
    exec_sql("DELETE FROM verify_configs WHERE id=?", (vid,))
    return jsonify({'success': True})

@app.route('/api/verify/run', methods=['POST'])
def api_verify_run():
    data = request.json
    phone = data.get('phone')
    bot_username = data.get('bot_username')
    answer = data.get('answer')

    if not phone or not bot_username or not answer:
        return jsonify({'error': '缺少参数'}), 400

    tg = get_tg()
    push_log(f"[{phone}] 尝试过验证: {bot_username} -> {answer}")
    ok = tg.check_verify_bot(phone, bot_username, answer)
    if ok:
        push_log(f"[{phone}] 验证完成: {bot_username}")
    return jsonify({'success': ok})

# -- Logs --
@app.route('/api/logs', methods=['GET'])
def api_logs():
    limit = int(request.args.get('limit', 200))
    return jsonify(_logs[-limit:])

@app.route('/api/logs/clear', methods=['POST'])
def api_logs_clear():
    _logs.clear()
    return jsonify({'success': True})

# -- Export --
@app.route('/api/export/members', methods=['GET'])
def api_export_members():
    group_id = request.args.get('group_id', '')
    fmt = request.args.get('format', 'csv')

    sql = "SELECT * FROM members"
    params = []
    if group_id:
        sql += " WHERE source_group_id=?"
        params.append(int(group_id))
    rows = get_rows(sql, params)

    if fmt == 'csv':
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return Response(output.getvalue(), mimetype='text/csv',
                       headers={'Content-Disposition': 'attachment;filename=members.csv'})

    elif fmt == 'json':
        return Response(json.dumps(rows, ensure_ascii=False, indent=2),
                       mimetype='application/json',
                       headers={'Content-Disposition': 'attachment;filename=members.json'})

    return jsonify({'error': 'unsupported format'}), 400

@app.route('/api/export/groups', methods=['GET'])
def api_export_groups():
    fmt = request.args.get('format', 'csv')
    rows = get_rows("SELECT * FROM groups ORDER BY participants DESC")

    if fmt == 'csv':
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return Response(output.getvalue(), mimetype='text/csv',
                       headers={'Content-Disposition': 'attachment;filename=groups.csv'})
    elif fmt == 'json':
        return Response(json.dumps(rows, ensure_ascii=False, indent=2),
                       mimetype='application/json',
                       headers={'Content-Disposition': 'attachment;filename=groups.json'})
    return jsonify({'error': 'unsupported format'}), 400

# -- Stats --
@app.route('/api/stats', methods=['GET'])
def api_stats():
    db = get_db()
    acc_count = db.execute("SELECT COUNT(*) as c FROM accounts").fetchone()['c']
    grp_count = db.execute("SELECT COUNT(*) as c FROM groups").fetchone()['c']
    mbr_count = db.execute("SELECT COUNT(*) as c FROM members").fetchone()['c']
    tpl_count = db.execute("SELECT COUNT(*) as c FROM message_templates").fetchone()['c']
    kw_count  = db.execute("SELECT COUNT(*) as c FROM monitor_keywords").fetchone()['c']
    db.close()
    return jsonify({
        'accounts': acc_count,
        'groups': grp_count,
        'members': mbr_count,
        'templates': tpl_count,
        'keywords': kw_count
    })

# -- Main page --
@app.route('/')
def index():
    return send_file(str(BUNDLE_DIR / 'static' / 'index.html'))

# ======================== Startup ========================
def startup():
    """Auto-connect previously online accounts."""
    accounts = get_rows("SELECT * FROM accounts WHERE session_string != ''")
    tg = get_tg()
    for acc in accounts:
        push_log(f"自动连接: {acc['phone']}")
        tg.add_account(
            acc['phone'], acc['api_id'], acc['api_hash'],
            acc['proxy_type'], acc['proxy_host'], acc['proxy_port'],
            session_str=acc['session_string']
        )

if __name__ == '__main__':
    threading.Thread(target=startup, daemon=True).start()
    print("TG Marketing Suite running on http://localhost:8520")
    app.run(host='0.0.0.0', port=8520, debug=False, threaded=True)
