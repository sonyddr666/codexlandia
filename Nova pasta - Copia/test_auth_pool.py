#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

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

R="\033[91m"; Y="\033[93m"; G="\033[92m"; C="\033[96m"
GR="\033[90m"; B="\033[1m"; X="\033[0m"

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"

TEST_ENDPOINTS = {
    "openai-codex": ("https://chatgpt.com/backend-api/wham/usage", "GET", None),
}

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
    expired = datetime.now(timezone.utc).timestamp() > int(exp)
    mark = (R+"EXPIRADO"+X) if expired else (G+"valido"+X)
    return dt.strftime('%d/%m %H:%M') + ' [' + mark + ']'

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
    """Bate na OpenAI e tenta buscar token novo."""
    if not refresh_token:
        return None
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
    """Caça access e refresh nas mutações do json."""
    tok = None
    ref = None
    acc = None
    
    # Mutação 1: credential_pool
    if isinstance(entry, dict) and 'access_token' in entry:
        tok = entry.get('access_token')
        ref = entry.get('refresh_token')
        acc = entry.get('account_id') or entry.get('accountId') or (entry.get('extra') or {}).get('account_id')
        
    # Mutação 2: Formato 'tokens' solto
    elif isinstance(data, dict) and 'tokens' in data:
        t = data['tokens']
        tok = t.get('access_token') or t.get('access')
        ref = t.get('refresh_token') or t.get('refresh')
        acc = t.get('account_id') or t.get('accountId') or data.get('accountId')

    # Mutação 3: Formato flat
    elif isinstance(data, dict) and 'access' in data:
        tok = data.get('access')
        ref = data.get('refresh')
        acc = data.get('accountId') or data.get('account_id')

    return tok, ref, acc

def update_json_file(file_path, old_access, new_auth_data):
    """Injeta os tokens novos ou apaga se inválido."""
    try:
        content = Path(file_path).read_text(encoding='utf-8')
        data = json.loads(content)
        modified = False
        keep_file = True

        # Se new_auth_data é None, significa que vamos deletar/invalidar
        if isinstance(data, dict):
            # Se for formato credential_pool
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
                            modified = True # Marcado pra deletar
                    else:
                        new_pool.append(acc)
                data['credential_pool']['openai-codex'] = new_pool
                if not new_pool: keep_file = False

            # Se for formato flat (access, refresh na raiz)
            elif 'access' in data and data['access'] == old_access:
                if new_auth_data:
                    data['access'] = new_auth_data.get('access_token')
                    data['refresh'] = new_auth_data.get('refresh_token')
                    if 'expires_in' in new_auth_data:
                        data['expires'] = int(time.time() * 1000) + (new_auth_data['expires_in'] * 1000)
                    modified = True
                else:
                    keep_file = False

            # Se for formato 'tokens' na raiz
            elif 'tokens' in data and isinstance(data['tokens'], dict):
                t = data['tokens']
                if t.get('access_token') == old_access or t.get('access') == old_access:
                    if new_auth_data:
                        if 'access_token' in t: t['access_token'] = new_auth_data.get('access_token')
                        if 'access' in t: t['access'] = new_auth_data.get('access_token')
                        if 'refresh_token' in t: t['refresh_token'] = new_auth_data.get('refresh_token')
                        if 'refresh' in t: t['refresh'] = new_auth_data.get('refresh_token')
                        if 'id_token' in t and 'id_token' in new_auth_data:
                            t['id_token'] = new_auth_data['id_token']
                        modified = True
                    else:
                        keep_file = False

        if not keep_file:
            # Arquivo não tem mais salvação, deleta pra limpar aura
            Path(file_path).unlink(missing_ok=True)
            return "DELETADO"
        elif modified:
            Path(file_path).write_text(json.dumps(data, indent=2), encoding='utf-8')
            return "RENOVADO"
            
    except Exception:
        pass
    return "FALHA_UPDATE"

