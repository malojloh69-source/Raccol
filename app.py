"""Private Telegram announcement panel with isolated user sessions."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any

from aiohttp import web
from telethon import TelegramClient, errors
from telethon.sessions import StringSession


PAGE_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="csrf-token" content="__CSRF_TOKEN__">
  <title>Панель рассылки</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="/static/app.js" defer></script>
</head>
<body>
  <main class="shell">
    <header>
      <div class="mark">↗</div>
      <div><h1>Рассылка по чатам</h1><p>Одна отправка по выбранным группам и каналам</p></div>
      <span class="badge" id="connection">Проверка…</span>
      <button id="logout" type="button" class="secondary" hidden>Выйти</button>
    </header>
    <p class="notice" id="notice" role="status" hidden></p>

    <section class="card" id="login-panel" hidden>
      <div class="section-heading"><span class="step">01</span><div><h2>Подключить аккаунт</h2><p>Номер и код используются только для входа. Код и пароль не сохраняются.</p></div></div>
      <form id="phone-form" class="row">
        <label>Номер телефона<input id="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="+79991234567" required></label>
        <button type="submit">Получить код</button>
      </form>
      <form id="code-form" class="row" hidden>
        <label>Код из Telegram<input id="code" type="text" inputmode="numeric" autocomplete="one-time-code" required></label>
        <button type="submit">Войти</button>
      </form>
      <form id="password-form" class="row" hidden>
        <label>Пароль двухэтапной проверки<input id="two-factor" type="password" autocomplete="current-password" required></label>
        <button type="submit">Подтвердить</button>
      </form>
    </section>

    <section id="workspace" hidden>
      <div class="grid">
        <section class="card">
          <div class="section-heading"><span class="step">02</span><div><h2>Выбрать чаты</h2><p>Личные переписки не включены. Отправка доступна только в отмеченные чаты.</p></div></div>
          <div class="toolbar"><input id="search" type="search" placeholder="Поиск по названию" aria-label="Поиск по названию"><button id="refresh" type="button" class="secondary">Обновить</button></div>
          <div class="chats" id="chats" aria-live="polite"></div>
          <div class="selection"><span id="selected-count">Выбрано: 0</span><span>До 50 чатов</span></div>
        </section>

        <section class="card compose">
          <div class="section-heading"><span class="step">03</span><div><h2>Настроить отправку</h2><p>Каждый выбранный чат получит одно сообщение.</p></div></div>
          <label class="field">Текст сообщения<textarea id="message" rows="8" maxlength="4000" placeholder="Напишите сообщение для выбранных чатов"></textarea></label>
          <label class="field interval">Интервал между чатами, секунд<input id="interval" type="number" min="30" max="3600" step="1" value="60"></label>
          <label class="confirm"><input id="permission" type="checkbox"><span>У меня есть право отправлять сообщение в выбранные чаты.</span></label>
          <button id="start" type="button" class="primary">Начать отправку</button>
          <p class="hint">Telegram может ограничить отправку. При таком ответе процесс остановится.</p>
        </section>
      </div>
      <section class="card progress">
        <div class="progress-title"><div><h2>Ход отправки</h2><p id="summary">Ожидание запуска</p></div><button id="stop" type="button" class="secondary" hidden>Остановить</button></div>
        <div class="bar"><div id="bar-fill"></div></div>
        <ul id="events" aria-live="polite"></ul>
      </section>
    </section>
    <footer>У каждого пользователя отдельные чаты и отправка · Сессия хранится на сервере, ключ доступа — в браузере</footer>
  </main>
</body>
</html>
"""

