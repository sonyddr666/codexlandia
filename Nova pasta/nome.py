#!/usr/bin/env python3
import requests
import sys

def extrair_nomes_business(token):
    url = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    print("\nBuscando aura da conta...")
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            accounts = data.get("accounts", {})
            
            if not accounts:
                print("Conta sem workspaces. Vibe de água de ar condicionado.")
                return
                
            for acc_id, info in accounts.items():
                nome = info.get("name") or "Sem Nome (Default)"
                plano = info.get("plan_type", "Desconhecido")
                is_active = info.get("is_active", False)
                
                status = "🟢 Ativa" if is_active else "🔴 Inativa"
                print(f"[{status}] ID: {acc_id} | Plano: {plano.upper()} | Nome: {nome}")
        else:
            print(f"Colapso estético. HTTP {resp.status_code}: {resp.text[:100]}")
            
    except Exception as e:
        print(f"Erro bizarro: {e}")

if __name__ == '__main__':
    print("Cole o access_token (JWT) gigante da conta Codex/ChatGPT:")
    token_input = input("> ").strip()
    
    if not token_input:
        print("Morno. Faltou o token.")
        sys.exit(1)
        
    extrair_nomes_business(token_input)