#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, json, os, re, sys, time
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
    print("pip install requests", file=sys.stderr)
    sys.exit(1)

R="\033[91m"; Y="\033[93m"; G="\033[92m"; C="\033[96m"; M="\033[95m"
GR="\033[90m"; B="\033[1m"; X="\033[0m"

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
LOG_FILE = "hermes_monitor.log"

TEST_ENDPOINTS = {
    "openai-codex": ("https://chatgpt.com/backend-api/wham/usage", "GET", None),
}

# --- SISTEMA DE REGISTO E MEMÓRIA ---
live_logs = []
account_history = {} # Guarda o estado do último tick e o nome da Business

def add_log(msg):
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_line = f"[{timestamp}] {msg}"
    live_logs.append(log_line)
    if len(live_logs) > 15: 
        live_logs.pop(0)
    
    # Caixa Preta (Ficheiro de log)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        clean_msg = re.sub(r'\x1b\[[0-9;]*[mK]', '', log_line)
        f.write(clean_msg + "\n")

def cls():
    os.system("cls" if os.name == "nt" else "clear")

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
    return (str(h)+"h"+f"{m:02d}"+"m") if h else (str(m)+"m")

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

def get_business_name(token, target_acc_id):
    """X-9 que vai à OpenAI buscar o nome do Workspace/Business"""
    url = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            accs = resp.json().get("accounts", {})
            if target_acc_id and target_acc_id in accs:
                return accs[target_acc_id].get("name") or "Personal"
            # Se não achou pelo ID, pega a ativa
            for k, v in accs.items():
                if v.get("is_active"):
                    return v.get("name") or "Personal"
    except:
        pass
    return "Desconhecido"

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

            elif 'tokens' in data and isinstance(data['tokens'], dict):
                t = data['tokens']
                if t.get('access_token') == old_access or t.get('access') == old_access:
                    if new_auth_data:
                        if 'access_token' in t: t['access_token'] = new_auth_data.get('access_token')
                        if 'access' in t: t['access'] = new_auth_data.get('access_token')
                        if 'refresh_token' in t: t['refresh_token'] = new_auth_data.get('refresh_token')
                        if 'refresh' in t: t['refresh'] = new_auth_data.get('refresh_token')
                        if 'id_token' in t and 'id_token' in new_auth_data: t['id_token'] = new_auth_data['id_token']
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

def test_and_renew(provider, entry, filename, data):
    if not isinstance(entry, dict) and provider != 'openai-codex': return None
    
    raw_label = entry.get('label', entry.get('id', 'mutante')) if isinstance(entry, dict) else 'mutante'
    label = f"[{filename}] {raw_label}"
    
    tok, ref, acc_id = extract_tokens_from_entry(entry, data)
    r = dict(label=label, provider=provider, email='-', token_exp='-', quota={}, http_status=None, result='?', error='', diff_5h='', diff_sem='', diff_http='', biz_name='A carregar...')
             
    if not tok: 
        r['result'] = 'ERRO'; r['error'] = 'Sem token'; return r

    claims = decode_jwt(tok)
    r['email'], r['token_exp'] = jwt_email(tok), jwt_exp(tok)
    
    if not acc_id: 
        acc_id = claims.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')

    needs_renew = False
    if time.time() > claims.get('exp', 0): needs_renew = True
    
    headers = {
        'Authorization': f'Bearer {tok}',
        'Accept': 'application/json',
        'Origin': 'https://chatgpt.com',
        'User-Agent': 'Mozilla/5.0'
    }
    if acc_id: headers['ChatGPT-Account-Id'] = acc_id

    url = TEST_ENDPOINTS.get('openai-codex')[0]
    
    if not needs_renew:
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            r['http_status'] = resp.status_code
            
            if resp.status_code == 200:
                r['result'] = 'OK'
                q = parse_quota(resp.json())
                r['quota'] = q
            elif resp.status_code == 429:
                r['result'] = 'ESGOTADO'; r['error'] = 'HTTP 429'
            elif resp.status_code == 401:
                needs_renew = True
            else:
                r['result'] = f'HTTP {resp.status_code}'
        except Exception as e:
            r['result'] = 'ERRO'; r['error'] = str(e)

    if needs_renew:
        new_auth = do_renew(ref)
        if new_auth and 'access_token' in new_auth:
            status = update_json_file(filename, tok, new_auth)
            if status == "RENOVADO":
                r['result'] = f'{G}RENOVADO{X}'
                r['token_exp'] = jwt_exp(new_auth['access_token'])
                add_log(f"{G}AUTO-RENEW:{X} {r['email']} ({filename}) renovada com sucesso.")
            else:
                r['result'] = f'{Y}RENOVADO (Mem){X}'
        else:
            status = update_json_file(filename, tok, None)
            if status == "DELETADO":
                r['result'] = f'{R}APAGADO{X}'
                add_log(f"{R}MORTO:{X} {r['email']} ({filename}) token morreu e o ficheiro foi pro ralo.")
            else:
                r['result'] = f'{R}MORTO{X}'

    curr_5h = r['quota'].get('five_hour_pct')
    curr_sem = r['quota'].get('weekly_pct')
    curr_http = r['http_status']
    uid = f"{filename}_{r['email']}"
    
    # --- Gestão de Memória: Tracking e Workspace Name ---
    biz_name = "A carregar..."
    if uid in account_history:
        prev = account_history[uid]
        biz_name = prev.get('biz_name', biz_name)
        
        if prev['http'] != curr_http and curr_http is not None:
            r['diff_http'] = f" (era {prev['http']})"
            add_log(f"{Y}STATUS MUDOU:{X} {r['email']} passou de HTTP {prev['http']} para {curr_http}")

        if prev['5h'] is not None and curr_5h is not None:
            if curr_5h < prev['5h']:
                r['diff_5h'] = f" {R}↓{X}"
                add_log(f"{M}CONSUMO:{X} {r['email']} gastou cota 5H: {prev['5h']}% -> {curr_5h}%")
            elif curr_5h > prev['5h']:
                r['diff_5h'] = f" {G}↑{X}"
                add_log(f"{G}RESET:{X} {r['email']} cota 5H renovou: {prev['5h']}% -> {curr_5h}%")
                
        if prev['sem'] is not None and curr_sem is not None:
            if curr_sem < prev['sem']:
                r['diff_sem'] = f" {R}↓{X}"
            elif curr_sem > prev['sem']:
                r['diff_sem'] = f" {G}↑{X}"
                
    # Se ainda não temos o nome da Business e a conta está válida, vamos buscar!
    if biz_name in ("A carregar...", "Desconhecido") and r['result'] in ('OK', 'OK*'):
        biz_name = get_business_name(tok, acc_id)

    r['biz_name'] = biz_name
    account_history[uid] = {'5h': curr_5h, 'sem': curr_sem, 'http': curr_http, 'biz_name': biz_name}
    return r

