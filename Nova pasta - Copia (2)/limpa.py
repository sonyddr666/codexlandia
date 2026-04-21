#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
from pathlib import Path

# Antídoto contra PowerShell Neandertal
if os.name == 'nt':
    try:
        import ctypes
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except:
        pass

R="\033[91m"; Y="\033[93m"; G="\033[92m"; C="\033[96m"; GR="\033[90m"; B="\033[1m"; X="\033[0m"

TARGET_FILE = "auth(infinityzero).json"

def extract_accounts(data, filename):
    """O Caçador: percebe qualquer mutação de JSON e arranca as contas."""
    accounts = []
    
    # Mutação 1: Formato Hermes (credential_pool)
    if isinstance(data, dict) and 'credential_pool' in data and 'openai-codex' in data['credential_pool']:
        for entry in data['credential_pool']['openai-codex']:
            tok = entry.get('access_token')
            ref = entry.get('refresh_token')
            acc = entry.get('account_id') or entry.get('accountId') or (entry.get('extra') or {}).get('account_id')
            lbl = entry.get('label', f"{filename}_extraida")
            if tok:
                accounts.append({'label': lbl, 'auth_type': 'oauth', 'access_token': tok, 'refresh_token': ref, 'account_id': acc})
        return accounts

    # Mutação 2: Formato aninhado em "tokens" (Teu HTML)
    if isinstance(data, dict) and 'tokens' in data:
        t = data['tokens']
        tok = t.get('access_token') or t.get('access')
        ref = t.get('refresh_token') or t.get('refresh')
        acc = t.get('account_id') or t.get('accountId') or data.get('accountId')
        if tok:
            accounts.append({'label': f"[{filename}]", 'auth_type': 'oauth', 'access_token': tok, 'refresh_token': ref, 'account_id': acc})
        return accounts

    # Mutação 3: Formato flat/plano (Tudo na raiz)
    if isinstance(data, dict) and ('access' in data or 'access_token' in data):
        tok = data.get('access') or data.get('access_token')
        ref = data.get('refresh') or data.get('refresh_token')
        acc = data.get('accountId') or data.get('account_id')
        if tok:
            accounts.append({'label': f"[{filename}]", 'auth_type': 'oauth', 'access_token': tok, 'refresh_token': ref, 'account_id': acc})
        return accounts

    # Mutação 4: Lista solta de contas
    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict) and ('access_token' in item or 'access' in item):
                tok = item.get('access_token') or item.get('access')
                ref = item.get('refresh_token') or item.get('refresh')
                acc = item.get('account_id') or item.get('accountId')
                lbl = item.get('label', f"[{filename}]_{i}")
                if tok:
                    accounts.append({'label': lbl, 'auth_type': 'oauth', 'access_token': tok, 'refresh_token': ref, 'account_id': acc})
        return accounts

    return accounts

def main():
    print(f"{C}{B}╔══ O BURACO NEGRO DO CODEX: infinityzero ══╗{X}\n")
    
    search_dir = Path('.').resolve()
    json_files = list(search_dir.glob('*.json'))
    
    all_accounts = []
    
    # Tenta carregar o auth(infinityzero).json se ele já existir (para não apagar o que lá está)
    if Path(TARGET_FILE).exists():
        try:
            existing_data = json.loads(Path(TARGET_FILE).read_text(encoding='utf-8'))
            all_accounts = extract_accounts(existing_data, TARGET_FILE)
            print(f" {GR}> Base {TARGET_FILE} encontrada com {len(all_accounts)} conta(s) existente(s).{X}")
        except Exception as e:
            print(f" {R}> Erro ao ler a base existente: {e}{X}")

    # Memória de tokens (para não adicionarmos o mesmo Access Token duas vezes)
    seen_tokens = {acc['access_token'] for acc in all_accounts if 'access_token' in acc}
    
    for f in json_files:
        if f.name == TARGET_FILE:
            continue # O buraco negro não se deve engolir a si próprio
            
        try:
            content = f.read_text(encoding='utf-8')
            if not content.strip(): continue
            data = json.loads(content)
            
            extracted = extract_accounts(data, f.name)
            
            added_from_file = 0
            for acc in extracted:
                if acc['access_token'] not in seen_tokens:
                    seen_tokens.add(acc['access_token'])
                    all_accounts.append(acc)
                    added_from_file += 1
            
            if added_from_file > 0:
                print(f" {G}↑ {added_from_file:02d} conta(s){X} sugada(s) do ficheiro {B}{f.name}{X}")
            elif len(extracted) > 0:
                print(f" {Y}≈ {len(extracted):02d} conta(s){X} ignorada(s) em {GR}{f.name} (Já existem na Singularidade){X}")
                
        except json.JSONDecodeError:
            print(f" {R}! {f.name} é um PowerPoint sem alma (JSON quebrado/inválido){X}")
        except Exception as e:
            print(f" {R}! Colapso ao processar {f.name}: {e}{X}")
            
    if not all_accounts:
        print(f"\n{R}Aura vazia. Nenhuma conta encontrada na pasta.{X}")
        return
        
    # Estrutura suprema do Hermes
    final_data = {
        "credential_pool": {
            "openai-codex": all_accounts
        }
    }
    
    # Escreve o buraco negro
    with open(TARGET_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, indent=2)
        
    print(f"\n{C}{B}▶ SUCESSO ABSOLUTO: {len(all_accounts)} contas consolidadas no {TARGET_FILE}!{X}")
    print(f"{GR}Podes apagar o resto dos .json agora. A Singularidade está completa.{X}")

if __name__ == '__main__':
    main()