PAGE_CSS = r"""
:root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; background:#09111f; color:#edf4ff }
* { box-sizing:border-box }
body { margin:0; min-height:100vh; background:radial-gradient(ellipse at 82% 1%, #16365b 0, transparent 38%), #09111f }
.shell { max-width:1150px; padding:36px 22px 60px; margin:auto }
header { display:flex; align-items:center; gap:17px; margin-bottom:34px }
.mark { width:52px; height:52px; display:grid; place-items:center; background:#2478e9; border-radius:16px; font-size:28px; font-weight:700; box-shadow:0 8px 32px #2478e945 }
h1,h2,p { margin:0 } h1 { font-size:clamp(22px,3vw,30px); letter-spacing:-.04em } h2 { font-size:19px; letter-spacing:-.025em }
p { color:#9badc6; line-height:1.5 } header p { margin-top:4px }
.badge { margin-left:auto; white-space:nowrap; border:1px solid #30435e; border-radius:100px; padding:8px 12px; font-size:12px; color:#c8d8f0 }
#logout { font-size:12px; padding:8px 12px }
.card { border:1px solid #273a55; border-radius:20px; padding:26px; background:#112035dd; box-shadow:0 12px 30px #02081428 }
.notice { margin-bottom:18px; padding:12px 16px; border-radius:12px; background:#623135; color:#fff0f0 }
.notice.ok { background:#17443d; color:#d2f6eb }
.section-heading { display:flex; align-items:flex-start; gap:14px; margin-bottom:24px }
.section-heading p { margin-top:5px; font-size:13px }
.step { font-size:12px; font-weight:700; color:#8fc4ff; background:#1d4671; padding:7px 8px; border-radius:8px }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:16px }
label { display:block; font-size:13px; color:#c6d6ec; font-weight:600 }
input:not([type=checkbox]),textarea { width:100%; display:block; margin-top:8px; border:1px solid #38516f; color:#edf4ff; outline:0; background:#0c182a; border-radius:11px; padding:12px 14px; font:inherit }
textarea { resize:vertical; min-height:160px; line-height:1.5 }
input:focus,textarea:focus { border-color:#559ef7; box-shadow:0 0 0 3px #2584ee35 }
button { cursor:pointer; background:#287ee7; border:1px solid #438eec; color:#fff; border-radius:11px; font:inherit; font-size:14px; font-weight:700; padding:12px 17px; white-space:nowrap }
button:hover { background:#3b91f7 } button:disabled { cursor:wait; opacity:.55 }
.secondary { background:#172b45; border-color:#38516f } .secondary:hover { background:#244363 }
.row,.toolbar { display:flex; align-items:end; gap:12px } .row { margin-top:14px } .row label { flex:1 }
.toolbar { margin-bottom:15px; align-items:center } .toolbar input { margin:0; flex:1; min-width:0 }
.chats { max-height:375px; overflow:auto; border:1px solid #263c56; border-radius:12px; background:#0c192b }
.chat { display:flex; gap:12px; align-items:center; padding:12px 15px; border-bottom:1px solid #21354e; cursor:pointer; font-size:14px; color:#ebf3ff; min-width:0 }
.chat:last-child { border-bottom:0 } .chat:hover { background:#172c46 } .chat input { accent-color:#388ff5; width:17px; height:17px; flex:none }
.chat span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap } .chat small { margin-left:auto; color:#8faac9; font-size:11px }
.empty { padding:24px; text-align:center; font-size:13px }
.selection { display:flex; justify-content:space-between; margin-top:12px; color:#9badc6; font-size:12px }
.field { margin-bottom:17px } .interval { max-width:260px }
.confirm { display:flex; align-items:flex-start; gap:10px; font-weight:400; margin:14px 0 20px; line-height:1.4 }
.confirm input { accent-color:#388ff5; margin:2px 0 0; flex:none }
.primary { width:100% } .hint { font-size:12px; margin-top:12px }
.progress { margin-top:16px } .progress-title { display:flex; align-items:center; justify-content:space-between; gap:10px } .progress-title p { font-size:13px; margin-top:4px }
.bar { height:7px; border-radius:20px; background:#233953; margin-top:19px; overflow:hidden } #bar-fill { width:0; height:100%; background:#409aff; border-radius:inherit; transition:width .3s }
#events { list-style:none; margin:16px 0 0; padding:0; max-height:230px; overflow:auto } #events:empty { display:none }
#events li { border-top:1px solid #273a55; padding:10px 0; display:flex; justify-content:space-between; gap:20px; color:#b9cae0; font-size:12px }
#events li strong { color:#e5f0fe; font-weight:500 }
footer { text-align:center; margin-top:26px; color:#7489a5; font-size:12px }
[hidden] { display:none !important }
@media(max-width:740px) { .shell { padding:22px 13px 36px } header { flex-wrap:wrap; margin-bottom:22px } .badge { margin-left:0 } .grid { grid-template-columns:1fr } .card { padding:19px } .row { align-items:stretch; flex-direction:column } .row button { width:100% } }
"""