def color_pct_diff(pct, diff_arrow):
    if pct is None: return GR+'-'.rjust(8)+X + "   "
    s = f"{int(round(pct))}%"
    if pct <= 10: base = R+B+s.rjust(4)+X
    elif pct <= 30: base = Y+s.rjust(4)+X
    else: base = G+s.rjust(4)+X
    arrow = diff_arrow if diff_arrow else "   " 
    return base + arrow

def render(all_results, time_left):
    cls()
    now = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    print(C+B+f'╔══ Bolsa de Valores do Codex · {now} · Fim em: {time_left}s ══╗'+X+'\n')
    
    for provider, results in all_results.items():
        if provider != 'openai-codex': continue
        print(B+f'── {provider.upper()} ({len(results)} conta(s)) ' + '─'*85 + X)
        print(f"  {'#':<3} {'label':<24} {'resultado':<18} {'HTTP':>8} {'5h%':>12} {'reset':>6}  {'sem%':>12} {'reset':>6}  {'Workspace':<15} {'email':<23}")
        print(f"  {'-'*3} {'-'*24} {'-'*18} {'-'*8} {'-'*12} {'-'*6}  {'-'*12} {'-'*6}  {'-'*15} {'-'*23}")
        
        for i, r in enumerate(results, 1):
            q = r.get('quota', {})
            fh_pct, fh_rst = q.get('five_hour_pct'), q.get('five_hour_reset')
            wk_pct, wk_rst = q.get('weekly_pct'), q.get('weekly_reset')
            
            http_str = str(r['http_status'] or '-') + r['diff_http']
            c_5h = color_pct_diff(fh_pct, r['diff_5h'])
            c_sem = color_pct_diff(wk_pct, r['diff_sem'])
            
            print(f"  {i:<3} {r['label'][:23]:<24} {r['result']:<18} {http_str:>8} "
                  f"{c_5h:>21} {fmt_remaining(fh_rst):>6}  {c_sem:>21} {fmt_reset_abs(wk_rst)[:5]:>6}  "
                  f"{r['biz_name'][:14]:<15} {r['email'][:22]:<23}")
            
        print()
        
    print(C+B+'── LIVE LOGS ' + '─'*100 + X)
    if not live_logs:
        print(f"  {GR}Nenhuma alteração detetada ainda... A aguardar.{X}")
    else:
        for log in live_logs:
            print(f"  {log}")
    print()

def main():
    search_dir = Path('.').resolve()
    
    total_duration = 300 # 5 minutos
    interval = 5 # 5 segundos
    start_time = time.time()
    
    add_log(f"{C}SISTEMA INICIADO:{X} Monitorização ativa por 5 minutos.")
    
    while True:
        elapsed = time.time() - start_time
        if elapsed > total_duration:
            add_log(f"{C}SISTEMA ENCERRADO:{X} 5 minutos concluídos.")
            break
            
        time_left = int(total_duration - elapsed)
        json_files = list(search_dir.glob('*.json'))
        all_r = {'openai-codex': []}
        
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
                    res = test_and_renew('openai-codex', entry, f.name, data)
                    if res: all_r['openai-codex'].append(res)
            except: pass
            
        render(all_r, time_left)
        time.sleep(interval)

if __name__ == '__main__':
    main()