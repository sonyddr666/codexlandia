#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MONITOR CODEX BALANCE - Edição Neandertal Retrô
Estilo: Windows 95/98 com aura de gabinete bege abrindo sozinho
"""

import sys
import json
import time
import base64
import re
import os
import warnings
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# Suprimir avisos de fontes do matplotlib
warnings.filterwarnings("ignore", message="findfont: Font family*")

# Antídoto contra PowerShell Neandertal
if os.name == 'nt':
    try:
        import ctypes
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except: pass

try:
    import requests
except ImportError:
    print("Erro: Instala 'requests' primeiro. pip install requests")
    sys.exit(1)

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                                 QHBoxLayout, QLabel, QPushButton, QTableWidget,
                                 QTableWidgetItem, QHeaderView, QTextEdit, QMdiArea,
                                 QMdiSubWindow, QMenuBar, QMenu, QStatusBar, QSplitter,
                                 QProgressBar, QFrame, QGroupBox, QTabWidget,
                                 QScrollArea, QSizePolicy)
    from PyQt6.QtGui import (QIcon, QPixmap, QFont, QColor, QPalette,
                              QAction, QCursor)
except ImportError:
    print("Erro: Instala PyQt6. pip install PyQt6")
    sys.exit(1)

import matplotlib
matplotlib.use('QtAgg')
# Configurações de fonte para evitar findfont warnings
matplotlib.rcParams['font.family'] = 'sans-serif'
matplotlib.rcParams['font.sans-serif'] = ['Tahoma', 'Segoe UI', 'Arial', 'Helvetica']
matplotlib.rcParams['font.size'] = 8
matplotlib.rcParams['figure.dpi'] = 100

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

# --- CONFIG ---
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
LOG_FILE = "hermes_monitor.log"
REFRESH_INTERVAL = 5000  # ms
SEARCH_DIR = Path('.').resolve()

# --- Tema Windows 95/98 Retrô ---
W95_STYLESHEET = """
QMainWindow {
    background-color: #008080;
}
QWidget {
    font-family: "Tahoma", "Segoe UI", "Arial", sans-serif;
    font-size: 8pt;
    color: #000000;
}
QMenuBar {
    background-color: #c0c0c0;
    color: #000000;
    border-bottom: 1px solid #808080;
    padding: 2px;
}
QMenuBar::item:selected {
    background-color: #000080;
    color: #ffffff;
}
QMenu {
    background-color: #c0c0c0;
    color: #000000;
    border: 1px solid #808080;
}
QMenu::item:selected {
    background-color: #000080;
    color: #ffffff;
}
QPushButton {
    background-color: #c0c0c0;
    color: #000000;
    border: 2px solid;
    border-color: #ffffff #808080 #808080 #ffffff;
    padding: 4px 12px;
    font-weight: bold;
}
QPushButton:pressed {
    background-color: #c0c0c0;
    border-color: #808080 #ffffff #ffffff #808080;
}
QPushButton:hover {
    background-color: #d4d4d4;
}
QTableWidget {
    background-color: #ffffff;
    alternate-background-color: #ffffe0;
    gridline-color: #808080;
    selection-background-color: #000080;
    selection-color: #ffffff;
}
QHeaderView::section {
    background-color: #c0c0c0;
    color: #000000;
    border: 1px solid #808080;
    font-weight: bold;
    padding: 4px;
}
QTextEdit {
    background-color: #000000;
    color: #00ff00;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 9pt;
    border: 2px inset #808080;
}
QGroupBox {
    border: 2px groove #808080;
    margin-top: 1ex;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 4px;
    background-color: #c0c0c0;
}
QProgressBar {
    border: 2px groove #808080;
    text-align: center;
    background-color: #ffffff;
}
QProgressBar::chunk {
    background-color: #0000ff;
}
QLabel {
    color: #000000;
}
QStatusBar {
    background-color: #c0c0c0;
    color: #000000;
}
QMdiSubWindow {
    background-color: #c0c0c0;
}
QMdiSubWindow::title {
    background-color: #000080;
    color: #ffffff;
    font-weight: bold;
    padding: 2px 4px;
}
"""

# --- utils (reutilizadas) ---
def clean_token(t):
    return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', t or '').strip()

def decode_jwt(token):
    try:
        p = token.split('.')[1]
        p += '=' * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}

def jwt_exp(token):
    claims = decode_jwt(token)
    exp = claims.get('exp')
    if not exp: return '-'
    dt = datetime.fromtimestamp(int(exp), tz=timezone.utc).astimezone()
    return dt.strftime('%d/%m %H:%M')

def jwt_email(token):
    claims = decode_jwt(token)
    profile = claims.get('https://api.openai.com/profile', {})
    return profile.get('email', claims.get('email', '-'))

def to_epoch_s(val):
    if val is None: return None
    try:
        v = float(val)
        return v / 1000.0 if v > 1_000_000_000_000 else v
    except (TypeError, ValueError): return None

def fmt_remaining(ts):
    if ts is None: return '-'
    delta = float(ts) - time.time()
    if delta <= 0: return 'agora'
    h, rem = divmod(int(delta), 3600)
    m = rem // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"

def fmt_reset_abs(ts):
    if ts is None: return ''
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone()
    return dt.strftime('%d/%m %H:%M')

def parse_quota(body):
    q = {'plan': body.get('plan_type', '-')}
    found_limits = []

    def hunt_quotas(obj):
        if isinstance(obj, dict):
            pct = obj.get('percent_left') if obj.get('percent_left') is not None else obj.get('remaining_percent')
            if pct is None and obj.get('used_percent') is not None:
                pct = 100.0 - float(obj['used_percent'])

            if pct is not None:
                rst = obj.get('reset_time_ms') or obj.get('reset_at')
                if not rst and obj.get('reset_after_seconds') is not None:
                    rst = time.time() + float(obj['reset_after_seconds'])
                if not rst and 'primary_window' in obj:
                    rst = obj['primary_window'].get('reset_time_ms')

                found_limits.append((float(pct), to_epoch_s(rst) if rst and rst > 1000000000 else rst))
            for v in obj.values():
                hunt_quotas(v)
        elif isinstance(obj, list):
            for item in obj:
                hunt_quotas(item)

    hunt_quotas(body)

    if found_limits:
        q['five_hour_pct'] = found_limits[0][0]
        q['five_hour_reset'] = found_limits[0][1]
        if len(found_limits) > 1:
            q['weekly_pct'] = found_limits[1][0]
            q['weekly_reset'] = found_limits[1][1]

    if q['plan'] == '-':
        q['plan'] = body.get('plan_type', 'Team/Biz')

    return q

def do_renew(refresh_token):
    if not refresh_token: return None
    url = "https://auth.openai.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "refresh_token": refresh_token
    }
    try:
        resp = requests.post(url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

def get_business_name(token, target_acc_id):
    url = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            accs = resp.json().get("accounts", {})
            if target_acc_id and target_acc_id in accs:
                return accs[target_acc_id].get("name") or "Personal"
            for k, v in accs.items():
                if v.get("is_active"):
                    return v.get("name") or "Personal"
    except:
        pass
    return "Desconhecido"

def extract_tokens_from_entry(entry, data):
    tok, ref, acc = None, None, None
    if isinstance(entry, dict) and 'access_token' in entry:
        tok = entry.get('access_token')
        ref = entry.get('refresh_token')
        acc = entry.get('account_id') or entry.get('accountId') or (entry.get('extra') or {}).get('account_id')
    elif isinstance(data, dict) and 'tokens' in data:
        t = data['tokens']
        tok = t.get('access_token') or t.get('access')
        ref = t.get('refresh_token') or t.get('refresh')
        acc = t.get('account_id') or t.get('accountId') or data.get('accountId')
    elif isinstance(data, dict) and 'access' in data:
        tok = data.get('access')
        ref = data.get('refresh')
        acc = data.get('accountId') or data.get('account_id')
    return tok, ref, acc

def update_json_file(file_path, old_access, new_auth_data):
    try:
        content = Path(file_path).read_text(encoding='utf-8')
        data = json.loads(content)
        modified, keep_file = False, True

        if isinstance(data, dict):
            if 'credential_pool' in data and 'openai-codex' in data['credential_pool']:
                pool = data['credential_pool']['openai-codex']
                new_pool = []
                for acc in pool:
                    if acc.get('access_token') == old_access:
                        if new_auth_data:
                            acc['access_token'] = new_auth_data.get('access_token')
                            acc['refresh_token'] = new_auth_data.get('refresh_token')
                            if 'id_token' in new_auth_data:
                                acc['id_token'] = new_auth_data['id_token']
                            new_pool.append(acc)
                            modified = True
                        else:
                            modified = True
                    else:
                        new_pool.append(acc)
                data['credential_pool']['openai-codex'] = new_pool
                if not new_pool:
                    keep_file = False

            elif 'access' in data and data['access'] == old_access:
                if new_auth_data:
                    data['access'] = new_auth_data.get('access_token')
                    data['refresh'] = new_auth_data.get('refresh_token')
                    if 'expires_in' in new_auth_data:
                        data['expires'] = int(time.time() * 1000) + (new_auth_data['expires_in'] * 1000)
                    modified = True
                else:
                    keep_file = False

        if not keep_file:
            Path(file_path).unlink(missing_ok=True)
            return "DELETADO"
        elif modified:
            Path(file_path).write_text(json.dumps(data, indent=2), encoding='utf-8')
            return "RENOVADO"
    except Exception:
        pass
    return "FALHA_UPDATE"

# --- Worker Thread para Monitoramento ---
class MonitorWorker(QThread):
    update_ui = pyqtSignal(list)
    log_signal = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.account_history = {}

    def run(self):
        try:
            while not self.isInterruptionRequested():
                try:
                    all_results = []
                    json_files = list(SEARCH_DIR.glob('*.json'))

                    for f in json_files:
                        try:
                            content = f.read_text(encoding='utf-8')
                            if not content.strip():
                                continue
                            data = json.loads(content)

                            entries = []
                            if 'credential_pool' in data and 'openai-codex' in data['credential_pool']:
                                entries = data['credential_pool']['openai-codex']
                            elif 'tokens' in data or 'access' in data:
                                entries = [{'label': 'token_extraido', 'auth_type': 'oauth'}]

                            for entry in entries:
                                res = self.process_account(entry, f.name, data)
                                if res:
                                    all_results.append(res)
                        except:
                            pass

                    self.update_ui.emit(all_results)
                except Exception as e:
                    self.log_signal.emit(f"ERRO: {e}")

                # Espera 5s, checando interrupção a cada 0.5s
                for _ in range(10):
                    if self.isInterruptionRequested():
                        break
                    time.sleep(0.5)
        finally:
            self.finished.emit()

    def process_account(self, entry, filename, data):
        if not isinstance(entry, dict):
            return None

        raw_label = entry.get('label', entry.get('id', 'mutante'))
        label = f"[{filename}] {raw_label}"

        tok, ref, acc_id = extract_tokens_from_entry(entry, data)
        r = {
            'label': label,
            'email': '-',
            'quota': {},
            'http_status': '-',
            'result': '?',
            'biz_name': 'A carregar...',
            'diff_5h': '',
            'diff_sem': ''
        }

        if not tok:
            r['result'] = 'ERRO (Sem token)'
            return r

        claims = decode_jwt(tok)
        r['email'] = jwt_email(tok)
        if not acc_id:
            acc_id = claims.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')

        needs_renew = False
        if time.time() > claims.get('exp', 0):
            needs_renew = True

        headers = {
            'Authorization': f'Bearer {tok}',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0'
        }
        if acc_id:
            headers['ChatGPT-Account-Id'] = acc_id

        test_url = "https://chatgpt.com/backend-api/wham/usage"

        if not needs_renew:
            try:
                resp = requests.get(test_url, headers=headers, timeout=10)
                r['http_status'] = resp.status_code
                if resp.status_code == 200:
                    r['result'] = 'OK'
                    r['quota'] = parse_quota(resp.json())
                elif resp.status_code == 429:
                    r['result'] = 'ESGOTADO'
                elif resp.status_code == 401:
                    needs_renew = True
                else:
                    r['result'] = f'HTTP {resp.status_code}'
            except Exception:
                r['result'] = 'ERRO DE LIGAÇÃO'

        if needs_renew:
            new_auth = do_renew(ref)
            if new_auth and 'access_token' in new_auth:
                status = update_json_file(filename, tok, new_auth)
                r['result'] = 'RENOVADO' if status == "RENOVADO" else 'RENOVADO (Mem)'
                self.log_signal.emit(f"AUTO-RENEW: {r['email']} ({filename}) renovado.")
            else:
                status = update_json_file(filename, tok, None)
                r['result'] = 'APAGADO' if status == "DELETADO" else 'MORTO'
                self.log_signal.emit(f"FALÊNCIA: {r['email']} ({filename}) token morreu.")

        # Histórico e diff
        curr_5h = r['quota'].get('five_hour_pct')
        curr_sem = r['quota'].get('weekly_pct')
        uid = f"{filename}_{r['email']}"

        if uid in self.account_history:
            prev = self.account_history[uid]

            if prev['5h'] is not None and curr_5h is not None:
                if curr_5h < prev['5h']:
                    r['diff_5h'] = "↓"
                elif curr_5h > prev['5h']:
                    r['diff_5h'] = "↑"

            if prev['sem'] is not None and curr_sem is not None:
                if curr_sem < prev['sem']:
                    r['diff_sem'] = "↓"
                elif curr_sem > prev['sem']:
                    r['diff_sem'] = "↑"

        # Buscar workspace nome
        if r['result'] in ('OK', 'RENOVADO'):
            r['biz_name'] = get_business_name(tok, acc_id) or "Personal"

        self.account_history[uid] = {
            '5h': curr_5h,
            'sem': curr_sem,
            'biz_name': r['biz_name']
        }

        return r

    def stop(self):
        self.requestInterruption()

# --- Janela de Device Login (estilo Windows 95) ---
class ClickableLabel(QLabel):
    """QLabel que emite sinal 'clicked' ao receber clique esquerdo."""
    clicked = pyqtSignal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class LoginWorker(QThread):
    """Worker thread para o fluxo de Device Login (não bloqueia a GUI)."""
    status_changed = pyqtSignal(str)
    code_set = pyqtSignal(str)
    progress_set = pyqtSignal(int)
    success = pyqtSignal(dict)
    error = pyqtSignal(str)

    def run(self):
        try:
            resp = requests.post(
                "https://auth.openai.com/api/accounts/deviceauth/usercode",
                json={"client_id": CLIENT_ID},
                timeout=10
            )
            if resp.status_code != 200:
                self.error.emit("Erro ao gerar código da API.")
                return

            data = resp.json()
            user_code = data.get("user_code")
            device_auth_id = data.get("device_auth_id")

            self.code_set.emit(user_code)

            max_attempts = 60
            for attempt in range(max_attempts):
                self.progress_set.emit(int((attempt + 1) / max_attempts * 100))
                self.status_changed.emit(f"A escutar a OpenAI... ({attempt+1}/{max_attempts})")

                poll_resp = requests.post(
                    "https://auth.openai.com/api/accounts/deviceauth/token",
                    json={
                        "client_id": CLIENT_ID,
                        "device_auth_id": device_auth_id,
                        "user_code": user_code
                    },
                    timeout=10
                )

                if poll_resp.status_code == 200:
                    poll_data = poll_resp.json()
                    auth_code = poll_data.get("authorization_code")
                    code_verifier = poll_data.get("code_verifier")

                    final_resp = requests.post(
                        "https://auth.openai.com/oauth/token",
                        data={
                            "grant_type": "authorization_code",
                            "client_id": CLIENT_ID,
                            "code": auth_code,
                            "code_verifier": code_verifier,
                            "redirect_uri": DEVICE_REDIRECT_URI
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"}
                    )

                    if final_resp.status_code == 200:
                        tokens = final_resp.json()
                        self.success.emit(tokens)
                        return
                    else:
                        self.error.emit("Falha ao trocar código por token.")
                        return

                time.sleep(5)

            self.error.emit("Tempo esgotado. Tenta de novo.")
        except Exception as e:
            self.error.emit(f"Erro de ligação: {e}")


class DeviceLoginWindow(QMdiSubWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚡ Injeção de Código (Device Login)")
        self.setMinimumSize(480, 320)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)

        widget = QWidget()
        widget.setStyleSheet("background-color: #c0c0c0;")
        layout = QVBoxLayout(widget)

        # Título
        title = QLabel("1. Pedir Código à OpenAI")
        title_font = QFont("Tahoma", 10, QFont.Weight.Bold)
        title.setFont(title_font)
        layout.addWidget(title)

        # Status
        self.status_lbl = QLabel("A gerar token de acesso...")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        # Código (clique para copiar)
        self.code_lbl = ClickableLabel("----")
        code_font = QFont("Courier New", 18, QFont.Weight.Bold)
        self.code_lbl.setFont(code_font)
        self.code_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_lbl.setStyleSheet("""
            background-color: #000000;
            color: #00ff00;
            border: 2px inset #808080;
            padding: 20px;
        """)
        layout.addWidget(self.code_lbl)

        hint = QLabel("(Clica no código para copiar)")
        hint_font = QFont("Tahoma", 8)
        hint.setFont(hint_font)
        layout.addWidget(hint)

        # Botão Abrir Browser
        self.btn_open = QPushButton("Abrir Browser (auth.openai.com/codex/device)")
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self.open_browser)
        layout.addWidget(self.btn_open)

        # Barra de progresso retro
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: 2px groove #808080;
                text-align: center;
                background-color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #000080;
            }
        """)
        layout.addWidget(self.progress)

        self.setWidget(widget)

        # Conectar clique para copiar
        self.code_lbl.clicked.connect(self.copy_code)

        # Iniciar worker thread
        self.worker = LoginWorker()
        self.worker.status_changed.connect(self.status_lbl.setText)
        self.worker.code_set.connect(self.handle_code)
        self.worker.progress_set.connect(self.progress.setValue)
        self.worker.success.connect(self.on_success)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def handle_code(self, code):
        self.code_lbl.setText(code)
        self.btn_open.setEnabled(True)

    def copy_code(self):
        code = self.code_lbl.text()
        if code and code != "----":
            QApplication.clipboard().setText(code)
            self.status_lbl.setText("Código copiado para a área de transferência! 📋")

    def open_browser(self):
        import webbrowser
        webbrowser.open("https://auth.openai.com/codex/device")

    def on_success(self, tokens):
        self.status_lbl.setText("SUCESSO! Conta injetada no auth(infinity).json")
        self.status_lbl.setStyleSheet("color: #00ff00;")
        self.save_new_account(tokens)
        QTimer.singleShot(3000, self.close)

    def on_error(self, msg):
        self.status_lbl.setText(msg)
        self.status_lbl.setStyleSheet("color: #ff0000;")

    def closeEvent(self, event):
        # Garantir que a thread de login termine antes de fechar
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.requestInterruption()
            if not self.worker.wait(1000):  # espera 1s
                self.worker.terminate()
        event.accept()

    def save_new_account(self, tokens):
        filename = "auth(infinity).json"
        payload = decode_jwt(tokens.get("access_token", ""))
        acc_id = payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")

        new_entry = {
            "label": f"Injetada_{datetime.now().strftime('%H%M')}",
            "auth_type": "oauth",
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "account_id": acc_id
        }

        data = {"credential_pool": {"openai-codex": []}}
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "credential_pool" not in data:
                        data["credential_pool"] = {"openai-codex": []}
            except:
                pass

        data["credential_pool"]["openai-codex"].append(new_entry)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def login_flow(self):
        try:
            resp = requests.post(
                "https://auth.openai.com/api/accounts/deviceauth/usercode",
                json={"client_id": CLIENT_ID},
                timeout=10
            )
            if resp.status_code != 200:
                self.status_lbl.setText("Erro ao gerar código da API.")
                return

            data = resp.json()
            user_code = data.get("user_code")
            device_auth_id = data.get("device_auth_id")

            self.code_lbl.setText(user_code)
            self.status_lbl.setText("Código pronto:")
            self.btn_open.setEnabled(True)

            max_attempts = 60
            for attempt in range(max_attempts):
                self.progress.setValue(int((attempt + 1) / max_attempts * 100))
                self.status_lbl.setText(f"A escutar a OpenAI... ({attempt+1}/{max_attempts})")

                poll_resp = requests.post(
                    "https://auth.openai.com/api/accounts/deviceauth/token",
                    json={
                        "client_id": CLIENT_ID,
                        "device_auth_id": device_auth_id,
                        "user_code": user_code
                    },
                    timeout=10
                )

                if poll_resp.status_code == 200:
                    poll_data = poll_resp.json()
                    auth_code = poll_data.get("authorization_code")
                    code_verifier = poll_data.get("code_verifier")

                    final_resp = requests.post(
                        "https://auth.openai.com/oauth/token",
                        data={
                            "grant_type": "authorization_code",
                            "client_id": CLIENT_ID,
                            "code": auth_code,
                            "code_verifier": code_verifier,
                            "redirect_uri": DEVICE_REDIRECT_URI
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"}
                    )

                    if final_resp.status_code == 200:
                        tokens = final_resp.json()
                        self.save_new_account(tokens)
                        self.status_lbl.setText("SUCESSO! Conta injetada no auth(infinity).json")
                        QTimer.singleShot(3000, self.close)
                        return

                time.sleep(5)

            self.status_lbl.setText("Tempo esgotado. Tenta de novo.")

        except Exception as e:
            self.status_lbl.setText(f"Erro de ligação: {e}")

    def save_new_account(self, tokens):
        filename = "auth(infinity).json"
        payload = decode_jwt(tokens.get("access_token", ""))
        acc_id = payload.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")

        new_entry = {
            "label": f"Injetada_{datetime.now().strftime('%H%M')}",
            "auth_type": "oauth",
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "account_id": acc_id
        }

        data = {"credential_pool": {"openai-codex": []}}
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "credential_pool" not in data:
                        data["credential_pool"] = {"openai-codex": []}
            except:
                pass

        data["credential_pool"]["openai-codex"].append(new_entry)

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

# --- Janela do Gráfico de Consumo ---
class QuotaGraphWindow(QMdiSubWindow):
    def __init__(self, account_data, parent=None):
        super().__init__(parent)
        self.account_data = account_data
        self.setWindowTitle("📊 Debounce Visualizer (Cota 5h)")
        self.setMinimumSize(500, 350)

        widget = QWidget()
        widget.setStyleSheet("background-color: #c0c0c0;")
        layout = QVBoxLayout(widget)

        # Canvas do Matplotlib
        self.fig = Figure(figsize=(5, 3), facecolor='white')
        self.canvas = FigureCanvas(self.fig)
        layout.addWidget(self.canvas)

        self.setWidget(widget)
        self.update_graph()

    def update_graph(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor('#ffffe0')
        ax.set_title('Consumo 5h (%)', fontsize=10)
        ax.set_xlabel('Tempo', fontsize=8)
        ax.set_ylabel('%', fontsize=8)
        ax.grid(True, linestyle='-', linewidth=1, color='#808080')
        ax.tick_params(axis='both', which='major', labelsize=8)

        if self.account_data:
            emails = list(self.account_data.keys())[:5]
            values = [self.account_data[e].get('current_5h', 0) for e in emails]

            bars = ax.bar(range(len(emails)), values, color='#000080', edgecolor='#000000', linewidth=2)
            ax.set_xticks(range(len(emails)))
            ax.set_xticklabels([e[:10] + '...' for e in emails], rotation=45, ha='right')

            for bar, val in zip(bars, values):
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height,
                        f'{val:.0f}%', ha='center', va='bottom', fontsize=8)

        self.canvas.draw()

# --- Janela de Logs ---
class LogWindow(QMdiSubWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📟 Terminal de Eventos (Live Logs)")
        self.setMinimumSize(600, 300)

        widget = QWidget()
        widget.setStyleSheet("background-color: #c0c0c0;")
        layout = QVBoxLayout(widget)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("""
            QTextEdit {
                background-color: #000000;
                color: #00ff00;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 9pt;
                border: 2px inset #808080;
            }
        """)
        layout.addWidget(self.log_area)

        # Botão limpar
        btn_clear = QPushButton("🗑 Limpar Logs")
        btn_clear.clicked.connect(self.log_area.clear)
        layout.addWidget(btn_clear)

        self.setWidget(widget)

    def append_log(self, text):
        self.log_area.append(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

# --- Janela Principal ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("📈 BOLSA DE VALORES DO CODEX · Edição Neandertal")
        self.setGeometry(100, 100, 1200, 800)

        # Aplicar tema retrô
        self.setStyleSheet(W95_STYLESHEET)

        # MDI Area
        self.mdi = QMdiArea()
        self.mdi.setBackground(QColor('#008080'))  # Azul bebê retrô
        self.setCentralWidget(self.mdi)

        # Barra de status
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("Pronto. A aguardar início da monitorização...")

        # Worker thread
        self.monitor_worker = None

        # Criar janelas internas PRIMEIRO (antes dos menus)
        self.create_internal_windows()

        # Criar menus DEPOIS (referencia as janelas criadas)
        self.create_menus()

        # Posicionar iniciais em cascata
        self.mdi.cascadeSubWindows()

    def create_menus(self):
        menubar = self.menuBar()

        # Menu Arquivo
        file_menu = menubar.addMenu("&Arquivo")

        inject_action = QAction("⚡ Injetar Conta (Device Code)", self)
        inject_action.triggered.connect(self.open_device_login)
        file_menu.addAction(inject_action)

        file_menu.addSeparator()

        exit_action = QAction("S&air", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Menu Visualizar
        view_menu = menubar.addMenu("&Visualizar")

        toggle_monitor_action = QAction("▶ Iniciar/Parar Monitor (F5)", self)
        toggle_monitor_action.setShortcut("F5")
        toggle_monitor_action.triggered.connect(self.toggle_monitor)
        view_menu.addAction(toggle_monitor_action)

        view_menu.addSeparator()

        cascade_action = QAction("🌊 Organizar em Cascata", self)
        cascade_action.triggered.connect(self.mdi.cascadeSubWindows)
        view_menu.addAction(cascade_action)

        # tile_action removido — esticava as janelas

        # Submenu para mostrar/ocultar painéis
        panels_menu = view_menu.addMenu("&Painéis")

        table_toggle = QAction("📊 Tabela de Contas", self)
        table_toggle.setCheckable(True)
        table_toggle.setChecked(True)
        table_toggle.toggled.connect(self.table_win.setVisible)
        panels_menu.addAction(table_toggle)

        log_toggle = QAction("📟 Terminal de Logs", self)
        log_toggle.setCheckable(True)
        log_toggle.setChecked(True)
        log_toggle.toggled.connect(self.log_win.setVisible)
        panels_menu.addAction(log_toggle)

        graph_toggle = QAction("📈 Gráfico de Cota", self)
        graph_toggle.setCheckable(True)
        graph_toggle.setChecked(True)
        graph_toggle.toggled.connect(self.graph_win.setVisible)
        panels_menu.addAction(graph_toggle)

        control_toggle = QAction("🎮 Painel de Controle", self)
        control_toggle.setCheckable(True)
        control_toggle.setChecked(True)
        control_toggle.toggled.connect(self.control_win.setVisible)
        panels_menu.addAction(control_toggle)

        # Menu Janela (Window)
        window_menu = menubar.addMenu("&Janela")

        close_all_action = QAction("❌ Fechar Todas", self)
        close_all_action.triggered.connect(self.mdi.closeAllSubWindows)
        window_menu.addAction(close_all_action)

        window_menu.addSeparator()

        # Ações individuais para cada subwindow (para trazer para frente)
        bring_table_action = QAction("📊 Trazer Tabela", self)
        bring_table_action.triggered.connect(self.table_win.showNormal)
        bring_table_action.triggered.connect(self.table_win.raise_)
        window_menu.addAction(bring_table_action)

        bring_log_action = QAction("📟 Trazer Logs", self)
        bring_log_action.triggered.connect(self.log_win.showNormal)
        bring_log_action.triggered.connect(self.log_win.raise_)
        window_menu.addAction(bring_log_action)

        bring_graph_action = QAction("📈 Trazer Gráfico", self)
        bring_graph_action.triggered.connect(self.graph_win.showNormal)
        bring_graph_action.triggered.connect(self.graph_win.raise_)
        window_menu.addAction(bring_graph_action)

        bring_control_action = QAction("🎮 Trazer Painel", self)
        bring_control_action.triggered.connect(self.control_win.showNormal)
        bring_control_action.triggered.connect(self.control_win.raise_)
        window_menu.addAction(bring_control_action)

        # Menu Ajuda
        help_menu = menubar.addMenu("&Ajuda")

        about_action = QAction("&Sobre", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def create_internal_windows(self):
        # 1. Tabela de Contas
        self.table_win = QMdiSubWindow()
        self.table_win.setWindowTitle("📊 Account Soulex Monitor")
        self.table_win.setMinimumSize(950, 400)

        table_widget = QWidget()
        table_widget.setStyleSheet("background-color: #c0c0c0;")
        table_layout = QVBoxLayout(table_widget)

        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "Ficheiro/Label", "Resultado", "HTTP",
            "Cota 5H", "Reset 5H", "Semanal",
            "Workspace", "E-mail"
        ])

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)

        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Cores retrô
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #ffffe0;
                gridline-color: #808080;
                border: 2px inset #808080;
            }
            QTableWidget::item:selected {
                background-color: #000080;
                color: #ffffff;
            }
        """)

        table_layout.addWidget(self.table)
        self.table_win.setWidget(table_widget)
        self.mdi.addSubWindow(self.table_win)
        # Botões de janela (fechar, minimizar, maximizar) + menu sistema
        self.table_win.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.table_win.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.table_win.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.table_win.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.table_win.show()

        self.log_win = LogWindow()
        self.mdi.addSubWindow(self.log_win)
        self.log_win.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.log_win.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.log_win.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.log_win.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.log_win.show()

        # 3. Janela do Gráfico
        self.graph_win = QuotaGraphWindow({})
        self.mdi.addSubWindow(self.graph_win)
        self.graph_win.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.graph_win.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.graph_win.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.graph_win.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.graph_win.show()

        # 4. Painel de Controle (botões)
        control_win = QMdiSubWindow()
        control_win.setWindowTitle("🎮 Painel de Controle")
        control_win.setMinimumSize(300, 200)

        cw = QWidget()
        cw.setStyleSheet("background-color: #c0c0c0;")
        cl = QVBoxLayout(cw)

        # Botão INICIAR (estilo botão verde Windows 95)
        self.btn_start = QPushButton("▶ INICIAR MONITORIZAÇÃO")
        start_font = QFont("Tahoma", 10, QFont.Weight.Bold)
        self.btn_start.setFont(start_font)
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setStyleSheet("""
            QPushButton {
                background-color: #00ff00;
                color: #000000;
                border: 3px outset #ffffff;
            }
            QPushButton:pressed {
                border: 3px inset #ffffff;
            }
        """)
        self.btn_start.clicked.connect(self.toggle_monitor)
        cl.addWidget(self.btn_start)

        # Botão INJETAR
        self.btn_inject = QPushButton("⚡ INJETAR CONTA (Device)")
        self.btn_inject.setFont(start_font)
        self.btn_inject.setMinimumHeight(40)
        self.btn_inject.setStyleSheet("""
            QPushButton {
                background-color: #0000ff;
                color: #ffffff;
                border: 3px outset #ffffff;
            }
            QPushButton:pressed {
                border: 3px inset #ffffff;
            }
        """)
        self.btn_inject.clicked.connect(self.open_device_login)
        cl.addWidget(self.btn_inject)

        # Stats
        stats_group = QGroupBox("📈 Estatísticas em Tempo Real")
        stats_group.setFont(QFont("Tahoma", 8, QFont.Weight.Bold))
        stats_layout = QVBoxLayout(stats_group)

        self.lbl_total = QLabel("Total: 0 contas")
        self.lbl_ok = QLabel("OK: 0")
        self.lbl_ok.setStyleSheet("color: #00ff00;")
        self.lbl_esgot = QLabel("Esgotado: 0")
        self.lbl_esgot.setStyleSheet("color: #ff0000;")
        self.lbl_erro = QLabel("Erro: 0")
        self.lbl_erro.setStyleSheet("color: #ff0000;")

        stats_layout.addWidget(self.lbl_total)
        stats_layout.addWidget(self.lbl_ok)
        stats_layout.addWidget(self.lbl_esgot)
        stats_layout.addWidget(self.lbl_erro)

        cl.addWidget(stats_group)

        # Barra de estado da thread
        self.thread_status = QLabel("⏸ Thread: Parada")
        self.thread_status.setFont(QFont("Tahoma", 8))
        cl.addWidget(self.thread_status)

        cl.addStretch()

        control_win.setWidget(cw)
        self.mdi.addSubWindow(control_win)
        self.control_win = control_win
        self.control_win.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.control_win.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.control_win.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.control_win.setWindowFlag(Qt.WindowType.WindowSystemMenuHint, True)
        self.control_win.show()

        # Posicionar em cascata inicial
        self.mdi.cascadeSubWindows()

    def toggle_monitor(self):
        if self.monitor_worker and self.monitor_worker.isRunning():
            self.monitor_worker.requestInterruption()
            self.btn_start.setText("⏹ A PARAR...")
            self.thread_status.setText("⏸ Thread: A parar...")
            return

        # Iniciar nova monitorização
        self.monitor_worker = MonitorWorker()
        self.monitor_worker.update_ui.connect(self.update_table)
        self.monitor_worker.log_signal.connect(self.log_win.append_log)
        self.monitor_worker.finished.connect(self.on_monitor_finished)
        self.monitor_worker.start()

        self.btn_start.setText("⏹ PARAR MONITORIZAÇÃO")
        self.btn_start.setStyleSheet("""
            QPushButton {
                background-color: #ff0000;
                color: #ffffff;
                border: 3px outset #ffffff;
            }
            QPushButton:pressed {
                border: 3px inset #ffffff;
            }
        """)
        self.thread_status.setText("🟢 Thread: A Rodar")
        self.statusBar.showMessage("Monitorização ativa... A escutar API OpenAI.")

    def on_monitor_finished(self):
        # Verifica se o worker que finalizou é o atual
        worker = self.sender()
        if worker is self.monitor_worker:
            self.btn_start.setText("▶ INICIAR MONITORIZAÇÃO")
            self.btn_start.setStyleSheet("""
                QPushButton {
                    background-color: #00ff00;
                    color: #000000;
                    border: 3px outset #ffffff;
                }
                QPushButton:pressed {
                    border: 3px inset #ffffff;
                }
            """)
            self.thread_status.setText("⏸ Thread: Parada")
            self.statusBar.showMessage("Monitorização parada.")

    def update_table(self, results):
        self.table.setRowCount(0)

        total = len(results)
        ok_count = 0
        esgot_count = 0
        erro_count = 0

        for r in results:
            row = self.table.rowCount()
            self.table.insertRow(row)

            quota = r.get('quota', {})
            pct5h = f"{int(quota.get('five_hour_pct', 0))}%" if quota.get('five_hour_pct') is not None else "-"
            if r.get('diff_5h'):
                pct5h += f" {r['diff_5h']}"

            rst5h = fmt_remaining(quota.get('five_hour_reset'))

            pctsem = f"{int(quota.get('weekly_pct', 0))}%" if quota.get('weekly_pct') is not None else "-"
            if r.get('diff_sem'):
                pctsem += f" {r['diff_sem']}"

            # Cor da linha
            result = r['result']
            if result in ('OK', 'RENOVADO'):
                color = QColor('#008000')  # Verde
                ok_count += 1
            elif result in ('ESGOTADO',):
                color = QColor('#ff0000')  # Vermelho
                esgot_count += 1
            elif 'ERRO' in result or 'MORTO' in result:
                color = QColor('#ff0000')  # Vermelho
                erro_count += 1
            else:
                color = QColor('#ff8000')  # Laranja

            items = [
                QTableWidgetItem(r['label']),
                QTableWidgetItem(result),
                QTableWidgetItem(str(r['http_status'] or '-')),
                QTableWidgetItem(pct5h),
                QTableWidgetItem(rst5h),
                QTableWidgetItem(pctsem),
                QTableWidgetItem(r['biz_name']),
                QTableWidgetItem(r['email'])
            ]

            for col, item in enumerate(items):
                item.setForeground(color)
                self.table.setItem(row, col, item)

        # Atualizar estatísticas
        self.lbl_total.setText(f"Total: {total} contas")
        self.lbl_ok.setText(f"OK: {ok_count}")
        self.lbl_esgot.setText(f"Esgotado: {esgot_count}")
        self.lbl_erro.setText(f"Erro: {erro_count}")

        # Atualizar gráfico
        account_data = {}
        for r in results:
            email = r.get('email', '-')
            quota = r.get('quota', {})
            five_hour_pct = quota.get('five_hour_pct')
            if email != '-' and five_hour_pct is not None:
                account_data[email] = {'current_5h': five_hour_pct}
        self.graph_win.account_data = account_data
        self.graph_win.update_graph()

    def open_device_login(self):
        sub = DeviceLoginWindow()
        self.mdi.addSubWindow(sub)
        sub.show()
        sub.setFocus()

    def show_about(self):
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("Sobre")
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            "<h3>📈 Monitor Codex Balance</h3>"
            "<p>Edição Neandertal Retrô · Windows 95/98 Style</p>"
            "<p>Funcionalidades:</p>"
            "<ul>"
            "<li>Monitorização de cotas (5h e Semanal)</li>"
            "<li>Auto-renovação de tokens via refresh</li>"
            "<li>Device Code Flow integrado</li>"
            "<li>Gráfico de consumo retrô</li>"
            "<li>Logs em tempo real estilo terminal</li>"
            "</ul>"
            "<p><small>Aura: 💀 Feito com Python + PyQt6 + Matplotlib</small></p>"
        )
        msg.setIcon(QtWidgets.QMessageBox.Icon.Information)
        msg.exec()

    def closeEvent(self, event):
        if self.monitor_worker and self.monitor_worker.isRunning():
            self.monitor_worker.stop()
            # Espera no máximo 2 segundos pela thread terminar
            if not self.monitor_worker.wait(2000):
                self.monitor_worker.terminate()
        event.accept()

# --- Main Entry Point ---
def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Base, mas sobrescrevemos com stylesheet

    window = MainWindow()
    window.show()

    sys.exit(app.exec())

if __name__ == '__main__':
    main()