PAGE_JS = r"""
let csrf = document.querySelector('meta[name="csrf-token"]').content;
const $ = id => document.getElementById(id);
const selected = new Set();
let chats = [];
let authorized = false;
let busy = false;

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: JSON.stringify(body)
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw Error(result.error || 'Ошибка запроса');
  return result;
}

function notice(text, success = false) {
  $('notice').textContent = text;
  $('notice').classList.toggle('ok', success);
  $('notice').hidden = !text;
}

async function action(fn, successMessage = '') {
  if (busy) return;
  busy = true;
  try { const result = await fn(); if (result !== false) notice(successMessage, !!successMessage); }
  catch (error) { notice(error.message || 'Ошибка соединения'); }
  finally { busy = false; }
}

function renderChats() {
  const container = $('chats');
  container.replaceChildren();
  const query = $('search').value.trim().toLocaleLowerCase();
  const filtered = chats.filter(chat => chat.title.toLocaleLowerCase().includes(query));
  if (!filtered.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = chats.length ? 'Ничего не найдено' : 'Доступных групп и каналов нет';
    container.append(empty);
  }
  for (const chat of filtered) {
    const row = document.createElement('label');
    row.className = 'chat';
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = selected.has(chat.id);
    box.addEventListener('change', () => {
      if (box.checked && selected.size >= 50) { box.checked = false; notice('Можно выбрать до 50 чатов.'); return; }
      if (box.checked) selected.add(chat.id); else selected.delete(chat.id);
      $('selected-count').textContent = `Выбрано: ${selected.size}`;
    });
    const title = document.createElement('span');
    title.textContent = chat.title;
    const type = document.createElement('small');
    type.textContent = chat.type;
    row.append(box, title, type);
    container.append(row);
  }
  $('selected-count').textContent = `Выбрано: ${selected.size}`;
}

async function loadChats() {
  const result = await api('/api/chats');
  chats = result.chats;
  const valid = new Set(chats.map(chat => chat.id));
  for (const id of selected) if (!valid.has(id)) selected.delete(id);
  renderChats();
}

function renderJob(job) {
  const active = job.state === 'running';
  $('stop').hidden = !active;
  $('start').disabled = active;
  $('refresh').disabled = active;
  const labels = {idle: 'Ожидание запуска', running: 'Отправка идёт', finished: 'Отправка завершена',
    stopped: 'Остановлено', limited: 'Остановлено из-за ограничения Telegram'};
  $('summary').textContent = `${labels[job.state] || job.state} · Отправлено: ${job.sent}/${job.total} · Ошибок: ${job.failed}`;
  const processed = job.sent + job.failed;
  $('bar-fill').style.width = `${job.total ? 100 * processed / job.total : 0}%`;
  const list = $('events');
  list.replaceChildren();
  for (const event of job.events) {
    const item = document.createElement('li');
    const chat = chats.find(value => value.id === event.chat_id);
    const name = document.createElement('strong');
    name.textContent = chat ? chat.title : event.chat_id;
    const result = document.createElement('span');
    result.textContent = event.result;
    item.append(name, result);
    list.append(item);
  }
}

async function refreshStatus() {
  const state = await api('/api/status');
  $('connection').textContent = state.authorized ? (state.account_name || 'Аккаунт подключён') : 'Требуется вход';
  $('logout').hidden = !state.authorized;
  $('login-panel').hidden = state.authorized;
  $('workspace').hidden = !state.authorized;
  $('code-form').hidden = !state.code_requested || state.password_needed;
  $('password-form').hidden = !state.password_needed;
  $('phone-form').hidden = !!state.code_requested;
  renderJob(state.job);
  if (state.authorized && !authorized) {
    authorized = true;
    await loadChats();
  }
  authorized = state.authorized;
}

$('phone-form').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => { await api('/api/code', {phone: $('phone').value.trim()}); await refreshStatus(); }, 'Код запрошен в Telegram.');
});
$('code-form').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    const result = await api('/api/verify', {code: $('code').value.trim()});
    csrf = result.csrf_token;
    $('code').value = '';
    await refreshStatus();
  }, 'Код принят.');
});
$('password-form').addEventListener('submit', event => {
  event.preventDefault();
  action(async () => {
    const result = await api('/api/password', {password: $('two-factor').value});
    csrf = result.csrf_token;
    $('two-factor').value = '';
    await refreshStatus();
  }, 'Аккаунт подключён.');
});
$('logout').addEventListener('click', () => action(async () => {
  if (!$('stop').hidden && !window.confirm('Остановить отправку и выйти из аккаунта?')) return false;
  const result = await api('/api/logout', {});
  csrf = result.csrf_token;
  selected.clear(); chats = []; renderChats();
  $('message').value = '';
  $('permission').checked = false;
  await refreshStatus();
}, 'Вы вышли из аккаунта.'));
$('search').addEventListener('input', renderChats);
$('refresh').addEventListener('click', () => action(loadChats, 'Список чатов обновлён.'));
$('start').addEventListener('click', () => action(async () => {
  const message = $('message').value;
  const interval = Number($('interval').value);
  if (!selected.size || !message.trim()) throw Error('Выберите чаты и введите текст.');
  if (!$('permission').checked) throw Error('Подтвердите право отправлять сообщения.');
  if (!Number.isInteger(interval) || interval < 30 || interval > 3600) throw Error('Интервал: от 30 до 3600 секунд.');
  if (!window.confirm(`Отправить одно сообщение в ${selected.size} чат(ов) с интервалом ${interval} с?`)) return false;
  await api('/api/start', {chat_ids: [...selected], message, interval, permission_confirmed: true});
  await refreshStatus();
}, 'Отправка запущена.'));
$('stop').addEventListener('click', () => action(async () => {
  await api('/api/stop', {});
  await refreshStatus();
}, 'Остановка запрошена.'));

refreshStatus().catch(error => notice(error.message));
setInterval(() => refreshStatus().catch(error => notice(error.message)), 2500);
"""

