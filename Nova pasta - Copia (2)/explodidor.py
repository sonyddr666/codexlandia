#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import time
import json
import base64
import re
import os
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# Antídoto contra PowerShell Neandertal (Força cores ANSI)
if os.name == 'nt':
    try:
        import ctypes
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except:
        pass

try:
    import requests
except ImportError:
    print("Erro: A biblioteca 'requests' não está instalada. Executa: pip install requests")
    sys.exit(1)

# --- CONFIGURAÇÕES DA API ---
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
LOG_FILE = "hermes_monitor.log"
TEST_ENDPOINTS = {
    "openai-codex": ("https://chatgpt.com/backend-api/wham/usage", "GET", None),
}

# --- FUNÇÕES UTILITÁRIAS (Core Logic) ---
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
    return f"{int(h)}h{int(m):02d}m" if h else f"{int(m)}m"

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
                            if 'id_token' in new_auth_data: acc['id_token'] = new_auth_data['id_token']
                            new_pool.append(acc)
                            modified = True
                        else: modified = True
                    else: new_pool.append(acc)
                data['credential_pool']['openai-codex'] = new_pool
                if not new_pool: keep_file = False

            elif 'access' in data and data['access'] == old_access:
                if new_auth_data:
                    data['access'] = new_auth_data.get('access_token')
                    data['refresh'] = new_auth_data.get('refresh_token')
                    if 'expires_in' in new_auth_data:
                        data['expires'] = int(time.time() * 1000) + (new_auth_data['expires_in'] * 1000)
                    modified = True
                else: keep_file = False

        if not keep_file:
            Path(file_path).unlink(missing_ok=True)
            return "DELETADO"
        elif modified:
            Path(file_path).write_text(json.dumps(data, indent=2), encoding='utf-8')
            return "RENOVADO"
    except Exception: pass
    return "FALHA_UPDATE"

