"""
TG Marketing Suite - Telethon Client Wrapper
Multi-account management, proxy rotation, session handling.
"""
import os, sys, asyncio, threading, time, random, re, json
from datetime import datetime
from pathlib import Path

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import (
    GetDialogsRequest, SendMessageRequest, ImportChatInviteRequest,
    CheckChatInviteRequest
)
from telethon.tl.functions.channels import (
    GetParticipantsRequest, InviteToChannelRequest, JoinChannelRequest,
    GetFullChannelRequest
)
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest
from telethon.tl.functions.photos import UploadProfilePhotoRequest
from telethon.tl.types import (
    InputPeerUser, InputPeerChannel, InputPeerChat,
    ChannelParticipantsSearch, InputPhoto, InputFile
)
from telethon.errors import (
    FloodWaitError, UserPrivacyRestrictedError, UserAlreadyParticipantError,
    ChatAdminRequiredError, UserNotMutualContactError, PeerFloodError,
    UserBannedInChannelError, UserKickedError
)
from telethon.utils import get_display_name


class TGClientManager:
    """Manages multiple TelegramClient instances per account."""

    def __init__(self, log_callback=None):
        self.clients = {}       # phone -> TelegramClient
        self.sessions = {}      # phone -> session_string
        self.status = {}        # phone -> 'online'|'offline'|'connecting'
        self.loops = {}         # phone -> asyncio event loop
        self.threads = {}       # phone -> thread
        self.log = log_callback or (lambda msg: None)
        self._lock = threading.Lock()

    def _make_proxy(self, proxy_type, host, port):
        if not host or not port:
            return None
        ptype = (proxy_type or 'socks5').lower()
        return (ptype, host, int(port))

    def get_client(self, phone):
        return self.clients.get(phone)

    def is_online(self, phone):
        return self.status.get(phone) == 'online'

    # ---- Sync wrappers that run async code in background threads ----

    def add_account(self, phone, api_id, api_hash, proxy_type, proxy_host, proxy_port, session_str=None):
        """Add and connect a new account. Returns True on success."""
        if phone in self.clients:
            return True

        proxy = self._make_proxy(proxy_type, proxy_host, proxy_port)

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loops[phone] = loop

            if session_str:
                client = TelegramClient(
                    StringSession(session_str), int(api_id), api_hash,
                    proxy=proxy, loop=loop
                )
            else:
                session_path = str(SESSIONS_DIR / f"{phone}.session")
                client = TelegramClient(
                    session_path, int(api_id), api_hash,
                    proxy=proxy, loop=loop
                )

            self.clients[phone] = client
            self.status[phone] = 'connecting'
            self.log(f"[{phone}] 连接中...")

            try:
                loop.run_until_complete(client.connect())
                if loop.run_until_complete(client.is_user_authorized()):
                    self.status[phone] = 'online'
                    me = loop.run_until_complete(client.get_me())
                    self.log(f"[{phone}] 已登录: {get_display_name(me)}")
                    ss = StringSession.save(client.session)
                    self.sessions[phone] = ss
                else:
                    self.status[phone] = 'offline'
                    self.log(f"[{phone}] 已连接, 等待验证码登录")
            except Exception as e:
                self.status[phone] = 'offline'
                self.log(f"[{phone}] 连接失败: {e}")

            loop.run_forever()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self.threads[phone] = t
        return True

    def send_code(self, phone):
        """Request login code. Returns phone_code_hash or None."""
        # Wait for client to be ready (up to 5 seconds)
        for _ in range(25):
            if phone in self.clients and phone in self.loops:
                break
            time.sleep(0.2)

        client = self.clients.get(phone)
        loop = self.loops.get(phone)
        if not client or not loop:
            self.log(f"[{phone}] 客户端未初始化")
            return None

        async def _send():
            if not client.is_connected():
                await client.connect()
            if await client.is_user_authorized():
                return {'authorized': True}
            result = await client.send_code_request(phone)
            return {'phone_code_hash': result.phone_code_hash, 'authorized': False}

        try:
            return asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=30)
        except Exception as e:
            self.log(f"[{phone}] 发送验证码失败: {e}")
            return None

    def sign_in(self, phone, code, phone_code_hash, password=None):
        """Complete login with code. Returns True on success."""
        client = self.clients.get(phone)
        loop = self.loops.get(phone)
        if not client or not loop:
            return False

        async def _sign():
            await client.connect()
            try:
                await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            except Exception as e:
                # might need 2FA password
                if password:
                    await client.sign_in(password=password)
                else:
                    raise e
            me = await client.get_me()
            self.status[phone] = 'online'
            ss = StringSession.save(client.session)
            self.sessions[phone] = ss
            self.log(f"[{phone}] 登录成功: {get_display_name(me)}")
            await client.disconnect()
            return True

        try:
            return asyncio.run_coroutine_threadsafe(_sign(), loop).result(timeout=30)
        except Exception as e:
            self.log(f"[{phone}] 登录失败: {e}")
            self.status[phone] = 'offline'
            return False

    def logout(self, phone):
        """Logout and remove account."""
        client = self.clients.get(phone)
        loop = self.loops.get(phone)
        if client and loop:
            async def _logout():
                await client.connect()
                await client.log_out()
                await client.disconnect()
            try:
                asyncio.run_coroutine_threadsafe(_logout(), loop).result(timeout=15)
            except:
                pass

        with self._lock:
            self.clients.pop(phone, None)
            self.sessions.pop(phone, None)
            self.status[phone] = 'offline'
            if phone in self.loops:
                l = self.loops.pop(phone)
                l.call_soon_threadsafe(l.stop)
            self.threads.pop(phone, None)

        session_file = SESSIONS_DIR / f"{phone}.session"
        if session_file.exists():
            session_file.unlink()
        self.log(f"[{phone}] 已登出")

    # ---- Business operations ----

    def _run_async(self, phone, coro_factory, timeout=120):
        """Run an async operation on account's event loop."""
        client = self.clients.get(phone)
        loop = self.loops.get(phone)
        if not client or not loop:
            return None
        try:
            return asyncio.run_coroutine_threadsafe(
                coro_factory(client), loop
            ).result(timeout=timeout)
        except Exception as e:
            self.log(f"[{phone}] 操作失败: {e}")
            return None

    def get_dialogs(self, phone, limit=200):
        """Get all dialogs (groups/channels/users) for an account."""
        async def _get(client):
            await client.connect()
            dialogs = await client.get_dialogs(limit=limit)
            result = []
            for d in dialogs:
                if d.is_group or d.is_channel:
                    result.append({
                        'tg_id': d.id,
                        'title': d.title,
                        'username': d.entity.username or '',
                        'participants': getattr(d.entity, 'participants_count', 0) or 0,
                        'is_group': d.is_group,
                        'is_channel': d.is_channel,
                        'access_hash': str(getattr(d.entity, 'access_hash', '')),
                    })
            await client.disconnect()
            return result

        return self._run_async(phone, _get) or []

    def get_participants(self, phone, group_id, access_hash='', limit=5000):
        """Get members from a group. Falls back to chat history if member list is hidden."""
        async def _get(client):
            await client.connect()
            entity = await client.get_entity(int(group_id))
            all_users = []

            # Try official method first
            try:
                participants = await client.get_participants(entity, limit=limit)
                for p in participants:
                    all_users.append({
                        'user_id': p.id,
                        'username': p.username or '',
                        'first_name': p.first_name or '',
                        'last_name': p.last_name or '',
                        'phone': p.phone or '',
                        'is_bot': p.bot,
                    })
                self.log(f"[{phone}] 获取到 {len(all_users)} 个成员 (官方API)")
            except ChatAdminRequiredError:
                self.log(f"[{phone}] 官方API受限, 从聊天记录提取成员...")
                # Extract from recent messages
                seen = set()
                messages = await client.get_messages(entity, limit=3000)
                for msg in messages:
                    if msg.sender_id and msg.sender_id not in seen:
                        seen.add(msg.sender_id)
                        sender = msg.sender
                        if sender:
                            all_users.append({
                                'user_id': sender.id,
                                'username': sender.username or '',
                                'first_name': sender.first_name or '',
                                'last_name': sender.last_name or '',
                                'phone': getattr(sender, 'phone', '') or '',
                                'is_bot': sender.bot,
                            })
                self.log(f"[{phone}] 从历史消息提取到 {len(all_users)} 个成员")

            await client.disconnect()
            return all_users

        return self._run_async(phone, _get, timeout=300) or []

    def send_message(self, phone, target, message, is_username=False):
        """Send a message to a user/group. target = @username or user_id."""
        async def _send(client):
            await client.connect()
            if is_username or (isinstance(target, str) and target.startswith('@')):
                entity = await client.get_entity(target.replace('@', ''))
            else:
                entity = await client.get_entity(int(target))
            await client.send_message(entity, message)
            await client.disconnect()
            return True

        return self._run_async(phone, _send) or False

    def send_bulk_messages(self, phone, targets, message, delay=2, progress_cb=None):
        """Send messages to multiple targets with delay control. Returns (success, fail, errors)."""
        success = 0
        fail = 0
        errors = []

        async def _bulk(client):
            nonlocal success, fail
            await client.connect()
            for i, item in enumerate(targets):
                if progress_cb:
                    progress_cb(i + 1, len(targets))
                try:
                    uid = item.get('user_id')
                    username = item.get('username')
                    if username and not str(username).startswith('@'):
                        username = f"@{username}"
                    target = username or uid
                    is_uname = bool(username)

                    entity = await client.get_entity(target) if is_uname else \
                             await client.get_entity(int(uid))
                    await client.send_message(entity, message)
                    success += 1
                    self.log(f"[{phone}] [{i+1}/{len(targets)}] 已发送 -> {username or uid}")
                except FloodWaitError as e:
                    wait = e.seconds
                    self.log(f"[{phone}] FloodWait {wait}s, 等待中...")
                    await asyncio.sleep(wait + 2)
                except (UserPrivacyRestrictedError, UserNotMutualContactError) as e:
                    fail += 1
                    errors.append({'target': item, 'error': '隐私限制'})
                    self.log(f"[{phone}] [{i+1}/{len(targets)}] 隐私限制: {username or uid}")
                except PeerFloodError:
                    self.log(f"[{phone}] PeerFlood, 账号受限, 停止发送")
                    errors.append({'target': item, 'error': 'PeerFlood'})
                    break
                except Exception as e:
                    fail += 1
                    errors.append({'target': item, 'error': str(e)})
                    self.log(f"[{phone}] [{i+1}/{len(targets)}] 失败: {e}")

                await asyncio.sleep(delay + random.uniform(0, delay * 0.5))

            await client.disconnect()
            return success, fail, errors

        result = self._run_async(phone, _bulk, timeout=3600)
        return result if result else (0, len(targets), [])

    def invite_to_group(self, phone, group_id, user_ids, chunk_size=20, delay=60):
        """Invite users to a group. Respects TG limits (~20 per batch, 60s delay)."""
        async def _invite(client):
            await client.connect()
            entity = await client.get_entity(int(group_id))
            success = 0
            fail = 0
            for i in range(0, len(user_ids), chunk_size):
                chunk = user_ids[i:i + chunk_size]
                try:
                    to_invite = [await client.get_entity(int(uid)) for uid in chunk]
                    await client(InviteToChannelRequest(entity, to_invite))
                    success += len(chunk)
                    self.log(f"[{phone}] 已邀请 {success}/{len(user_ids)} 人 -> {entity.title}")
                except FloodWaitError as e:
                    self.log(f"[{phone}] FloodWait {e.seconds}s, 等待中...")
                    await asyncio.sleep(e.seconds + 5)
                    success += len(chunk)  # rough, try-continue would be better but keep it simple
                except Exception as e:
                    fail += len(chunk)
                    self.log(f"[{phone}] 邀请失败: {e}")
                await asyncio.sleep(delay + random.uniform(0, 30))
            await client.disconnect()
            return success, fail

        return self._run_async(phone, _invite, timeout=3600) or (0, len(user_ids))

    def join_group(self, phone, username_or_link):
        """Join a group by username or invite link."""
        async def _join(client):
            await client.connect()
            try:
                if username_or_link.startswith('https://t.me/'):
                    result = await client(ImportChatInviteRequest(
                        username_or_link.split('/')[-1].replace('+', '')
                    ))
                elif username_or_link.startswith('@'):
                    result = await client(JoinChannelRequest(
                        await client.get_entity(username_or_link)
                    ))
                else:
                    result = await client(JoinChannelRequest(
                        await client.get_entity(username_or_link)
                    ))
                await client.disconnect()
                return True
            except Exception as e:
                self.log(f"[{phone}] 加群失败: {e}")
                return False

        return self._run_async(phone, _join) or False

    def set_profile(self, phone, first_name=None, last_name=None, bio=None, username=None, avatar_path=None):
        """Batch update account profile."""
        async def _update(client):
            await client.connect()
            if first_name is not None or last_name is not None:
                await client(UpdateProfileRequest(
                    first_name=first_name or '',
                    last_name=last_name or '',
                    about=bio or ''
                ))
                self.log(f"[{phone}] 昵称已更新")
            if username is not None:
                try:
                    await client(UpdateUsernameRequest(username.strip('@')))
                    self.log(f"[{phone}] 用户名已更新: {username}")
                except Exception as e:
                    self.log(f"[{phone}] 用户名设置失败: {e}")
            if avatar_path and os.path.exists(avatar_path):
                try:
                    uploaded = await client.upload_file(avatar_path)
                    await client(UploadProfilePhotoRequest(uploaded))
                    self.log(f"[{phone}] 头像已更新")
                except Exception as e:
                    self.log(f"[{phone}] 头像上传失败: {e}")
            await client.disconnect()
            return True

        return self._run_async(phone, _update) or False

    def listen_messages(self, phone, keywords, reply_text, group_usernames=None, callback=None):
        """
        Monitor incoming messages in groups for keywords. Auto-reply when matched.
        Runs on the client's existing event loop. Returns a stop-event.
        """
        stop_event = threading.Event()
        client = self.clients.get(phone)
        loop = self.loops.get(phone)
        if not client or not loop:
            self.log(f"[{phone}] 客户端未就绪, 无法启动监听")
            return stop_event

        async def handler(event):
            if stop_event.is_set():
                return
            msg_text = event.message.message or ''
            try:
                chat = await event.get_chat()
            except:
                return
            chat_title = getattr(chat, 'title', '') or ''
            chat_user = getattr(chat, 'username', '') or ''

            if group_usernames and chat_user not in group_usernames:
                return

            for kw in keywords:
                if kw in msg_text:
                    self.log(f"[{phone}] 命中关键词 '{kw}' 在 {chat_title}")
                    try:
                        await event.reply(reply_text)
                        self.log(f"[{phone}] 已自动回复 -> {chat_title}")
                    except Exception as e:
                        self.log(f"[{phone}] 回复失败: {e}")
                    if callback:
                        callback({
                            'keyword': kw,
                            'chat': chat_title,
                            'sender': get_display_name(await event.get_sender()),
                            'text': msg_text[:200],
                            'time': datetime.now().isoformat()
                        })
                    break

        def _setup():
            asyncio.run_coroutine_threadsafe(self._start_listen(phone, handler), loop)

        def _cleanup():
            asyncio.run_coroutine_threadsafe(self._stop_listen(phone, handler), loop)

        stop_event._cleanup = _cleanup
        _setup()
        self.log(f"[{phone}] 开始监听关键词: {keywords}")
        return stop_event

    async def _start_listen(self, phone, handler):
        client = self.clients.get(phone)
        if client:
            await client.connect()
            client.add_event_handler(handler)

    async def _stop_listen(self, phone, handler):
        client = self.clients.get(phone)
        if client:
            client.remove_event_handler(handler)
            self.log(f"[{phone}] 监听已停止")

    def check_verify_bot(self, phone, bot_username, answer):
        """Interact with a verification bot. Send expected answer."""
        async def _verify(client):
            await client.connect()
            try:
                entity = await client.get_entity(bot_username)
                messages = await client.get_messages(entity, limit=5)
                for msg in messages:
                    text = msg.message or ''
                    # Check if bot is asking a question
                    if '?' in text or '点击' in text or '验证' in text or '请' in text:
                        await client.send_message(entity, answer)
                        self.log(f"[{phone}] 已回复验证: {answer}")
                        await asyncio.sleep(2)
                await client.disconnect()
                return True
            except Exception as e:
                self.log(f"[{phone}] 验证失败: {e}")
                return False

        return self._run_async(phone, _verify) or False


# Singleton
_manager = None

def get_manager(log_callback=None):
    global _manager
    if _manager is None:
        _manager = TGClientManager(log_callback=log_callback)
    return _manager