ROOT = Path(__file__).resolve().parent
SESSION_DB = ROOT / "sessions.sqlite3"
PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")
MAX_CHATS = 50
MIN_INTERVAL = 30
MAX_INTERVAL = 3600
MAX_MESSAGE_LENGTH = 4000
COOKIE_NAME = "sender_access"
COOKIE_TTL = 30 * 24 * 60 * 60
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")


class InputError(Exception):
    pass


class Panel:
    def __init__(self, client: Any):
        self.client = client
        self.account_name = ""
        self.phone: str | None = None
        self.phone_code_hash: str | None = None
        self.password_needed = False
        self.dialogs: dict[str, Any] = {}
        self.auth_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self.stop_event = asyncio.Event()
        self.job: dict[str, Any] = self._empty_job()
        self.last_code_sent = 0.0

    @staticmethod
    def _empty_job() -> dict[str, Any]:
        return {"state": "idle", "total": 0, "sent": 0, "failed": 0,
                "current": "", "events": []}

    async def authorized(self) -> bool:
        return bool(await self.client.is_user_authorized())

    async def send_code(self, phone: str) -> None:
        if not isinstance(phone, str) or not PHONE_RE.fullmatch(phone):
            raise InputError("Введите номер в международном формате: +79991234567.")
        async with self.auth_lock:
            if await self.authorized():
                raise InputError("Аккаунт уже подключён.")
            if time.monotonic() - self.last_code_sent < 60:
                raise InputError("Подождите минуту перед повторным запросом кода.")
            sent = await self.client.send_code_request(phone)
            self.last_code_sent = time.monotonic()
            self.phone = phone
            self.phone_code_hash = sent.phone_code_hash
            self.password_needed = False

    async def sign_in_code(self, code: str) -> bool:
        if not self.phone or not self.phone_code_hash:
            raise InputError("Сначала запросите код.")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9\s-]{3,12}", code):
            raise InputError("Проверьте формат кода.")
        async with self.auth_lock:
            try:
                await self.client.sign_in(
                    phone=self.phone,
                    code=re.sub(r"\D", "", code),
                    phone_code_hash=self.phone_code_hash,
                )
            except errors.SessionPasswordNeededError:
                self.password_needed = True
                return False
            self._clear_login()
            return True

    async def sign_in_password(self, password: str) -> None:
        if not self.password_needed or not self.phone:
            raise InputError("Двухэтапный пароль сейчас не запрошен.")
        if not isinstance(password, str) or not password:
            raise InputError("Введите пароль двухэтапной проверки.")
        async with self.auth_lock:
            await self.client.sign_in(password=password)
            self._clear_login()

    def _clear_login(self) -> None:
        self.phone = None
        self.phone_code_hash = None
        self.password_needed = False

    async def refresh_dialogs(self) -> list[dict[str, str]]:
        if not await self.authorized():
            raise InputError("Сначала войдите в аккаунт.")
        if self.task and not self.task.done():
            raise InputError("Дождитесь завершения отправки, затем обновите список.")
        dialogs = await self.client.get_dialogs(limit=500)
        self.dialogs = {}
        visible = []
        for dialog in dialogs:
            if not (dialog.is_group or dialog.is_channel):
                continue
            entity = dialog.entity
            if getattr(entity, "left", False) or getattr(entity, "kicked", False):
                continue
            if (getattr(entity, "broadcast", False)
                    and not (getattr(entity, "creator", False)
                             or getattr(entity, "admin_rights", None))):
                continue
            key = str(dialog.id)
            self.dialogs[key] = dialog.input_entity
            visible.append({"id": key, "title": dialog.title or "Без названия",
                            "type": "Канал" if getattr(entity, "broadcast", False)
                            else "Группа"})
        return visible

    def start_job(self, chat_ids: Any, message: Any, interval: Any,
                  permission_confirmed: Any) -> None:
        if self.task and not self.task.done():
            raise InputError("Отправка уже идёт.")
        if permission_confirmed is not True:
            raise InputError("Подтвердите право отправлять сообщения в выбранные чаты.")
        if not isinstance(message, str) or not message.strip():
            raise InputError("Введите текст сообщения.")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise InputError(f"Текст должен быть не длиннее {MAX_MESSAGE_LENGTH} символов.")
        if isinstance(interval, bool) or not isinstance(interval, int) or not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            raise InputError(f"Интервал: от {MIN_INTERVAL} до {MAX_INTERVAL} секунд.")
        if (not isinstance(chat_ids, list) or not 1 <= len(chat_ids) <= MAX_CHATS
                or any(not isinstance(key, str) for key in chat_ids)
                or len(set(chat_ids)) != len(chat_ids)
                or any(key not in self.dialogs for key in chat_ids)):
            raise InputError(f"Выберите от 1 до {MAX_CHATS} чатов из списка.")

        # Copy the resolved destinations: a later UI refresh cannot change this run.
        targets = [(key, self.dialogs[key]) for key in chat_ids]
        self.stop_event = asyncio.Event()
        self.job = {"state": "running", "total": len(targets), "sent": 0,
                    "failed": 0, "current": "", "events": []}
        self.task = asyncio.create_task(self._run(targets, message, interval))

    async def _run(self, targets: list[tuple[str, Any]], message: str, interval: int) -> None:
        try:
            for index, (key, entity) in enumerate(targets):
                if self.stop_event.is_set():
                    self.job["state"] = "stopped"
                    break
                self.job["current"] = key
                try:
                    await self.client.send_message(entity, message, parse_mode=None,
                                                   link_preview=False)
                    self.job["sent"] += 1
                    self.job["events"].append({"chat_id": key, "result": "Отправлено"})
                except (errors.FloodWaitError, errors.SlowModeWaitError) as exc:
                    seconds = int(getattr(exc, "seconds", 0))
                    self.job["state"] = "limited"
                    self.job["events"].append({"chat_id": key,
                                               "result": f"Ограничение Telegram: {seconds} с. Отправка остановлена."})
                    break
                except errors.RPCError as exc:
                    self.job["failed"] += 1
                    self.job["events"].append({"chat_id": key,
                                               "result": f"Ошибка Telegram: {type(exc).__name__}"})
                except Exception:
                    logging.exception("Unexpected send error for chat %s", key)
                    self.job["failed"] += 1
                    self.job["events"].append({"chat_id": key, "result": "Ошибка отправки"})
                if index < len(targets) - 1 and not self.stop_event.is_set():
                    try:
                        await asyncio.wait_for(self.stop_event.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass
            if self.job["state"] == "running":
                self.job["state"] = "stopped" if self.stop_event.is_set() else "finished"
        except asyncio.CancelledError:
            self.job["state"] = "stopped"
            raise
        finally:
            self.job["current"] = ""

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.stop_event.set()

    async def cancel_job(self) -> None:
        self.stop()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        await self.cancel_job()
        await self.client.disconnect()


class SessionDatabase:
    """Stores all user authorization strings in one local SQLite file."""

    def __init__(self, path: Path):
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "token_hash TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        self.connection.commit()

    def get(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value, created_at FROM sessions WHERE token_hash = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if row[1] < time.time() - COOKIE_TTL:
            self.delete(key)
            return None
        return row[0]

    def put(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO sessions (token_hash, value, created_at) VALUES (?, ?, ?)",
                (key, value, time.time()),
            )

    def delete(self, key: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))

    def purge_expired(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM sessions WHERE created_at < ?",
                                    (time.time() - COOKIE_TTL,))

    def close(self) -> None:
        self.connection.close()


class AccountRegistry:
    """Keeps one Telegram client and one job per browser login."""

    def __init__(self, client_factory: Any, serialize: Any, database_path: Path):
        self.client_factory = client_factory
        self.serialize = serialize
        self.database = SessionDatabase(database_path)
        self.panels: dict[str, Panel] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def key(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def purge_expired(self) -> None:
        self.database.purge_expired()

    @staticmethod
    async def _name(panel: Panel) -> None:
        user = await panel.client.get_me()
        panel.account_name = (f"@{user.username}" if getattr(user, "username", None)
                              else getattr(user, "first_name", None) or "Мой аккаунт")

    async def get(self, token: str, *, create: bool = False) -> Panel | None:
        key = self.key(token)
        if key in self.panels:
            return self.panels[key]
        if not create and self.database.get(key) is None:
            return None
        async with self.lock:
            if key in self.panels:
                return self.panels[key]
            saved = self.database.get(key)
            if not create and saved is None:
                return None
            client = self.client_factory(saved)
            try:
                await client.connect()
                panel = Panel(client)
                if saved:
                    if not await panel.authorized():
                        await client.disconnect()
                        self.database.delete(key)
                        if not create:
                            return None
                        client = self.client_factory(None)
                        await client.connect()
                        panel = Panel(client)
                    else:
                        await self._name(panel)
            except Exception:
                await client.disconnect()
                raise
            self.panels[key] = panel
            return panel

    async def promote(self, token: str) -> str:
        """Rotate the pending cookie after authorization and persist its session."""
        async with self.lock:
            old_key = self.key(token)
            panel = self.panels.get(old_key)
            if panel is None or not await panel.authorized():
                raise InputError("Сначала войдите в аккаунт.")
            await self._name(panel)
            session = self.serialize(panel.client)
            if not isinstance(session, str) or not session:
                raise RuntimeError("Authorized session could not be saved")
            new_token = secrets.token_urlsafe(32)
            self.database.put(self.key(new_token), session)
            self.panels[self.key(new_token)] = panel
            del self.panels[old_key]
            return new_token

    async def logout(self, token: str) -> None:
        try:
            await self.get(token)
        except Exception:
            logging.exception("Could not reconnect the session during logout")
        async with self.lock:
            panel = self.panels.pop(self.key(token), None)
            if panel:
                await panel.cancel_job()
                try:
                    await panel.client.log_out()
                except Exception:
                    logging.exception("Could not revoke a Telegram session")
                await panel.client.disconnect()
            self.database.delete(self.key(token))

    async def close(self) -> None:
        for panel in list(self.panels.values()):
            await panel.close()
        self.panels.clear()
        self.database.close()


REGISTRY_KEY = web.AppKey("registry", AccountRegistry)


def make_app(client_factory: Any, serialize: Any, session_db: Path,
             *, secure_cookie: bool = False) -> web.Application:
    registry = AccountRegistry(client_factory, serialize, session_db)
    csrf_secret = secrets.token_bytes(32)

    def csrf_for(token: str) -> str:
        return hmac.new(csrf_secret, token.encode("ascii"), hashlib.sha256).hexdigest()

    @web.middleware
    async def guard(request: web.Request, handler: Any) -> web.StreamResponse:
        received = request.cookies.get(COOKIE_NAME, "")
        token = received if TOKEN_RE.fullmatch(received) else secrets.token_urlsafe(32)
        request["session_token"] = token
        if request.method not in ("GET", "HEAD") and not hmac.compare_digest(
                request.headers.get("X-CSRF-Token", ""), csrf_for(token)):
            raise web.HTTPForbidden(text="CSRF token missing")
        response = await handler(request)
        next_token = request.get("next_token")
        if next_token or token != received:
            response.set_cookie(COOKIE_NAME, next_token or token, httponly=True,
                                secure=secure_cookie, samesite="Strict", path="/",
                                max_age=COOKIE_TTL)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response

    app = web.Application(middlewares=[guard], client_max_size=16 * 1024)
    app[REGISTRY_KEY] = registry

    async def homepage(request: web.Request) -> web.Response:
        return web.Response(text=PAGE_HTML.replace("__CSRF_TOKEN__", csrf_for(request["session_token"])),
                            content_type="text/html")

    async def static_file(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name == "app.js":
            return web.Response(text=PAGE_JS, content_type="application/javascript")
        if name == "style.css":
            return web.Response(text=PAGE_CSS, content_type="text/css")
        raise web.HTTPNotFound()

    async def payload(request: web.Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (ValueError, web.HTTPException):
            raise InputError("Некорректный JSON.")
        if not isinstance(data, dict):
            raise InputError("Ожидается объект JSON.")
        return data

    def failure(exc: Exception) -> web.Response:
        if isinstance(exc, InputError):
            message, status = str(exc), 400
        elif isinstance(exc, (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError)):
            message, status = "Код неверный или срок его действия истёк.", 400
        elif isinstance(exc, errors.PasswordHashInvalidError):
            message, status = "Неверный пароль двухэтапной проверки.", 400
        elif isinstance(exc, (errors.FloodWaitError, errors.SlowModeWaitError)):
            message, status = "Telegram временно ограничил запросы. Подождите и попробуйте позже.", 429
        else:
            logging.exception("Request failed", exc_info=exc)
            message, status = "Не удалось выполнить запрос. Проверьте соединение и журнал приложения.", 502
        return web.json_response({"error": message}, status=status)

    async def status(request: web.Request) -> web.Response:
        try:
            panel = await registry.get(request["session_token"])
            if panel is None:
                return web.json_response({"authorized": False, "account_name": "",
                                          "password_needed": False, "code_requested": False,
                                          "job": Panel._empty_job()})
            return web.json_response({"authorized": await panel.authorized(),
                                      "account_name": panel.account_name,
                                      "password_needed": panel.password_needed,
                                      "code_requested": bool(panel.phone),
                                      "job": panel.job})
        except Exception as exc:
            return failure(exc)

    async def code(request: web.Request) -> web.Response:
        try:
            body = await payload(request)
            panel = await registry.get(request["session_token"], create=True)
            await panel.send_code(body.get("phone", ""))
            return web.json_response({"ok": True})
        except Exception as exc:
            return failure(exc)

    async def verify(request: web.Request) -> web.Response:
        try:
            body = await payload(request)
            panel = await registry.get(request["session_token"])
            if panel is None:
                raise InputError("Сначала запросите код.")
            authorized = await panel.sign_in_code(body.get("code", ""))
            if authorized:
                request["next_token"] = await registry.promote(request["session_token"])
            return web.json_response({"authorized": authorized,
                                      "password_needed": panel.password_needed,
                                      "csrf_token": csrf_for(request.get("next_token", request["session_token"]))})
        except Exception as exc:
            return failure(exc)

    async def password(request: web.Request) -> web.Response:
        try:
            body = await payload(request)
            panel = await registry.get(request["session_token"])
            if panel is None:
                raise InputError("Сначала запросите код.")
            await panel.sign_in_password(body.get("password", ""))
            request["next_token"] = await registry.promote(request["session_token"])
            return web.json_response({"authorized": True,
                                      "csrf_token": csrf_for(request["next_token"])})
        except Exception as exc:
            return failure(exc)

    async def dialogs(request: web.Request) -> web.Response:
        try:
            panel = await registry.get(request["session_token"])
            if panel is None:
                raise InputError("Сначала войдите в аккаунт.")
            return web.json_response({"chats": await panel.refresh_dialogs()})
        except Exception as exc:
            return failure(exc)

    async def start(request: web.Request) -> web.Response:
        try:
            body = await payload(request)
            panel = await registry.get(request["session_token"])
            if panel is None or not await panel.authorized():
                raise InputError("Сначала войдите в аккаунт.")
            panel.start_job(body.get("chat_ids"), body.get("message"), body.get("interval"),
                            body.get("permission_confirmed"))
            return web.json_response({"ok": True})
        except Exception as exc:
            return failure(exc)

    async def stop(request: web.Request) -> web.Response:
        panel = await registry.get(request["session_token"])
        if panel:
            panel.stop()
        return web.json_response({"ok": True})

    async def logout(request: web.Request) -> web.Response:
        await registry.logout(request["session_token"])
        request["next_token"] = secrets.token_urlsafe(32)
        return web.json_response({"ok": True, "csrf_token": csrf_for(request["next_token"])})

    app.router.add_get("/", homepage)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/api/status", status)
    app.router.add_post("/api/code", code)
    app.router.add_post("/api/verify", verify)
    app.router.add_post("/api/password", password)
    app.router.add_get("/api/chats", dialogs)
    app.router.add_post("/api/start", start)
    app.router.add_post("/api/stop", stop)
    app.router.add_post("/api/logout", logout)

    async def startup(_: web.Application) -> None:
        registry.purge_expired()

    async def cleanup(_: web.Application) -> None:
        await registry.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.umask(0o077)
    api_id = os.getenv("API_ID", "")
    api_hash = os.getenv("API_HASH", "")
    if not api_id.isdecimal() or not api_hash:
        raise SystemExit("Set API_ID and API_HASH from my.telegram.org before starting.")
    host = os.getenv("HOST", "127.0.0.1")
    def client_factory(saved: str | None) -> TelegramClient:
        return TelegramClient(StringSession(saved or ""), int(api_id), api_hash,
                              flood_sleep_threshold=0)

    web.run_app(make_app(client_factory, lambda client: client.session.save(),
                         SESSION_DB, secure_cookie=host not in ("127.0.0.1", "localhost", "::1")), host=host,
                port=int(os.getenv("PORT", "8080")), access_log=None)


if __name__ == "__main__":
    main()