# --- CLASSE DA INTERFACE GRÁFICA (A BOLSA) ---
class BolsaCodexApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bolsa de Valores do Codex OS")
        self.root.geometry("1100x700")
        self.root.configure(bg="#0d1117")
        
        self.is_monitoring = False
        self.monitor_thread = None
        self.account_history = {}
        
        self.setup_ui()
        
    def setup_ui(self):
        # Estilos
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#161b22", foreground="#e6edf3", fieldbackground="#161b22", borderwidth=0, rowheight=25)
        style.configure("Treeview.Heading", background="#21262d", foreground="#58a6ff", font=('Segoe UI', 10, 'bold'), borderwidth=1)
        style.map("Treeview", background=[('selected', '#1f6feb')])

        # Header
        header_frame = tk.Frame(self.root, bg="#0d1117")
        header_frame.pack(fill=tk.X, padx=10, pady=10)
        
        lbl_title = tk.Label(header_frame, text="📈 BOLSA DE VALORES DO CODEX", font=("Courier New", 18, "bold"), bg="#0d1117", fg="#3fb950")
        lbl_title.pack(side=tk.LEFT)
        
        self.btn_start = tk.Button(header_frame, text="▶ Iniciar Monitorização", bg="#238636", fg="white", font=("Segoe UI", 10, "bold"), command=self.toggle_monitor, relief=tk.FLAT, padx=10, pady=5)
        self.btn_start.pack(side=tk.RIGHT, padx=5)
        
        self.btn_login = tk.Button(header_frame, text="⚡ Injetar Conta (Device Code)", bg="#1f6feb", fg="white", font=("Segoe UI", 10, "bold"), command=self.open_login_window, relief=tk.FLAT, padx=10, pady=5)
        self.btn_login.pack(side=tk.RIGHT, padx=5)

        # Tabela
        columns = ("label", "status", "http", "5h", "reset5h", "sem", "workspace", "email")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings", selectmode="browse")
        
        self.tree.heading("label", text="Ficheiro / Label")
        self.tree.heading("status", text="Resultado")
        self.tree.heading("http", text="HTTP")
        self.tree.heading("5h", text="Cota 5H")
        self.tree.heading("reset5h", text="Reset 5H")
        self.tree.heading("sem", text="Semanal")
        self.tree.heading("workspace", text="Workspace")
        self.tree.heading("email", text="E-mail")
        
        self.tree.column("label", width=180)
        self.tree.column("status", width=120)
        self.tree.column("http", width=60, anchor=tk.CENTER)
        self.tree.column("5h", width=80, anchor=tk.CENTER)
        self.tree.column("reset5h", width=80, anchor=tk.CENTER)
        self.tree.column("sem", width=80, anchor=tk.CENTER)
        self.tree.column("workspace", width=120)
        self.tree.column("email", width=200)
        
        # Tags de cor para a tabela
        self.tree.tag_configure("ok", foreground="#56d364")
        self.tree.tag_configure("warn", foreground="#e3b341")
        self.tree.tag_configure("error", foreground="#ff7b72")
        self.tree.tag_configure("dead", foreground="#8b949e")

        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Log Console
        log_frame = tk.Frame(self.root, bg="#0d1117")
        log_frame.pack(fill=tk.X, padx=10, pady=10)
        tk.Label(log_frame, text="Terminal de Eventos (Live Logs):", bg="#0d1117", fg="#8b949e", font=("Segoe UI", 9)).pack(anchor=tk.W)
        self.log_area = scrolledtext.ScrolledText(log_frame, height=8, bg="#010409", fg="#a5d6ff", font=("Consolas", 9), borderwidth=1, relief=tk.SUNKEN)
        self.log_area.pack(fill=tk.X)

    def log(self, msg):
        timestamp = datetime.now().strftime('%H:%M:%S')
        log_line = f"[{timestamp}] {msg}\n"
        self.root.after(0, self._append_log, log_line)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)

    def _append_log(self, text):
        self.log_area.insert(tk.END, text)
        self.log_area.see(tk.END)

    def toggle_monitor(self):
        if self.is_monitoring:
            self.is_monitoring = False
            self.btn_start.config(text="▶ Iniciar Monitorização", bg="#238636")
            self.log("SISTEMA PARADO.")
        else:
            self.is_monitoring = True
            self.btn_start.config(text="⏹ Parar Monitorização", bg="#da3633")
            self.log("SISTEMA INICIADO: A bater na API para extrair os dados...")
            
            # --- TELA DE LOADING NA TABELA ---
            for item in self.tree.get_children():
                self.tree.delete(item)
            self.tree.insert("", tk.END, values=("A iniciar motores...", "A CARREGAR ⏳", "...", "...", "...", "...", "Aguarde", "..."), tags=("warn",))
            
            self.monitor_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
            self.monitor_thread.start()

    def monitoring_loop(self):
        search_dir = Path('.').resolve()
        while self.is_monitoring:
            json_files = list(search_dir.glob('*.json'))
            all_results = []
            
            for f in json_files:
                try:
                    content = f.read_text(encoding='utf-8')
                    if not content.strip(): continue
                    data = json.loads(content)
                    
                    entries = []
                    if 'credential_pool' in data and 'openai-codex' in data['credential_pool']:
                        entries = data['credential_pool']['openai-codex']
                    elif 'tokens' in data or 'access' in data:
                        entries = [{'label': 'token_extraido', 'auth_type': 'oauth'}]
                        
                    for entry in entries:
                        res = self.process_account(entry, f.name, data)
                        if res: all_results.append(res)
                except: pass
                
            self.root.after(0, self.update_table, all_results)
            
            # Espera 5 segundos sem travar a thread
            for _ in range(10):
                if not self.is_monitoring: break
                time.sleep(0.5)

    def process_account(self, entry, filename, data):
        if not isinstance(entry, dict): return None
        raw_label = entry.get('label', entry.get('id', 'mutante'))
        label = f"[{filename}] {raw_label}"
        
        tok, ref, acc_id = extract_tokens_from_entry(entry, data)
        r = {'label': label, 'email': '-', 'quota': {}, 'http_status': '-', 'result': '?', 'biz_name': 'A carregar...'}
                 
        if not tok: 
            r['result'] = 'ERRO (Sem token)'; return r

        claims = decode_jwt(tok)
        r['email'] = jwt_email(tok)
        if not acc_id: acc_id = claims.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')

        needs_renew = False
        if time.time() > claims.get('exp', 0): needs_renew = True
        
        headers = {'Authorization': f'Bearer {tok}', 'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'}
        if acc_id: headers['ChatGPT-Account-Id'] = acc_id

        if not needs_renew:
            try:
                resp = requests.get(TEST_ENDPOINTS['openai-codex'][0], headers=headers, timeout=10)
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
            except Exception as e:
                r['result'] = 'ERRO DE LIGAÇÃO'

        if needs_renew:
            new_auth = do_renew(ref)
            if new_auth and 'access_token' in new_auth:
                status = update_json_file(filename, tok, new_auth)
                r['result'] = 'RENOVADO' if status == "RENOVADO" else 'RENOVADO (Mem)'
                self.log(f"AUTO-RENEW: {r['email']} ({filename}) renovado.")
            else:
                status = update_json_file(filename, tok, None)
                r['result'] = 'APAGADO' if status == "DELETADO" else 'MORTO'
                self.log(f"FALÊNCIA: {r['email']} ({filename}) token morreu.")

        curr_5h = r['quota'].get('five_hour_pct')
        curr_sem = r['quota'].get('weekly_pct')
        uid = f"{filename}_{r['email']}"
        biz_name = "A carregar..."
        
        if uid in self.account_history:
            prev = self.account_history[uid]
            biz_name = prev.get('biz_name', biz_name)
            
            if prev['5h'] is not None and curr_5h is not None:
                if curr_5h < prev['5h']:
                    r['diff_5h'] = "↓"
                    self.log(f"QUEDA: {r['email']} gastou: {prev['5h']}% -> {curr_5h}%")
                elif curr_5h > prev['5h']:
                    r['diff_5h'] = "↑"
                    self.log(f"LUCRO: {r['email']} resetou: {prev['5h']}% -> {curr_5h}%")
            
            if prev['sem'] is not None and curr_sem is not None:
                if curr_sem < prev['sem']: r['diff_sem'] = "↓"
                elif curr_sem > prev['sem']: r['diff_sem'] = "↑"

        if biz_name in ("A carregar...", "Desconhecido") and r['result'] in ('OK', 'RENOVADO'):
            biz_name = get_business_name(tok, acc_id)

        r['biz_name'] = biz_name
        self.account_history[uid] = {'5h': curr_5h, 'sem': curr_sem, 'biz_name': biz_name}
        return r

    def update_table(self, results):
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        for r in results:
            q = r.get('quota', {})
            pct5h = f"{int(q.get('five_hour_pct', 0))}%" if q.get('five_hour_pct') is not None else "-"
            if r.get('diff_5h'): pct5h += f" {r['diff_5h']}"
            
            rst5h = fmt_remaining(q.get('five_hour_reset'))
            
            pctsem = f"{int(q.get('weekly_pct', 0))}%" if q.get('weekly_pct') is not None else "-"
            if r.get('diff_sem'): pctsem += f" {r['diff_sem']}"

            tag = "ok"
            if r['result'] not in ('OK', 'RENOVADO'):
                tag = "error" if "ERRO" in r['result'] or "MORTO" in r['result'] else "warn"

            self.tree.insert("", tk.END, values=(
                r['label'], r['result'], r['http_status'], 
                pct5h, rst5h, pctsem, r['biz_name'], r['email']
            ), tags=(tag,))

    # --- FLUXO DE LOGIN DEVICE CODE ---
    def open_login_window(self):
        login_win = tk.Toplevel(self.root)
        login_win.title("Injeção de Código (Device Login)")
        login_win.geometry("500x350")
        login_win.configure(bg="#161b22")
        login_win.transient(self.root)

        tk.Label(login_win, text="1. Pedir Código à OpenAI", font=("Segoe UI", 12, "bold"), bg="#161b22", fg="#58a6ff").pack(pady=10)
        
        info_lbl = tk.Label(login_win, text="A gerar token de acesso...", bg="#161b22", fg="#8b949e", font=("Consolas", 10))
        info_lbl.pack(pady=10)
        
        # --- O TRUQUE DO CLIQUE PARA COPIAR ---
        code_lbl = tk.Label(login_win, text="----", font=("Courier New", 24, "bold"), bg="#0d1117", fg="#56d364", width=12, relief=tk.RIDGE, cursor="hand2")
        code_lbl.pack(pady=5)
        
        # Texto de ajuda em baixo
        help_copy = tk.Label(login_win, text="(Clica no código para copiar)", font=("Segoe UI", 8), bg="#161b22", fg="#8b949e")
        help_copy.pack(pady=0)
        
        btn_open = tk.Button(login_win, text="Abrir Browser (auth.openai.com/codex/device)", state=tk.DISABLED, bg="#1f6feb", fg="white", font=("Segoe UI", 10))
        btn_open.pack(pady=15)
        
        status_lbl = tk.Label(login_win, text="", bg="#161b22", fg="#e3b341", font=("Segoe UI", 9))
        status_lbl.pack(pady=5)

        def copy_code_to_clipboard(event):
            c = code_lbl.cget("text")
            if c and c != "----":
                self.root.clipboard_clear()
                self.root.clipboard_append(c)
                status_lbl.config(text="Código copiado para a área de transferência! 📋", fg="#58a6ff")

        # Binda o clique esquerdo do rato à função de copiar
        code_lbl.bind("<Button-1>", copy_code_to_clipboard)

        def login_flow():
            try:
                resp = requests.post("https://auth.openai.com/api/accounts/deviceauth/usercode", json={"client_id": CLIENT_ID}, timeout=10)
                if resp.status_code != 200:
                    info_lbl.config(text="Erro ao gerar código da API.")
                    return
                    
                data = resp.json()
                user_code = data.get("user_code")
                device_auth_id = data.get("device_auth_id")
                verify_url = data.get("verification_uri", "https://auth.openai.com/codex/device")
                
                code_lbl.config(text=user_code)
                info_lbl.config(text="Código pronto:")
                btn_open.config(state=tk.NORMAL, command=lambda: webbrowser.open(verify_url))
                
                max_attempts = 60
                for attempt in range(max_attempts):
                    # Não apaga a mensagem de cópia imediatamente
                    if "copiado" not in status_lbl.cget("text"):
                        status_lbl.config(text=f"A escutar a OpenAI... (Tentativa {attempt+1}/{max_attempts})")
                        
                    poll_resp = requests.post("https://auth.openai.com/api/accounts/deviceauth/token", 
                                              json={"client_id": CLIENT_ID, "device_auth_id": device_auth_id, "user_code": user_code}, timeout=10)
                    
                    if poll_resp.status_code == 200:
                        status_lbl.config(text="Aprovado! A trocar código por Token Final...", fg="#56d364")
                        poll_data = poll_resp.json()
                        auth_code = poll_data.get("authorization_code")
                        code_verifier = poll_data.get("code_verifier")
                        
                        final_resp = requests.post("https://auth.openai.com/oauth/token", data={
                            "grant_type": "authorization_code",
                            "client_id": CLIENT_ID,
                            "code": auth_code,
                            "code_verifier": code_verifier,
                            "redirect_uri": DEVICE_REDIRECT_URI
                        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
                        
                        if final_resp.status_code == 200:
                            tokens = final_resp.json()
                            self.save_new_account(tokens)
                            status_lbl.config(text="SUCESSO! Conta injetada no auth(infinity).json", fg="#56d364")
                            self.log("NOVA CONTA INJETADA VIA DEVICE CODE!")
                            login_win.after(3000, login_win.destroy)
                            return
                    
                    time.sleep(5)
                status_lbl.config(text="Tempo esgotado. Tenta de novo.", fg="#ff7b72")

            except Exception as e:
                status_lbl.config(text=f"Erro de ligação: {e}", fg="#ff7b72")

        threading.Thread(target=login_flow, daemon=True).start()

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
            except: pass
            
        data["credential_pool"]["openai-codex"].append(new_entry)
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

if __name__ == "__main__":
    root = tk.Tk()
    app = BolsaCodexApp(root)
    root.mainloop()