def test_and_renew(provider, entry, filename, data, raw=False):
    if not isinstance(entry, dict) and provider != 'openai-codex': return None
    
    label = f"[{filename}] {entry.get('label', entry.get('id', '?'))}" if isinstance(entry, dict) else f"[{filename}] mutante"
    
    tok, ref, acc_id = extract_tokens_from_entry(entry, data)
    
    r = dict(label=label, provider=provider, auth_type='oauth', email='-', token_exp='-', quota={}, http_status=None, result='?', error='')
             
    if not tok: 
        r['result'] = 'ERRO'; r['error'] = 'Sem token de acesso'; return r

    claims = decode_jwt(tok)
    r['email'], r['token_exp'] = jwt_email(tok), jwt_exp(tok)
    
    # Caçada do Account ID pra API não dar migué
    if not acc_id: 
        auth_claim = claims.get('https://api.openai.com/auth', {})
        acc_id = auth_claim.get('chatgpt_account_id')

    # Verifica se já expirou no tempo da máquina
    needs_renew = False
    if time.time() > claims.get('exp', 0):
        needs_renew = True
    
    headers = {
        'Authorization': f'Bearer {tok}',
        'Accept': 'application/json',
        'Origin': 'https://chatgpt.com',
        'Referer': 'https://chatgpt.com/',
        'User-Agent': 'Mozilla/5.0'
    }
    if acc_id: headers['ChatGPT-Account-Id'] = acc_id

    url = TEST_ENDPOINTS.get('openai-codex')[0]
    
    if not needs_renew:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            r['http_status'] = resp.status_code
            if raw and resp.status_code == 200: r['raw_body'] = resp.json()
            
            if resp.status_code == 200:
                r['result'] = 'OK'
                q = parse_quota(resp.json())
                r['quota'] = q
                if 'five_hour_pct' not in q:
                    rl_dump = json.dumps(resp.json().get('primary_window', resp.json()))[:150]
                    r['error'] = f"Ainda Vazio. Raio-X: {rl_dump}..."
            elif resp.status_code == 429:
                r['result'] = 'ESGOTADO'; r['error'] = 'HTTP 429'
            elif resp.status_code == 401:
                needs_renew = True # API rejeitou, vamos tentar reviver
            else:
                r['result'] = f'HTTP {resp.status_code}'
        except Exception as e:
            r['result'] = 'ERRO'; r['error'] = str(e)

    # Se a conta pediu água (Expirada ou 401), tentamos o Renew
    if needs_renew:
        r['error'] = "Token inválido/expirado. Tentando RENEW..."
        new_auth = do_renew(ref)
        if new_auth and 'access_token' in new_auth:
            status = update_json_file(filename, tok, new_auth)
            if status == "RENOVADO":
                r['result'] = f'{G}RENOVADO{X}'
                r['error'] = "Token atualizado com sucesso no arquivo!"
                r['token_exp'] = jwt_exp(new_auth['access_token'])
            else:
                r['result'] = f'{Y}RENOVADO (Memória){X}'
                r['error'] = "Renovou na API, mas falhou ao gravar no JSON."
        else:
            status = update_json_file(filename, tok, None)
            if status == "DELETADO":
                r['result'] = f'{R}MORTO & APAGADO{X}'
                r['error'] = "Refresh inválido. Arquivo/conta mandado pro limbo."
            else:
                r['result'] = f'{R}MORTO{X}'
                r['error'] = "Refresh falhou. Apague manualmente."

    return r

def color_pct(pct):
    if pct is None: return GR+'-'.rjust(5)+X
    s = f"{int(round(pct))}%"
    if pct <= 10: return R+B+s.rjust(5)+X
    if pct <= 30: return Y+s.rjust(5)+X
    return G+s.rjust(5)+X

def render(all_results):
    cls()
    now = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    print(C+B+'╔══ Hermes Auth Tester & Auto-Renew · '+now+' ══╗'+X+'\n')
    
    for provider, results in all_results.items():
        if provider != 'openai-codex': continue
        print(B+f'── {provider.upper()} ({len(results)} conta(s)) ' + '─'*60 + X)
        print(f"  {'#':<3} {'label':<38} {'resultado':<20} {'HTTP':>5} {'5h%':>6} {'reset5h':>9}  {'sem%':>6} {'resetSem':>10}  {'email':<28} {'token_exp'}")
        print(f"  {'-'*3} {'-'*38} {'-'*20} {'-'*5} {'-'*6} {'-'*9}  {'-'*6} {'-'*10}  {'-'*28} {'-'*15}")
        
        for i, r in enumerate(results, 1):
            q = r.get('quota', {})
            fh_pct, fh_rst = q.get('five_hour_pct'), q.get('five_hour_reset')
            wk_pct, wk_rst = q.get('weekly_pct'), q.get('weekly_reset')
            
            print(f"  {i:<3} {r['label'][:37]:<38} {r['result']:<20} {str(r['http_status'] or '-'):>5} "
                  f"{color_pct(fh_pct)} {fmt_remaining(fh_rst):>9}  {color_pct(wk_pct)} {fmt_reset_abs(wk_rst)[:10]:>10}  "
                  f"{r['email'][:27]:<28} {r['token_exp']}")
            if r.get('error') and (r['result'] not in ('OK', 'OK*') or 'Raio-X:' in r['error']):
                print(f"     {GR}> {r['error']}{X}")
        print()

def main():
    search_dir = Path('.').resolve()
    json_files = list(search_dir.glob('*.json'))
    all_r = {'openai-codex': []}
    
    for f in json_files:
        try:
            content = f.read_text(encoding='utf-8')
            if not content.strip(): continue
            data = json.loads(content)
            
            # Caça as entradas dependendo da mutação
            entries = []
            if 'credential_pool' in data and 'openai-codex' in data['credential_pool']:
                entries = data['credential_pool']['openai-codex']
            elif 'tokens' in data or 'access' in data:
                entries = [{'label': 'token_extraido', 'auth_type': 'oauth'}]
                
            for entry in entries:
                res = test_and_renew('openai-codex', entry, f.name, data, raw=False)
                if res: all_r['openai-codex'].append(res)
        except: pass
        
    render(all_r)

if __name__ == '__main__':
    main()