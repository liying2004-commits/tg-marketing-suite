# -*- coding: utf-8 -*-
"""
TG Marketing Suite - Desktop GUI
================================
模块: 账号管理 | 群组采集 | 成员采集 | 群发私信 | 拉人 | 自动回复 | 私信监听 | 过验证 | 账号设置
"""
import os, sys, json, asyncio, threading, time, queue, csv, io
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Paths
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BASE_DIR = Path(__file__).parent
    BUNDLE_DIR = BASE_DIR

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
SESSIONS_DIR = BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)
sys.path.insert(0, str(BUNDLE_DIR))

import sqlite3

# ---- DB ----
DB_PATH = str(DATA_DIR / "tg_marketing.db")

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
    """)
    db.commit()
    db.close()

init_db()

def db_query(sql, params=()):
    db = get_db()
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    db.close()
    return rows

def db_exec(sql, params=()):
    db = get_db()
    db.execute(sql, params)
    db.commit()
    db.close()

def db_one(sql, params=()):
    db = get_db()
    r = db.execute(sql, params).fetchone()
    db.close()
    return dict(r) if r else None

# ---- TG Client ----
from modules.tg_client import get_manager

log_queue = queue.Queue()

def push_log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    log_queue.put(f"[{ts}] {msg}")

tg = get_manager(log_callback=push_log)


# ====================== GUI ======================
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("TG Marketing Suite")
        self.root.geometry("1200x780")
        self.root.configure(bg="#f0f0f0")
        self.root.minsize(1000, 650)

        # Style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background='#f0f0f0', borderwidth=1)
        style.configure('TNotebook.Tab', padding=[12, 4], font=('Microsoft YaHei', 10))
        style.configure('Treeview', rowheight=26, font=('Microsoft YaHei', 9))
        style.configure('Treeview.Heading', font=('Microsoft YaHei', 9, 'bold'))

        self._build_ui()
        self._monitor_tasks = {}
        self._process_logs()

    # ---- Build UI ----
    def _build_ui(self):
        # Top frame: controls
        top = ttk.Frame(self.root, padding=5)
        top.pack(fill=tk.X)

        ttk.Label(top, text="TG Marketing Suite", font=('Microsoft YaHei', 14, 'bold')).pack(side=tk.LEFT, padx=10)

        self.status_label = ttk.Label(top, text="● 就绪", foreground="gray", font=('Microsoft YaHei', 10))
        self.status_label.pack(side=tk.RIGHT, padx=15)

        # Notebook
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        self._tab_accounts()
        self._tab_groups_scrape()
        self._tab_groups_manage()
        self._tab_folders()
        self._tab_members()
        self._tab_send()
        self._tab_dm()
        self._tab_templates()
        self._tab_monitor()
        self._tab_group_listen()
        self._tab_invite()
        self._tab_verify()
        self._tab_profile()

        self.nb.bind('<<NotebookTabChanged>>', self._on_tab_changed)

        # Bottom log
        log_frame = ttk.LabelFrame(self.root, text="日志", padding=3)
        log_frame.pack(fill=tk.BOTH, padx=5, pady=(0,2))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, font=('Consolas', 10),
                                                   bg='#1e1e1e', fg='#d4d4d4', wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Bottom status bar
        self.progress = ttk.Progressbar(self.root, mode='indeterminate')
        self.progress.pack(fill=tk.X, padx=5, pady=(0,3))

    def _on_tab_changed(self, event):
        tab_text = self.nb.tab(self.nb.index(self.nb.select()), "text")
        tabs_to_refresh = {"成员采集", "消息群发", "用户私信", "群组拉人", "自动回复", "私信监听"}
        if tab_text in tabs_to_refresh:
            self._fill_combos()

    def _process_logs(self):
        while not log_queue.empty():
            msg = log_queue.get_nowait()
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
        self.root.after(300, self._process_logs)

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def set_status(self, text, color="gray"):
        self.status_label.config(text=text, foreground=color)

    # ====================== Tab: 账号管理 ======================
    def _tab_accounts(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="账号管理")

        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill=tk.X, pady=(0,5))
        ttk.Button(btn_frame, text="+ 添加账号", command=self._add_account_dialog).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame, text="刷新", command=self._refresh_accounts).pack(side=tk.LEFT, padx=3)

        cols = ('手机号', '状态', '代理', '日限额', '今日已发', 'First Name')
        self.acc_tree = ttk.Treeview(f, columns=cols, show='headings', selectmode='browse')
        for c in cols:
            self.acc_tree.heading(c, text=c)
            self.acc_tree.column(c, width=100, anchor='center')
        self.acc_tree.column('手机号', width=130)
        self.acc_tree.column('代理', width=180)
        self.acc_tree.column('状态', width=70)
        self.acc_tree.pack(fill=tk.BOTH, expand=True)

        btn_frame2 = ttk.Frame(f)
        btn_frame2.pack(fill=tk.X, pady=5)
        ttk.Button(btn_frame2, text="登录", command=self._login_account).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame2, text="登出", command=self._logout_account).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame2, text="删除", command=self._delete_account).pack(side=tk.LEFT, padx=3)

    def _refresh_accounts(self):
        for item in self.acc_tree.get_children():
            self.acc_tree.delete(item)
        for a in db_query("SELECT * FROM accounts ORDER BY created_at DESC"):
            live = tg.status.get(a['phone'], 'offline')
            self.acc_tree.insert('', 'end', iid=a['phone'], values=(
                a['phone'], live,
                f"{a['proxy_type']}://{a['proxy_host']}:{a['proxy_port']}",
                a['daily_limit'], a['sent_today'],
                a['first_name'] or '-'
            ))
        self._fill_combos()

    def _add_account_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("添加账号")
        dlg.geometry("400x320")
        dlg.transient(self.root)
        dlg.grab_set()

        fields = [
            ('手机号', 'phone', '+8613800000000'),
            ('API ID', 'api_id', '2040'),
            ('API Hash', 'api_hash', 'b18441a1ff607e10a989891a5462e627'),
            ('代理类型', 'proxy_type', 'socks5'),
            ('代理地址', 'proxy_host', '127.0.0.1'),
            ('代理端口', 'proxy_port', '10808'),
            ('日限额', 'daily_limit', '50'),
        ]
        entries = {}
        for i, (label, key, default) in enumerate(fields):
            ttk.Label(dlg, text=label).grid(row=i, column=0, sticky='e', padx=5, pady=4)
            e = ttk.Entry(dlg, width=30)
            e.insert(0, default)
            e.grid(row=i, column=1, padx=5, pady=4, sticky='w')
            entries[key] = e

        def _save():
            data = {k: e.get().strip() for k, e in entries.items()}
            if not data['phone']:
                messagebox.showerror("错误", "手机号不能为空")
                return
            if db_one("SELECT id FROM accounts WHERE phone=?", (data['phone'],)):
                messagebox.showerror("错误", "该账号已存在")
                return
            db_exec("""
                INSERT INTO accounts (phone, session_name, api_id, api_hash, proxy_type, proxy_host, proxy_port, daily_limit)
                VALUES (?,?,?,?,?,?,?,?)
            """, (data['phone'], data['phone'], data['api_id'], data['api_hash'],
                  data['proxy_type'], data['proxy_host'], data['proxy_port'], int(data['daily_limit'])))
            tg.add_account(data['phone'], data['api_id'], data['api_hash'],
                          data['proxy_type'], data['proxy_host'], data['proxy_port'])
            self.log(f"账号 {data['phone']} 已添加")
            self._refresh_accounts()
            dlg.destroy()

        ttk.Button(dlg, text="确定添加", command=_save).grid(row=7, column=0, columnspan=2, pady=15)

    def _login_account(self):
        sel = self.acc_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个账号")
            return
        phone = sel[0]
        dlg = tk.Toplevel(self.root)
        dlg.title(f"登录 {phone}")
        dlg.geometry("350x220")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text=f"账号: {phone}").pack(pady=5)
        ttk.Label(dlg, text="验证码:").pack()
        code_entry = ttk.Entry(dlg, width=20)
        code_entry.pack(pady=2)
        ttk.Label(dlg, text="2FA密码 (无则不填):").pack()
        pwd_entry = ttk.Entry(dlg, width=20, show='*')
        pwd_entry.pack(pady=2)
        msg_label = ttk.Label(dlg, text="")
        msg_label.pack(pady=3)

        def _send_code():
            result = tg.send_code(phone)
            if result is None:
                msg_label.config(text="发送失败, 请检查网络/代理")
                return
            if result.get('authorized'):
                msg_label.config(text="已授权, 无需登录")
                self._refresh_accounts()
                return
            self._login_hash = result.get('phone_code_hash', '')
            msg_label.config(text="验证码已发送")

        def _verify():
            code = code_entry.get().strip()
            pwd = pwd_entry.get().strip() or None
            if not code:
                return
            ok = tg.sign_in(phone, code, getattr(self, '_login_hash', ''), pwd)
            if ok:
                ss = tg.sessions.get(phone, '')
                db_exec("UPDATE accounts SET status='online', session_string=? WHERE phone=?", (ss, phone))
                self.log(f"{phone} 登录成功")
                self._refresh_accounts()
                dlg.destroy()
            else:
                msg_label.config(text="验证码错误")

        ttk.Button(dlg, text="发送验证码", command=_send_code).pack(pady=5)
        ttk.Button(dlg, text="登录", command=_verify).pack(pady=3)

    def _logout_account(self):
        sel = self.acc_tree.selection()
        if not sel: return
        phone = sel[0]
        if messagebox.askyesno("确认", f"确定登出 {phone} 吗?"):
            tg.logout(phone)
            db_exec("UPDATE accounts SET status='offline', session_string='' WHERE phone=?", (phone,))
            self._refresh_accounts()

    def _delete_account(self):
        sel = self.acc_tree.selection()
        if not sel: return
        phone = sel[0]
        if messagebox.askyesno("确认", f"确定删除 {phone} 及其所有数据吗?"):
            tg.logout(phone)
            db_exec("DELETE FROM accounts WHERE phone=?", (phone,))
            db_exec("DELETE FROM groups WHERE account_phone=?", (phone,))
            db_exec("DELETE FROM members WHERE account_phone=?", (phone,))
            self._refresh_accounts()

    # ====================== Tab: 群组采集 ======================
    def _tab_groups_scrape(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="群组采集")

        ttk.Label(f, text="选择账号:").grid(row=0, column=0, sticky='e', padx=5, pady=4)
        self.gs_phone = ttk.Combobox(f, state='readonly', width=25)
        self.gs_phone.grid(row=0, column=1, sticky='w', padx=5)

        ttk.Label(f, text="采集上限:").grid(row=1, column=0, sticky='e', padx=5, pady=4)
        self.gs_limit = ttk.Entry(f, width=10)
        self.gs_limit.insert(0, '200')
        self.gs_limit.grid(row=1, column=1, sticky='w', padx=5)

        ttk.Label(f, text="存入文件夹:").grid(row=2, column=0, sticky='e', padx=5, pady=4)
        self.gs_folder = ttk.Entry(f, width=15)
        self.gs_folder.insert(0, 'default')
        self.gs_folder.grid(row=2, column=1, sticky='w', padx=5)

        ttk.Button(f, text="开始采集群组", command=self._scrape_groups).grid(row=3, column=1, sticky='w', padx=5, pady=10)
        self.gs_label = ttk.Label(f, text="")
        self.gs_label.grid(row=4, column=1, sticky='w', padx=5)

        ttk.Button(f, text="刷新账号列表", command=self._fill_combos).grid(row=5, column=1, sticky='w', padx=5)

    def _scrape_groups(self):
        phone = self.gs_phone.get()
        if not phone:
            messagebox.showwarning("提示", "请选择账号")
            return
        limit = int(self.gs_limit.get() or 200)
        folder = self.gs_folder.get().strip() or 'default'

        self.set_status("正在采集群组...", "orange")
        self.progress.start()

        def _do():
            dialogs = tg.get_dialogs(phone, limit)
            added = 0
            for d in dialogs:
                if not db_one("SELECT id FROM groups WHERE tg_id=? AND account_phone=?", (d['tg_id'], phone)):
                    db_exec("""
                        INSERT INTO groups (tg_id, title, username, participants, folder, source, account_phone, access_hash)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (d['tg_id'], d['title'], d['username'], d['participants'],
                          folder, 'scrape', phone, d.get('access_hash', '')))
                    added += 1
            self.root.after(0, lambda: [
                self.gs_label.config(text=f"完成: 扫描 {len(dialogs)} 个对话, 新增 {added} 个群组"),
                self.set_status("就绪", "gray"),
                self.progress.stop(),
                self._fill_combos(),
                self._load_groups()
            ])
            self.log(f"[{phone}] 群组采集完成: 新增 {added}")

        threading.Thread(target=_do, daemon=True).start()

    # ====================== Tab: 群组管理 ======================
    def _tab_groups_manage(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="群组管理")

        top = ttk.Frame(f)
        top.pack(fill=tk.X, pady=(0,5))

        ttk.Label(top, text="文件夹:").pack(side=tk.LEFT)
        self.gm_folder = ttk.Combobox(top, state='readonly', width=12)
        self.gm_folder.pack(side=tk.LEFT, padx=3)
        self.gm_folder.bind('<<ComboboxSelected>>', lambda e: self._load_groups())

        ttk.Label(top, text="搜索:").pack(side=tk.LEFT, padx=(10,0))
        self.gm_search = ttk.Entry(top, width=15)
        self.gm_search.pack(side=tk.LEFT, padx=3)
        self.gm_search.bind('<Return>', lambda e: self._load_groups())

        ttk.Button(top, text="刷新", command=self._load_groups).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="全选", command=lambda: self._toggle_all(self.gm_tree, True)).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="取消全选", command=lambda: self._toggle_all(self.gm_tree, False)).pack(side=tk.LEFT)

        cols = ('#', '群名', 'Username', '人数', '文件夹')
        self.gm_tree = ttk.Treeview(f, columns=cols, show='headings', selectmode='extended')
        self.gm_tree.heading('#', text='☐'); self.gm_tree.column('#', width=30, anchor='center')
        self.gm_tree.heading('群名', text='群名'); self.gm_tree.column('群名', width=280)
        self.gm_tree.heading('Username', text='Username'); self.gm_tree.column('Username', width=150)
        self.gm_tree.heading('人数', text='人数'); self.gm_tree.column('人数', width=70, anchor='center')
        self.gm_tree.heading('文件夹', text='文件夹'); self.gm_tree.column('文件夹', width=80, anchor='center')
        self.gm_tree.pack(fill=tk.BOTH, expand=True)

        btn = ttk.Frame(f)
        btn.pack(fill=tk.X, pady=5)
        ttk.Label(btn, text="移动到:").pack(side=tk.LEFT)
        self.gm_move_entry = ttk.Entry(btn, width=12)
        self.gm_move_entry.insert(0, 'default')
        self.gm_move_entry.pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="移动选中", command=self._move_groups).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="删除选中", command=self._delete_groups).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="导出CSV", command=self._export_groups).pack(side=tk.LEFT, padx=10)

    def _load_groups(self):
        for item in self.gm_tree.get_children():
            self.gm_tree.delete(item)
        folder = self.gm_folder.get()
        search = self.gm_search.get().strip()

        sql = "SELECT * FROM groups WHERE 1=1"
        params = []
        if folder and folder != '全部':
            sql += " AND folder=?"
            params.append(folder)
        if search:
            sql += " AND (title LIKE ? OR username LIKE ?)"
            params.extend([f'%{search}%', f'%{search}%'])
        sql += " ORDER BY participants DESC LIMIT 5000"

        for i, g in enumerate(db_query(sql, params)):
            self.gm_tree.insert('', 'end', iid=str(g['id']), values=(i+1, g['title'] or '(无)', g['username'] or '-', g['participants'], g['folder']))

        # Update folder filter
        folders = ['全部'] + [r['folder'] for r in db_query("SELECT DISTINCT folder FROM groups ORDER BY folder")]
        self.gm_folder['values'] = folders
        if not self.gm_folder.get():
            self.gm_folder.set('全部')

    def _toggle_all(self, tree, sel):
        if sel:
            for item in tree.get_children():
                tree.selection_add(item)
        else:
            tree.selection_remove(*tree.selection())

    def _move_groups(self):
        ids = [int(i) for i in self.gm_tree.selection()]
        if not ids: return
        folder = self.gm_move_entry.get().strip() or 'default'
        for gid in ids:
            db_exec("UPDATE groups SET folder=? WHERE id=?", (folder, gid))
        self.log(f"已移动 {len(ids)} 个群组到 {folder}")
        self._load_groups()

    def _delete_groups(self):
        ids = [int(i) for i in self.gm_tree.selection()]
        if not ids: return
        if not messagebox.askyesno("确认", f"删除 {len(ids)} 个群组?"): return
        for gid in ids:
            db_exec("DELETE FROM groups WHERE id=?", (gid,))
        self._load_groups()

    def _export_groups(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path: return
        rows = db_query("SELECT * FROM groups ORDER BY participants DESC")
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        self.log(f"已导出 {len(rows)} 个群组到 {path}")

    # ====================== Tab: 文件夹管理 ======================
    def _tab_folders(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="文件夹管理")

        top = ttk.Frame(f)
        top.pack(fill=tk.X, pady=(0,5))
        ttk.Label(top, text="选中群组 → 移动到:").pack(side=tk.LEFT)
        self.fd_target = ttk.Entry(top, width=15)
        self.fd_target.insert(0, 'default')
        self.fd_target.pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text="移动", command=self._fd_move).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="刷新", command=self._fd_load).pack(side=tk.LEFT, padx=10)
        ttk.Label(top, text="文件夹:").pack(side=tk.LEFT, padx=(10,0))
        self.fd_list = ttk.Combobox(top, state='readonly', width=14)
        self.fd_list.pack(side=tk.LEFT, padx=3)
        self.fd_list.bind('<<ComboboxSelected>>', lambda e: self._fd_load())
        ttk.Button(top, text="删除文件夹", command=self._fd_delete).pack(side=tk.LEFT, padx=3)

        cols = ('☐', '#', '群名', 'Username', '人数', '文件夹')
        self.fd_tree = ttk.Treeview(f, columns=cols, show='headings', selectmode='extended')
        self.fd_tree.heading('☐', text='☐'); self.fd_tree.column('☐', width=30, anchor='center')
        self.fd_tree.heading('#', text='#'); self.fd_tree.column('#', width=35, anchor='center')
        self.fd_tree.heading('群名', text='群名'); self.fd_tree.column('群名', width=320)
        self.fd_tree.heading('Username', text='Username'); self.fd_tree.column('Username', width=160)
        self.fd_tree.heading('人数', text='人数'); self.fd_tree.column('人数', width=80, anchor='center')
        self.fd_tree.heading('文件夹', text='文件夹'); self.fd_tree.column('文件夹', width=90, anchor='center')
        self.fd_tree.pack(fill=tk.BOTH, expand=True)

    def _fd_load(self):
        for item in self.fd_tree.get_children():
            self.fd_tree.delete(item)
        folder = self.fd_list.get()
        sql = "SELECT * FROM groups WHERE 1=1"
        params = []
        if folder:
            sql += " AND folder=?"
            params.append(folder)
        sql += " ORDER BY folder, participants DESC LIMIT 5000"
        rows = db_query(sql, params)
        for i, g in enumerate(rows):
            self.fd_tree.insert('', 'end', iid=str(g['id']), values=('', i+1, g['title'] or '(无)', g['username'] or '-', g['participants'], g['folder']))
        folders = sorted(set(r['folder'] for r in db_query("SELECT DISTINCT folder FROM groups")))
        self.fd_list['values'] = folders

    def _fd_move(self):
        ids = [int(i) for i in self.fd_tree.selection()]
        if not ids: return
        folder = self.fd_target.get().strip() or 'default'
        for gid in ids:
            db_exec("UPDATE groups SET folder=? WHERE id=?", (folder, gid))
        self.log(f"已移动 {len(ids)} 个群组到 [{folder}]")
        self._fd_load()

    def _fd_delete(self):
        folder = self.fd_list.get()
        if not folder: return
        if not messagebox.askyesno("确认", f"删除文件夹 [{folder}]?\n其中的群组将移到 'default'"):
            return
        db_exec("UPDATE groups SET folder='default' WHERE folder=?", (folder,))
        self.log(f"文件夹 [{folder}] 已删除")
        self._fd_load()

    # ====================== Tab: 成员采集 ======================
    def _tab_members(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="成员采集")

        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(left, text="选择账号:").pack(anchor='w')
        self.ms_phone = ttk.Combobox(left, state='readonly', width=22)
        self.ms_phone.pack(pady=2)
        self.ms_phone.bind('<<ComboboxSelected>>', self._fill_ms_groups)

        ttk.Label(left, text="选择群组:").pack(anchor='w', pady=(8,0))
        self.ms_group = ttk.Combobox(left, state='readonly', width=22)
        self.ms_group.pack(pady=2)

        ttk.Label(left, text="采集上限:").pack(anchor='w', pady=(8,0))
        self.ms_limit = ttk.Entry(left, width=10)
        self.ms_limit.insert(0, '5000')
        self.ms_limit.pack(pady=2)

        ttk.Button(left, text="开始采集成员", command=self._scrape_members).pack(pady=10)
        self.ms_label = ttk.Label(left, text="")
        self.ms_label.pack()

        # Right: member list
        right = ttk.Frame(f)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        top = ttk.Frame(right)
        top.pack(fill=tk.X)
        ttk.Label(top, text="搜索:").pack(side=tk.LEFT)
        self.mb_search = ttk.Entry(top, width=15)
        self.mb_search.pack(side=tk.LEFT, padx=3)
        self.mb_search.bind('<Return>', lambda e: self._load_members())
        ttk.Button(top, text="刷新", command=self._load_members).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="全选", command=lambda: self._toggle_all(self.mb_tree, True)).pack(side=tk.LEFT, padx=3)
        ttk.Button(top, text="导出CSV", command=self._export_members).pack(side=tk.LEFT, padx=3)
        self.mb_count = ttk.Label(top, text="共0人")
        self.mb_count.pack(side=tk.RIGHT, padx=5)

        cols = ('#', '用户名', '昵称', '姓', '来源群', '已邀请到')
        self.mb_tree = ttk.Treeview(right, columns=cols, show='headings', selectmode='extended')
        self.mb_tree.heading('#', text='☐'); self.mb_tree.column('#', width=30, anchor='center')
        self.mb_tree.heading('用户名', text='用户名'); self.mb_tree.column('用户名', width=140)
        self.mb_tree.heading('昵称', text='昵称'); self.mb_tree.column('昵称', width=150)
        self.mb_tree.heading('姓', text='姓'); self.mb_tree.column('姓', width=100)
        self.mb_tree.heading('来源群', text='来源群'); self.mb_tree.column('来源群', width=150)
        self.mb_tree.heading('已邀请到', text='已邀请到'); self.mb_tree.column('已邀请到', width=80, anchor='center')
        self.mb_tree.pack(fill=tk.BOTH, expand=True)

    def _fill_ms_groups(self, event=None):
        groups = db_query("SELECT tg_id, title, access_hash FROM groups ORDER BY participants DESC")
        self._ms_groups_cache = {f"{g['tg_id']}|{g['title']}": g for g in groups}
        self.ms_group['values'] = list(self._ms_groups_cache.keys())

    def _scrape_members(self):
        phone = self.ms_phone.get()
        sel = self.ms_group.get()
        if not phone or not sel:
            messagebox.showwarning("提示", "请选择账号和群组")
            return
        g = self._ms_groups_cache.get(sel, {})
        group_tg_id = g.get('tg_id')
        group_title = sel.split('|', 1)[-1] if '|' in sel else sel
        access_hash = g.get('access_hash', '')
        limit = int(self.ms_limit.get() or 5000)

        self.set_status(f"正在采集成员: {group_title}", "orange")
        self.progress.start()
        self.ms_label.config(text="采集中, 请查看日志...")

        def _do():
            members = tg.get_participants(phone, int(group_tg_id), access_hash, limit)
            if not members:
                self.root.after(0, lambda: [self.set_status("就绪", "gray"), self.progress.stop(), self.ms_label.config(text="获取失败")])
                return
            added = 0
            for m in members:
                if not db_one("SELECT id FROM members WHERE user_id=? AND source_group_id=? AND account_phone=?", (m['user_id'], int(group_tg_id), phone)):
                    db_exec("INSERT INTO members (user_id, username, first_name, last_name, phone, source_group_id, source_group_title, account_phone) VALUES (?,?,?,?,?,?,?,?)",
                            (m['user_id'], m['username'], m['first_name'], m['last_name'], m['phone'], int(group_tg_id), group_title, phone))
                    added += 1
            self.root.after(0, lambda: [
                self.ms_label.config(text=f"新增 {added}/{len(members)}"),
                self.set_status("就绪", "gray"),
                self.progress.stop(),
                self._load_members()
            ])
            self.log(f"[{phone}] 成员采集完成: {group_title}, 新增 {added}")

        threading.Thread(target=_do, daemon=True).start()

    def _load_members(self):
        for item in self.mb_tree.get_children():
            self.mb_tree.delete(item)
        search = self.mb_search.get().strip()
        sql = "SELECT * FROM members WHERE 1=1"
        params = []
        if search:
            sql += " AND (username LIKE ? OR first_name LIKE ? OR last_name LIKE ?)"
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
        sql += " ORDER BY created_at DESC LIMIT 5000"
        rows = db_query(sql, params)
        for i, m in enumerate(rows):
            self.mb_tree.insert('', 'end', iid=str(m['id']), values=(i+1, m['username'] or '-', m['first_name'] or '', m['last_name'] or '', m['source_group_title'] or m['source_group_id'], 'Yes' if m['invited_to_group'] else ''))
        self.mb_count.config(text=f"共{len(rows)}人")

    def _export_members(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path: return
        rows = db_query("SELECT user_id, username, first_name, last_name, phone, source_group_title FROM members ORDER BY created_at DESC")
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)
        self.log(f"已导出 {len(rows)} 个成员到 {path}")

    # ====================== Tab: 消息群发 ======================
    def _tab_send(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="消息群发")

        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(left, text="选择账号:").pack(anchor='w')
        self.sd_phone = ttk.Combobox(left, state='readonly', width=22)
        self.sd_phone.pack(pady=2)

        ttk.Label(left, text="模板:").pack(anchor='w', pady=(5,0))
        self.sd_template = ttk.Combobox(left, state='readonly', width=22)
        self.sd_template.pack(pady=2)
        self.sd_template.bind('<<ComboboxSelected>>', self._on_template_select)

        ttk.Label(left, text="间隔(秒):").pack(anchor='w', pady=(5,0))
        self.sd_delay = ttk.Entry(left, width=10)
        self.sd_delay.insert(0, '3')
        self.sd_delay.pack(pady=2)

        ttk.Label(left, text="消息内容 ({username} {first_name} 可用):").pack(anchor='w', pady=(5,0))
        self.sd_message = tk.Text(left, height=5, width=30, font=('Microsoft YaHei', 10))
        self.sd_message.pack(pady=2, fill=tk.X)

        # Loop controls
        loop_frame = ttk.LabelFrame(left, text="循环发送", padding=3)
        loop_frame.pack(fill=tk.X, pady=3)
        self.sd_loop_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(loop_frame, text="启用循环", variable=self.sd_loop_enabled).pack(anchor='w')
        row1 = ttk.Frame(loop_frame)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="轮数:").pack(side=tk.LEFT)
        self.sd_loop_count = ttk.Entry(row1, width=6)
        self.sd_loop_count.insert(0, '3')
        self.sd_loop_count.pack(side=tk.LEFT, padx=3)
        ttk.Label(row1, text="轮间隔(秒):").pack(side=tk.LEFT)
        self.sd_loop_delay = ttk.Entry(row1, width=6)
        self.sd_loop_delay.insert(0, '60')
        self.sd_loop_delay.pack(side=tk.LEFT, padx=3)

        ttk.Button(left, text="发送给已选成员", command=self._start_send).pack(pady=5)
        ttk.Label(left, text="点击列表中的行来选择成员").pack()

        self.sd_progress = ttk.Label(left, text="")
        self.sd_progress.pack(pady=3)

        # Right: member list for picking
        right = ttk.Frame(f)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        top = ttk.Frame(right)
        top.pack(fill=tk.X)
        ttk.Button(top, text="筛选已选成员用于发送", command=self._load_send_targets).pack(side=tk.LEFT)
        self.sd_count_label = ttk.Label(top, text="选中: 0")
        self.sd_count_label.pack(side=tk.RIGHT)

        cols = ('☐', '用户名', '昵称', '来源群')
        self.sd_tree = ttk.Treeview(right, columns=cols, show='headings', selectmode='extended')
        self.sd_tree.heading('☐', text='☐'); self.sd_tree.column('☐', width=30, anchor='center')
        self.sd_tree.heading('用户名', text='用户名'); self.sd_tree.column('用户名', width=150)
        self.sd_tree.heading('昵称', text='昵称'); self.sd_tree.column('昵称', width=180)
        self.sd_tree.heading('来源群', text='来源群'); self.sd_tree.column('来源群', width=150)
        self.sd_tree.pack(fill=tk.BOTH, expand=True)
        self.sd_tree.bind('<<TreeviewSelect>>', lambda e: self.sd_count_label.config(text=f"选中: {len(self.sd_tree.selection())}"))

    def _on_template_select(self, event=None):
        tid = self.sd_template.get()
        if not tid: return
        tpl = db_one("SELECT * FROM message_templates WHERE id=?", (tid,))
        if tpl:
            self.sd_message.delete('1.0', tk.END)
            self.sd_message.insert('1.0', tpl['content'])

    def _load_send_targets(self):
        for item in self.sd_tree.get_children():
            self.sd_tree.delete(item)
        members = db_query("SELECT id, username, first_name, last_name, source_group_title FROM members ORDER BY created_at DESC LIMIT 10000")
        self._sd_targets = {str(m['id']): m for m in members}
        for m in members:
            self.sd_tree.insert('', 'end', iid=str(m['id']), values=('', m['username'] or '-', f"{m['first_name'] or ''} {m['last_name'] or ''}", m['source_group_title'] or ''))

    def _start_send(self):
        phone = self.sd_phone.get()
        message = self.sd_message.get('1.0', tk.END).strip()
        delay = int(self.sd_delay.get() or 3)
        ids = [int(i) for i in self.sd_tree.selection()]

        if not phone:
            messagebox.showwarning("提示", "请选择账号")
            return
        if not message:
            messagebox.showwarning("提示", "消息不能为空")
            return
        if not ids:
            messagebox.showwarning("提示", "请选择目标成员")
            return

        targets = [self._sd_targets[str(i)] for i in ids if str(i) in self._sd_targets]
        if not targets:
            return

        loop_enabled = self.sd_loop_enabled.get()
        loop_count = int(self.sd_loop_count.get() or 1)
        loop_delay = int(self.sd_loop_delay.get() or 60)

        self.set_status("正在群发...", "orange")
        self.progress.start()

        def _do():
            total_success, total_fail = 0, 0
            for round_num in range(loop_count):
                if round_num > 0:
                    self.root.after(0, lambda r=round_num: [
                        self.sd_progress.config(text=f"等待 {loop_delay}s 后开始第 {r+1} 轮..."),
                        self.log(f"[{phone}] 第 {r} 轮完成, 等待 {loop_delay}s...")
                    ])
                    time.sleep(loop_delay)

                self.root.after(0, lambda r=round_num: self.log(f"[{phone}] === 第 {r+1}/{loop_count} 轮群发 ==="))
                success, fail, errors = tg.send_bulk_messages(phone, targets, message, delay,
                    progress_cb=lambda done, total, r=round_num: self.root.after(0,
                        lambda: self.sd_progress.config(text=f"第{r+1}轮: {done}/{total}")))

                total_success += success
                total_fail += fail

                if not loop_enabled:
                    break

                if loop_count > 1 and round_num == loop_count - 1:
                    break

            final_msg = f"完成: 成功{total_success}, 失败{total_fail}"
            if loop_count > 1:
                final_msg += f" (共{loop_count}轮)"
            self.root.after(0, lambda: [
                self.sd_progress.config(text=final_msg),
                self.set_status("就绪", "gray"),
                self.progress.stop(),
                self.log(f"[{phone}] 群发完成: 成功{total_success} 失败{total_fail} (共{loop_count}轮)")
            ])

        threading.Thread(target=_do, daemon=True).start()

    # ====================== Tab: 用户私信 ======================
    def _tab_dm(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="用户私信")

        ttk.Label(f, text="账号:").grid(row=0, column=0, sticky='e', padx=5, pady=4)
        self.dm_phone = ttk.Combobox(f, state='readonly', width=25)
        self.dm_phone.grid(row=0, column=1, sticky='w', padx=5)

        ttk.Label(f, text="目标:").grid(row=1, column=0, sticky='e', padx=5, pady=4)
        self.dm_target = ttk.Entry(f, width=28)
        self.dm_target.grid(row=1, column=1, sticky='w', padx=5)
        ttk.Label(f, text="@username 或 数字user_id", foreground="gray", font=('Microsoft YaHei', 8)).grid(row=1, column=2, sticky='w')

        ttk.Label(f, text="消息:").grid(row=2, column=0, sticky='ne', padx=5, pady=4)
        self.dm_message = tk.Text(f, height=6, width=35, font=('Microsoft YaHei', 10))
        self.dm_message.grid(row=2, column=1, columnspan=2, sticky='w', pady=4)

        ttk.Button(f, text="发送私信", command=self._send_dm).grid(row=3, column=1, sticky='w', padx=5, pady=10)
        self.dm_result = ttk.Label(f, text="")
        self.dm_result.grid(row=4, column=1, sticky='w', padx=5)

        # Quick pick from members
        right = ttk.LabelFrame(f, text="快速选择成员")
        right.grid(row=0, column=3, rowspan=5, sticky='nsew', padx=10, pady=5)
        f.columnconfigure(3, weight=1)
        f.rowconfigure(5, weight=1)

        ttk.Button(right, text="加载成员列表", command=self._dm_load_members).pack(padx=5, pady=5)
        cols = ('用户名', '昵称', '来源群')
        self.dm_tree = ttk.Treeview(right, columns=cols, show='headings', selectmode='browse', height=15)
        self.dm_tree.heading('用户名', text='用户名'); self.dm_tree.column('用户名', width=130)
        self.dm_tree.heading('昵称', text='昵称'); self.dm_tree.column('昵称', width=160)
        self.dm_tree.heading('来源群', text='来源群'); self.dm_tree.column('来源群', width=130)
        self.dm_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.dm_tree.bind('<Double-1>', lambda e: self._dm_pick())

    def _dm_load_members(self):
        for item in self.dm_tree.get_children():
            self.dm_tree.delete(item)
        self._dm_members = {}
        for m in db_query("SELECT id, username, first_name, last_name, source_group_title FROM members ORDER BY created_at DESC LIMIT 3000"):
            username = m['username'] or ''
            name = f"{m['first_name'] or ''} {m['last_name'] or ''}"
            self._dm_members[username] = m
            self.dm_tree.insert('', 'end', values=(username or '-', name.strip(), m['source_group_title'] or ''))

    def _dm_pick(self):
        sel = self.dm_tree.selection()
        if sel:
            vals = self.dm_tree.item(sel[0], 'values')
            if vals[0] != '-':
                self.dm_target.delete(0, tk.END)
                self.dm_target.insert(0, f"@{vals[0]}")

    def _send_dm(self):
        phone = self.dm_phone.get()
        target = self.dm_target.get().strip()
        message = self.dm_message.get('1.0', tk.END).strip()
        if not phone or not target or not message:
            messagebox.showwarning("提示", "请填写完整信息")
            return
        def _do():
            ok = tg.send_message(phone, target, message, is_username=not target.replace('@','').isdigit())
            self.root.after(0, lambda: self.dm_result.config(text="发送成功" if ok else "发送失败"))
        threading.Thread(target=_do, daemon=True).start()

    # ====================== Tab: 消息模板 ======================
    def _tab_templates(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="消息模板")

        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(left, text="模板名称:").pack(anchor='w')
        self.tp_name = ttk.Entry(left, width=22)
        self.tp_name.pack(pady=2)

        ttk.Label(left, text="模板内容 ({username}等变量可用):").pack(anchor='w', pady=(5,0))
        self.tp_content = tk.Text(left, height=6, width=30, font=('Microsoft YaHei', 10))
        self.tp_content.pack(pady=2, fill=tk.X)

        ttk.Button(left, text="添加/更新模板", command=self._tp_save).pack(pady=5)

        # Right: template list
        right = ttk.Frame(f)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        cols = ('ID', '名称', '内容预览')
        self.tp_tree = ttk.Treeview(right, columns=cols, show='headings', selectmode='browse')
        self.tp_tree.heading('ID', text='ID'); self.tp_tree.column('ID', width=40, anchor='center')
        self.tp_tree.heading('名称', text='名称'); self.tp_tree.column('名称', width=130)
        self.tp_tree.heading('内容预览', text='内容预览'); self.tp_tree.column('内容预览', width=300)
        self.tp_tree.pack(fill=tk.BOTH, expand=True)
        self.tp_tree.bind('<<TreeviewSelect>>', self._tp_select)

        btn = ttk.Frame(right)
        btn.pack(fill=tk.X, pady=5)
        ttk.Button(btn, text="删除", command=self._tp_delete).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="刷新", command=self._tp_load).pack(side=tk.LEFT, padx=3)

    def _tp_load(self):
        for item in self.tp_tree.get_children():
            self.tp_tree.delete(item)
        for t in db_query("SELECT * FROM message_templates ORDER BY created_at DESC"):
            self.tp_tree.insert('', 'end', iid=str(t['id']), values=(t['id'], t['name'], t['content'][:50]))

    def _tp_select(self, event):
        sel = self.tp_tree.selection()
        if not sel: return
        tpl = db_one("SELECT * FROM message_templates WHERE id=?", (int(sel[0]),))
        if tpl:
            self.tp_name.delete(0, tk.END); self.tp_name.insert(0, tpl['name'])
            self.tp_content.delete('1.0', tk.END); self.tp_content.insert('1.0', tpl['content'])

    def _tp_save(self):
        name = self.tp_name.get().strip()
        content = self.tp_content.get('1.0', tk.END).strip()
        if not name or not content:
            messagebox.showwarning("提示", "名称和内容不能为空")
            return
        existing = db_one("SELECT id FROM message_templates WHERE name=?", (name,))
        if existing:
            db_exec("UPDATE message_templates SET content=? WHERE id=?", (content, existing['id']))
        else:
            db_exec("INSERT INTO message_templates (name, content) VALUES (?,?)", (name, content))
        self._tp_load()
        self.log(f"模板 [{name}] 已保存")

    def _tp_delete(self):
        sel = self.tp_tree.selection()
        if not sel: return
        db_exec("DELETE FROM message_templates WHERE id=?", (int(sel[0]),))
        self._tp_load()

    # ====================== Tab: 自动回复 ======================
    def _tab_monitor(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="自动回复")

        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(left, text="关键词 (逗号分隔):").pack(anchor='w')
        self.mk_keyword = ttk.Entry(left, width=28)
        self.mk_keyword.pack(pady=2)

        ttk.Label(left, text="回复内容:").pack(anchor='w', pady=(5,0))
        self.mk_reply = tk.Text(left, height=4, width=30, font=('Microsoft YaHei', 10))
        self.mk_reply.pack(pady=2, fill=tk.X)

        ttk.Label(left, text="账号:").pack(anchor='w')
        self.mk_phone = ttk.Combobox(left, state='readonly', width=28)
        self.mk_phone.pack(pady=2)

        ttk.Label(left, text="监听群 (username, 逗号分隔, 空=全部):").pack(anchor='w')
        self.mk_groups = ttk.Entry(left, width=28)
        self.mk_groups.pack(pady=2)

        ttk.Button(left, text="添加关键词", command=self._add_keyword).pack(pady=8)

        # Right: keyword list
        right = ttk.Frame(f)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        cols = ('关键词', '回复', '账号', '监听群', '状态')
        self.mk_tree = ttk.Treeview(right, columns=cols, show='headings')
        self.mk_tree.heading('关键词', text='关键词'); self.mk_tree.column('关键词', width=130)
        self.mk_tree.heading('回复', text='回复'); self.mk_tree.column('回复', width=200)
        self.mk_tree.heading('账号', text='账号'); self.mk_tree.column('账号', width=120)
        self.mk_tree.heading('监听群', text='监听群'); self.mk_tree.column('监听群', width=120)
        self.mk_tree.heading('状态', text='状态'); self.mk_tree.column('状态', width=60, anchor='center')
        self.mk_tree.pack(fill=tk.BOTH, expand=True)

        btn = ttk.Frame(right)
        btn.pack(fill=tk.X, pady=5)
        ttk.Button(btn, text="启动监听", command=self._start_monitor).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="停止监听", command=self._stop_monitor).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="删除", command=self._delete_keyword).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn, text="刷新", command=self._load_keywords).pack(side=tk.LEFT, padx=3)

    def _load_keywords(self):
        for item in self.mk_tree.get_children():
            self.mk_tree.delete(item)
        for kw in db_query("SELECT * FROM monitor_keywords ORDER BY created_at DESC"):
            status = "运行中" if kw['id'] in self._monitor_tasks else ("已停止" if kw['enabled'] else "未启动")
            self.mk_tree.insert('', 'end', iid=str(kw['id']), values=(kw['keyword'], kw['reply_text'][:40], kw['account_phone'] or '-', kw['group_usernames'] or '-', status))

    def _add_keyword(self):
        data = {
            'keyword': self.mk_keyword.get().strip(),
            'reply_text': self.mk_reply.get('1.0', tk.END).strip(),
            'account_phone': self.mk_phone.get(),
            'group_usernames': self.mk_groups.get().strip(),
            'enabled': 1
        }
        if not data['keyword'] or not data['reply_text']:
            messagebox.showwarning("提示", "关键词和回复内容不能为空")
            return
        db_exec("INSERT INTO monitor_keywords (keyword, reply_text, account_phone, group_usernames) VALUES (?,?,?,?)",
                (data['keyword'], data['reply_text'], data['account_phone'], data['group_usernames']))
        self._load_keywords()

    def _start_monitor(self):
        sel = self.mk_tree.selection()
        if not sel: return
        kid = int(sel[0])
        if kid in self._monitor_tasks:
            messagebox.showinfo("提示", "该监听已在运行")
            return
        kw = db_one("SELECT * FROM monitor_keywords WHERE id=?", (kid,))
        if not kw: return
        keywords = [k.strip() for k in kw['keyword'].split(',') if k.strip()]
        groups = [g.strip() for g in kw['group_usernames'].split(',') if g.strip()] if kw['group_usernames'] else None
        stop_event = tg.listen_messages(kw['account_phone'], keywords, kw['reply_text'], groups)
        self._monitor_tasks[kid] = stop_event
        self.log(f"监听已启动: {keywords}")
        self._load_keywords()

    def _stop_monitor(self):
        sel = self.mk_tree.selection()
        if not sel: return
        kid = int(sel[0])
        task = self._monitor_tasks.pop(kid, None)
        if task:
            task.set()
            if hasattr(task, '_cleanup'):
                task._cleanup()
            self.log("监听已停止")
        db_exec("UPDATE monitor_keywords SET enabled=0 WHERE id=?", (kid,))
        self._load_keywords()

    def _delete_keyword(self):
        sel = self.mk_tree.selection()
        if not sel: return
        kid = int(sel[0])
        self._stop_monitor() if kid in self._monitor_tasks else None
        db_exec("DELETE FROM monitor_keywords WHERE id=?", (kid,))
        self._load_keywords()

    # ====================== Tab: 群组私信监听 ======================
    def _tab_group_listen(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="群组私信监听")

        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(left, text="监听账号:").pack(anchor='w')
        self.gl_phone = ttk.Combobox(left, state='readonly', width=22)
        self.gl_phone.pack(pady=2)

        ttk.Label(left, text="监听群组 (username, 逗号分隔):").pack(anchor='w', pady=(5,0))
        self.gl_groups = ttk.Entry(left, width=28)
        self.gl_groups.pack(pady=2)

        ttk.Label(left, text="触发关键词 (逗号分隔):").pack(anchor='w', pady=(5,0))
        self.gl_keywords = ttk.Entry(left, width=28)
        self.gl_keywords.pack(pady=2)

        ttk.Label(left, text="自动回复内容:").pack(anchor='w', pady=(5,0))
        self.gl_reply = tk.Text(left, height=4, width=30, font=('Microsoft YaHei', 10))
        self.gl_reply.pack(pady=2, fill=tk.X)

        ttk.Button(left, text="启动监听", command=self._gl_start).pack(pady=5)
        ttk.Button(left, text="停止监听", command=self._gl_stop).pack(pady=2)
        self.gl_status = ttk.Label(left, text="未启动", foreground="gray")
        self.gl_status.pack(pady=3)

        # Right: hit log
        right = ttk.Frame(f)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        ttk.Label(right, text="命中记录:").pack(anchor='w')
        self.gl_log = tk.Text(right, height=20, font=('Consolas', 10),
                               bg='#1e1e1e', fg='#d4d4d4', wrap=tk.WORD)
        self.gl_log.pack(fill=tk.BOTH, expand=True)
        ttk.Button(right, text="清空记录", command=lambda: self.gl_log.delete('1.0', tk.END)).pack(pady=3)

        self._gl_task = None

    def _gl_start(self):
        phone = self.gl_phone.get()
        if not phone:
            messagebox.showwarning("提示", "请选择账号")
            return
        groups = [g.strip() for g in self.gl_groups.get().split(',') if g.strip()] or None
        keywords = [k.strip() for k in self.gl_keywords.get().split(',') if k.strip()]
        reply = self.gl_reply.get('1.0', tk.END).strip()
        if not keywords or not reply:
            messagebox.showwarning("提示", "关键词和回复内容不能为空")
            return

        if self._gl_task:
            self._gl_stop()

        def _hit_callback(info):
            self.root.after(0, lambda: self.gl_log.insert(tk.END,
                f"[{info['time']}] {info['keyword']} @ {info['chat']} | {info['sender']}: {info['text']}\n"))
            self.root.after(0, lambda: self.gl_log.see(tk.END))

        self._gl_task = tg.listen_messages(phone, keywords, reply, groups, callback=_hit_callback)
        self.gl_status.config(text="运行中", foreground="green")
        self.log(f"[{phone}] 群组私信监听已启动: {keywords}")

    def _gl_stop(self):
        if self._gl_task:
            self._gl_task.set()
            if hasattr(self._gl_task, '_cleanup'):
                self._gl_task._cleanup()
            self._gl_task = None
        self.gl_status.config(text="已停止", foreground="gray")
        self.log("群组私信监听已停止")

    # ====================== Tab: 群组拉人 ======================
    def _tab_invite(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="群组拉人")

        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(left, text="使用账号:").pack(anchor='w')
        self.iv_phone = ttk.Combobox(left, state='readonly', width=22)
        self.iv_phone.pack(pady=2)

        ttk.Label(left, text="目标群:").pack(anchor='w', pady=(5,0))
        self.iv_group = ttk.Combobox(left, state='readonly', width=22)
        self.iv_group.pack(pady=2)

        ttk.Label(left, text="筛选来源群:").pack(anchor='w', pady=(5,0))
        self.iv_filter = ttk.Combobox(left, state='readonly', width=22)
        self.iv_filter.pack(pady=2)
        self.iv_filter.bind('<<ComboboxSelected>>', lambda e: self._load_invite_members())

        self.iv_count = ttk.Label(left, text="选中: 0 人")
        self.iv_count.pack(pady=5)

        ttk.Button(left, text="开始拉人", command=self._start_invite).pack(pady=5)
        self.iv_label = ttk.Label(left, text="")
        self.iv_label.pack()

        # Right: member list
        right = ttk.Frame(f)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        ttk.Button(right, text="刷新列表", command=self._load_invite_members).pack(anchor='w', pady=(0,3))

        cols = ('☐', '用户名', '昵称', '来源群', '可邀请')
        self.iv_tree = ttk.Treeview(right, columns=cols, show='headings', selectmode='extended')
        self.iv_tree.heading('☐', text='☐'); self.iv_tree.column('☐', width=30, anchor='center')
        self.iv_tree.heading('用户名', text='用户名'); self.iv_tree.column('用户名', width=150)
        self.iv_tree.heading('昵称', text='昵称'); self.iv_tree.column('昵称', width=180)
        self.iv_tree.heading('来源群', text='来源群'); self.iv_tree.column('来源群', width=150)
        self.iv_tree.heading('可邀请', text='可邀请'); self.iv_tree.column('可邀请', width=60, anchor='center')
        self.iv_tree.pack(fill=tk.BOTH, expand=True)
        self.iv_tree.bind('<<TreeviewSelect>>', lambda e: self.iv_count.config(text=f"选中: {len(self.iv_tree.selection())} 人"))

    def _load_invite_members(self):
        for item in self.iv_tree.get_children():
            self.iv_tree.delete(item)
        filter_gid = self.iv_filter.get().split('|')[0] if self.iv_filter.get() else ''
        sql = "SELECT * FROM members WHERE 1=1"
        params = []
        if filter_gid and filter_gid.isdigit():
            sql += " AND source_group_id=?"
            params.append(int(filter_gid))
        sql += " ORDER BY created_at DESC LIMIT 5000"
        self._iv_targets = {}
        for m in db_query(sql, params):
            iid = str(m['id'])
            self._iv_targets[iid] = m
            self.iv_tree.insert('', 'end', iid=iid, values=('', m['username'] or '-', f"{m['first_name'] or ''} {m['last_name'] or ''}", m['source_group_title'] or '', 'Yes' if m['can_invite'] else 'No'))

    def _start_invite(self):
        phone = self.iv_phone.get()
        group_sel = self.iv_group.get()
        if not phone or not group_sel:
            messagebox.showwarning("提示", "请选择账号和目标群")
            return
        group_tg_id = int(group_sel.split('|')[0]) if '|' in group_sel else None
        if not group_tg_id:
            return
        ids = [int(i) for i in self.iv_tree.selection()]
        if not ids: return
        user_ids = [self._iv_targets[str(i)]['user_id'] for i in ids if str(i) in self._iv_targets]

        if not messagebox.askyesno("确认", f"邀请 {len(user_ids)} 人到群组?"): return

        self.set_status("正在拉人...", "orange")
        self.progress.start()

        def _do():
            success, fail = tg.invite_to_group(phone, group_tg_id, user_ids)
            self.root.after(0, lambda: [
                self.iv_label.config(text=f"成功{success}, 失败{fail}"),
                self.set_status("就绪", "gray"),
                self.progress.stop()
            ])
            self.log(f"[{phone}] 拉人完成: 成功{success} 失败{fail}")

        threading.Thread(target=_do, daemon=True).start()

    # ====================== Tab: 过验证 ======================
    def _tab_verify(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="过验证")

        left = ttk.Frame(f)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=5)

        ttk.Label(left, text="Bot用户名:").pack(anchor='w')
        self.vf_bot = ttk.Entry(left, width=22)
        self.vf_bot.pack(pady=2)

        ttk.Label(left, text="匹配模式 (可选):").pack(anchor='w', pady=(5,0))
        self.vf_pattern = ttk.Entry(left, width=22)
        self.vf_pattern.pack(pady=2)

        ttk.Label(left, text="答案:").pack(anchor='w', pady=(5,0))
        self.vf_answer = ttk.Entry(left, width=22)
        self.vf_answer.pack(pady=2)

        ttk.Button(left, text="添加配置", command=self._add_verify_config).pack(pady=8)

        ttk.Separator(left, orient='horizontal').pack(fill=tk.X, pady=5)

        ttk.Label(left, text="执行验证:").pack(anchor='w')
        ttk.Label(left, text="账号:").pack(anchor='w')
        self.vf_phone = ttk.Combobox(left, state='readonly', width=22)
        self.vf_phone.pack(pady=2)

        ttk.Label(left, text="配置:").pack(anchor='w')
        self.vf_config = ttk.Combobox(left, state='readonly', width=22)
        self.vf_config.pack(pady=2)

        ttk.Button(left, text="执行验证", command=self._run_verify).pack(pady=8)
        self.vf_result = ttk.Label(left, text="")
        self.vf_result.pack()

        # Right: config list
        right = ttk.Frame(f)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        cols = ('Bot', '匹配', '答案')
        self.vf_tree = ttk.Treeview(right, columns=cols, show='headings')
        self.vf_tree.heading('Bot', text='Bot'); self.vf_tree.column('Bot', width=180)
        self.vf_tree.heading('匹配', text='匹配'); self.vf_tree.column('匹配', width=180)
        self.vf_tree.heading('答案', text='答案'); self.vf_tree.column('答案', width=120)
        self.vf_tree.pack(fill=tk.BOTH, expand=True)

        ttk.Button(right, text="删除选中", command=self._delete_verify_config).pack(pady=3)
        ttk.Button(right, text="刷新", command=self._load_verify_configs).pack()

    def _load_verify_configs(self):
        for item in self.vf_tree.get_children():
            self.vf_tree.delete(item)
        cfgs = db_query("SELECT * FROM verify_configs ORDER BY created_at DESC")
        for c in cfgs:
            self.vf_tree.insert('', 'end', iid=str(c['id']), values=(c['bot_username'], c['question_pattern'] or '-', c['answer']))
        self.vf_config['values'] = [f"{c['id']}|{c['bot_username']} -> {c['answer']}" for c in cfgs]

    def _add_verify_config(self):
        data = {
            'bot_username': self.vf_bot.get().strip(),
            'question_pattern': self.vf_pattern.get().strip(),
            'answer': self.vf_answer.get().strip()
        }
        if not data['bot_username'] or not data['answer']:
            messagebox.showwarning("提示", "Bot用户名和答案不能为空")
            return
        db_exec("INSERT INTO verify_configs (bot_username, question_pattern, answer) VALUES (?,?,?)",
                (data['bot_username'], data['question_pattern'], data['answer']))
        self._load_verify_configs()

    def _delete_verify_config(self):
        sel = self.vf_tree.selection()
        if not sel: return
        db_exec("DELETE FROM verify_configs WHERE id=?", (int(sel[0]),))
        self._load_verify_configs()

    def _run_verify(self):
        phone = self.vf_phone.get()
        sel = self.vf_config.get()
        if not phone or not sel:
            messagebox.showwarning("提示", "请选择账号和配置")
            return
        cid = int(sel.split('|')[0])
        cfg = db_one("SELECT * FROM verify_configs WHERE id=?", (cid,))
        if not cfg: return
        self.vf_result.config(text="执行中...")
        def _do():
            ok = tg.check_verify_bot(phone, cfg['bot_username'], cfg['answer'])
            self.root.after(0, lambda: self.vf_result.config(text="验证完成" if ok else "验证失败"))
        threading.Thread(target=_do, daemon=True).start()

    # ====================== Tab: 账号设置 ======================
    def _tab_profile(self):
        f = ttk.Frame(self.nb, padding=8)
        self.nb.add(f, text="账号设置")

        ttk.Label(f, text="选择账号:").grid(row=0, column=0, sticky='e', padx=5, pady=4)
        self.pf_phone = ttk.Combobox(f, state='readonly', width=22)
        self.pf_phone.grid(row=0, column=1, sticky='w', padx=5)

        fields = [
            ('First Name', 'pf_fn'),
            ('Last Name', 'pf_ln'),
            ('Bio', 'pf_bio'),
            ('Username', 'pf_uname'),
            ('头像路径', 'pf_avatar'),
        ]
        for i, (label, attr) in enumerate(fields):
            ttk.Label(f, text=f"{label}:").grid(row=i+1, column=0, sticky='e', padx=5, pady=4)
            e = ttk.Entry(f, width=30)
            e.grid(row=i+1, column=1, sticky='w', padx=5)
            setattr(self, attr, e)

        ttk.Button(f, text="更新资料", command=self._update_profile).grid(row=6, column=1, sticky='w', padx=5, pady=10)
        self.pf_result = ttk.Label(f, text="")
        self.pf_result.grid(row=7, column=1, sticky='w', padx=5)

    def _update_profile(self):
        phone = self.pf_phone.get()
        if not phone:
            messagebox.showwarning("提示", "请选择账号")
            return
        data = {
            'first_name': self.pf_fn.get().strip(),
            'last_name': self.pf_ln.get().strip(),
            'bio': self.pf_bio.get().strip(),
            'username': self.pf_uname.get().strip(),
            'avatar_path': self.pf_avatar.get().strip(),
        }
        data = {k: v for k, v in data.items() if v}
        if not data:
            return
        def _do():
            ok = tg.set_profile(phone, **data)
            self.root.after(0, lambda: self.pf_result.config(text="更新完成" if ok else "更新失败"))
        threading.Thread(target=_do, daemon=True).start()

    # ====================== Helpers ======================
    def _fill_combos(self):
        phones = [a['phone'] for a in db_query("SELECT phone FROM accounts")]
        for attr in ['gs_phone', 'ms_phone', 'sd_phone', 'mk_phone', 'iv_phone', 'vf_phone', 'pf_phone', 'dm_phone', 'gl_phone']:
            cb = getattr(self, attr, None)
            if cb:
                cb['values'] = phones
                if phones and not cb.get():
                    cb.set(phones[0])

        # Fill groups for invite target, member scrape filter
        groups = db_query("SELECT tg_id, title FROM groups ORDER BY participants DESC")
        if hasattr(self, 'iv_group'):
            self.iv_group['values'] = [f"{g['tg_id']}|{g['title']}" for g in groups]
        if hasattr(self, 'iv_filter'):
            self.iv_filter['values'] = [f"{g['tg_id']}|{g['title']}" for g in groups]

        # Fill ms_group (成员采集) — always show all groups
        if hasattr(self, 'ms_group'):
            self._fill_ms_groups()

        # Fill templates
        tpls = db_query("SELECT id, name FROM message_templates ORDER BY created_at DESC")
        if hasattr(self, 'sd_template'):
            self.sd_template['values'] = [f"{t['id']}|{t['name']}" for t in tpls]

        # Fill verify configs
        cfgs = db_query("SELECT id, bot_username, answer FROM verify_configs ORDER BY created_at DESC")
        if hasattr(self, 'vf_config'):
            self.vf_config['values'] = [f"{c['id']}|{c['bot_username']} -> {c['answer']}" for c in cfgs]

    def run(self):
        self._fill_combos()
        self._refresh_accounts()
        self._load_groups()
        self._fd_load()
        self._load_members()
        self._load_keywords()
        self._tp_load()
        self._load_verify_configs()
        self.root.mainloop()


# ====================== Entry ======================
if __name__ == '__main__':
    App().run()
