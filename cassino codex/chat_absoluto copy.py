#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import base64
import random
import requests
from pathlib import Path
from flask import Flask, request, Response, jsonify

# --- CAÇADOR DE ALMAS (Carrega as contas dos teus JSONs) ---
def decode_jwt_email(token):
    try:
        p = token.split('.')[1]
        p += '=' * (-len(p) % 4)
        claims = json.loads(base64.urlsafe_b64decode(p))
        return claims.get('https://api.openai.com/profile', {}).get('email', 'Desconhecido')
    except:
        return 'Mutante_Sem_Email'

def load_accounts():
    accounts = {}
    for f in Path('.').glob('*.json'):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
            entries = []
            if isinstance(data, dict):
                if 'credential_pool' in data and 'openai-codex' in data['credential_pool']:
                    entries = data['credential_pool']['openai-codex']
                elif 'tokens' in data or 'access' in data:
                    entries = [data]
            elif isinstance(data, list):
                entries = data
                
            for entry in entries:
                tok = entry.get('access_token') or entry.get('access') or (entry.get('tokens') or {}).get('access_token')
                acc_id = entry.get('account_id') or entry.get('accountId') or (entry.get('tokens') or {}).get('account_id')
                if tok:
                    email = decode_jwt_email(tok)
                    # Guarda a conta usando o email como chave
                    accounts[email] = {'token': tok, 'account_id': acc_id}
        except: pass
    return accounts

# --- O FRONTEND (SPA EMBUTIDA NO PYTHON) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Codex OS - Chat Absoluto</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Segoe UI', Consolas, monospace; background: #0d1117; color: #e6edf3; display: flex; height: 100vh; overflow: hidden; }
        
        /* SIDEBAR */
        .sidebar { width: 300px; background: #161b22; border-right: 1px solid #30363d; padding: 20px; display: flex; flex-direction: column; gap: 20px; }
        h1 { color: #58a6ff; font-size: 1.2rem; text-align: center; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
        .control-group { display: flex; flex-direction: column; gap: 8px; }
        label { font-size: 0.85rem; color: #8b949e; font-weight: bold; }
        select, button { background: #0d1117; color: #e6edf3; border: 1px solid #30363d; padding: 10px; border-radius: 6px; outline: none; font-family: inherit; }
        select:focus { border-color: #58a6ff; }
        button { cursor: pointer; transition: 0.2s; font-weight: bold; }
        button:hover { background: #21262d; }
        .btn-primary { background: #238636; color: white; border: none; }
        .btn-primary:hover { background: #2ea043; }
        .btn-danger { background: #da3633; color: white; border: none; }
        .btn-danger:hover { background: #f85149; }
        
        /* CHAT AREA */
        .main { flex: 1; display: flex; flex-direction: column; background: #010409; }
        .chat-history { flex: 1; padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 15px; }
        .msg { max-width: 80%; padding: 15px; border-radius: 12px; line-height: 1.5; font-size: 0.95rem; white-space: pre-wrap; word-break: break-word; }
        .msg.user { background: #1f6feb; color: white; align-self: flex-end; border-bottom-right-radius: 2px; }
        .msg.assistant { background: #21262d; color: #e6edf3; align-self: flex-start; border-bottom-left-radius: 2px; border: 1px solid #30363d; }
        .msg img { max-width: 300px; border-radius: 8px; margin-bottom: 10px; display: block; }
        
        /* INPUT AREA */
        .input-area { background: #161b22; border-top: 1px solid #30363d; padding: 15px 20px; display: flex; flex-direction: column; gap: 10px; }
        .input-row { display: flex; gap: 10px; align-items: flex-end; }
        textarea { flex: 1; background: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 8px; padding: 12px; resize: none; min-height: 50px; max-height: 150px; font-family: inherit; outline: none; }
        textarea:focus { border-color: #58a6ff; }
        .file-btn { background: #30363d; color: #c9d1d9; border: none; padding: 12px 15px; border-radius: 8px; cursor: pointer; }
        .file-btn:hover { background: #8b949e; color: white; }
        
        /* LOADING & ATTACHMENTS */
        #img-preview { display: none; width: 60px; height: 60px; object-fit: cover; border-radius: 6px; border: 2px solid #58a6ff; }
        .status-bar { font-size: 0.8rem; color: #8b949e; text-align: center; }
    </style>
</head>
<body>

    <div class="sidebar">
        <h1>🌌 CODEX OS</h1>
        
        <div class="control-group">
            <label>👤 Conta (Alma)</label>
            <select id="account-selector">
                <option value="random">🎲 Roleta Russa (Aleatório)</option>
                </select>
        </div>

        <div class="control-group">
            <label>🧠 Modelo</label>
            <select id="model-selector">
                <option value="gpt-5.4">gpt-5.4 (Padrão)</option>
                <option value="gpt-5.4-mini">gpt-5.4-mini</option>
                <option value="gpt-5.3-codex">gpt-5.3-codex</option>
                <option value="gpt-5.2">gpt-5.2</option>
            </select>
        </div>

        <div class="control-group" style="margin-top: auto;">
            <button id="btn-stop" class="btn-danger" style="display: none;">🛑 Interromper Geração</button>
            <div class="status-bar" id="status">Aura Estável.</div>
        </div>
    </div>

    <div class="main">
        <div class="chat-history" id="chat">
            <div class="msg assistant">Bem-vindo à Singularidade. Escolhe a conta, o modelo e injeta a tua prompt. Podes colar imagens aqui também.</div>
        </div>
        
        <div class="input-area">
            <div id="attachment-area" style="display: none; align-items: center; gap: 10px;">
                <img id="img-preview" src="" alt="preview">
                <span style="font-size: 0.8rem; color: #ff7b72; cursor: pointer;" onclick="clearImage()">❌ Remover</span>
            </div>
            
            <div class="input-row">
                <button class="file-btn" onclick="document.getElementById('file-input').click()">📎</button>
                <input type="file" id="file-input" style="display: none;" accept="image/*" onchange="handleFile(event)">
                
                <textarea id="prompt" placeholder="Escreve a tua mensagem (Shift+Enter para saltar linha, Enter para enviar)..."></textarea>
                <button class="btn-primary" id="btn-send" style="padding: 0 25px;">Enviar</button>
            </div>
        </div>
    </div>

    <script>
        const chat = document.getElementById('chat');
        const promptIn = document.getElementById('prompt');
        const btnSend = document.getElementById('btn-send');
        const btnStop = document.getElementById('btn-stop');
        const status = document.getElementById('status');
        const imgPreview = document.getElementById('img-preview');
        
        let abortController = null;
        let base64Image = null;

        // Auto-carrega contas disponíveis da API
        fetch('/api/accounts').then(r => r.json()).then(accs => {
            const sel = document.getElementById('account-selector');
            accs.forEach(acc => {
                const opt = document.createElement('option');
                opt.value = acc;
                opt.textContent = acc;
                sel.appendChild(opt);
            });
        });

        // Lidar com Imagens (Botão ou Colar)
        function handleFile(e) { loadFile(e.target.files[0]); }
        
        promptIn.addEventListener('paste', (e) => {
            if(e.clipboardData.files.length > 0) {
                e.preventDefault();
                loadFile(e.clipboardData.files[0]);
            }
        });

        function loadFile(file) {
            if(!file || !file.type.startsWith('image/')) return;
            const reader = new FileReader();
            reader.onload = (e) => {
                base64Image = e.target.result;
                imgPreview.src = base64Image;
                imgPreview.style.display = 'block';
                document.getElementById('attachment-area').style.display = 'flex';
            };
            reader.readAsDataURL(file);
        }

        function clearImage() {
            base64Image = null;
            document.getElementById('attachment-area').style.display = 'none';
            document.getElementById('file-input').value = '';
        }

        // Lógica de Envio
        promptIn.addEventListener('keydown', (e) => {
            if(e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });
        btnSend.addEventListener('click', sendMessage);

        btnStop.addEventListener('click', () => {
            if(abortController) {
                abortController.abort();
                status.innerText = "⚠️ Geração interrompida.";
                cleanupUI();
            }
        });

        function appendMessage(role, text, img = null) {
            const div = document.createElement('div');
            div.className = `msg ${role}`;
            let content = '';
            if(img) content += `<img src="${img}">`;
            content += `<span class="text-content">${text}</span>`;
            div.innerHTML = content;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
            return div.querySelector('.text-content');
        }

        async function sendMessage() {
            const text = promptIn.value.trim();
            if(!text && !base64Image) return;
            
            appendMessage('user', text, base64Image);
            
            const payload = {
                model: document.getElementById('model-selector').value,
                account: document.getElementById('account-selector').value,
                text: text,
                image: base64Image
            };

            promptIn.value = '';
            clearImage();
            
            const responseNode = appendMessage('assistant', '...');
            
            btnSend.disabled = true;
            btnStop.style.display = 'block';
            status.innerText = "A extrair alma da API...";
            
            abortController = new AbortController();
            
            try {
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                    signal: abortController.signal
                });

                if(!res.ok) {
                    const err = await res.text();
                    responseNode.innerText = `[Colapso Estético: HTTP ${res.status}] ${err}`;
                    cleanupUI();
                    return;
                }

                // Leitor de Stream SSE (Magia Negra do Proxy)
                const reader = res.body.getReader();
                const decoder = new TextDecoder("utf-8");
                responseNode.innerText = '';
                
                while(true) {
                    const {done, value} = await reader.read();
                    if(done) break;
                    
                    const chunk = decoder.decode(value, {stream: true});
                    const lines = chunk.split('\\n');
                    
                    for(let line of lines) {
                        if(line.startsWith('data: ') && line !== 'data: [DONE]') {
                            try {
                                const data = JSON.parse(line.slice(6));
                                // Lida com o formato padrão OpenAI OU o formato Codex do README
                                if(data.type === "response.output_text.delta" && data.delta) {
                                    responseNode.innerText += data.delta; // Codex SSE
                                } else if (data.choices && data.choices[0].delta && data.choices[0].delta.content) {
                                    responseNode.innerText += data.choices[0].delta.content; // Normal OpenAI SSE
                                }
                                chat.scrollTop = chat.scrollHeight;
                            } catch(e) {}
                        }
                    }
                }
                status.innerText = "Geração concluída com sucesso.";
            } catch(e) {
                if(e.name !== 'AbortError') {
                    responseNode.innerText += `\\n[Erro Bizarro: ${e.message}]`;
                    status.innerText = "Falha na Singularidade.";
                }
            }
            cleanupUI();
        }
        
        function cleanupUI() {
            btnSend.disabled = false;
            btnStop.style.display = 'none';
            abortController = null;
        }
    </script>
</body>
</html>
"""

# --- O BACKEND (FLASK PROXY) ---
app = Flask(__name__)
ACCOUNTS_POOL = {}

@app.route('/')
def index():
    return HTML_TEMPLATE

@app.route('/api/accounts')
def get_accounts():
    global ACCOUNTS_POOL
    ACCOUNTS_POOL = load_accounts() # Atualiza em tempo real
    return jsonify(list(ACCOUNTS_POOL.keys()))

@app.route('/api/chat', methods=['POST'])
def chat_proxy():
    global ACCOUNTS_POOL
    if not ACCOUNTS_POOL: ACCOUNTS_POOL = load_accounts()
    if not ACCOUNTS_POOL: return "Nenhuma conta carregada. Vibe de fantasma.", 400

    req = request.json
    acc_key = req.get('account')
    
    # Roleta Russa
    if acc_key == 'random' or acc_key not in ACCOUNTS_POOL:
        acc_key = random.choice(list(ACCOUNTS_POOL.keys()))
        
    auth_data = ACCOUNTS_POOL[acc_key]
    token = auth_data['token']
    acc_id = auth_data['account_id']
    
    # Monta a Payload estilo Codex (do teu README)
    messages = []
    
    # Suporte a Imagem Multimodal
    if req.get('image'):
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": req.get('text', '')},
                {"type": "image_url", "image_url": {"url": req.get('image')}}
            ]
        })
    else:
        messages.append({"role": "user", "content": req.get('text', '')})

    # Usando o formato padrão de Completions que suporta SSE nativamente
    # (Adaptado para garantir que a stream funcione impecável no browser)
    api_url = "https://chatgpt.com/backend-api/codex/responses" # Podes mudar para /v1/chat/completions se a API refilar
    
    payload = {
        "model": req.get('model', 'gpt-5.4'),
        "instructions": "You are a helpful assistant.",
        "input": messages, # Formato Codex usa "input", se der 400 muda para "messages"
        "store": True,
        "stream": True
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"
    }
    if acc_id: headers["ChatGPT-Account-Id"] = acc_id

    print(f"[PROXY] A enviar prompt via {acc_key}...")

    def generate():
        # Faz o Proxy do SSE diretamente da OpenAI para o teu Browser
        with requests.post(api_url, json=payload, headers=headers, stream=True) as r:
            if r.status_code != 200:
                yield f"data: {{\"type\":\"response.output_text.delta\",\"delta\":\"[HTTP {r.status_code}] Falha na API. O formato Codex pode ter mudado ou o Token morreu.\"}}\n\n"
                return
                
            for chunk in r.iter_lines():
                if chunk:
                    # Empurra o chunk SSE do Python para o Frontend
                    yield chunk.decode('utf-8') + "\n\n"

    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    print(f"\n🌌 CODEX OS PROXY ATIVADO 🌌")
    print("A carregar almas na Singularidade...")
    ACCOUNTS_POOL = load_accounts()
    print(f"[{len(ACCOUNTS_POOL)}] Contas carregadas com sucesso.")
    print("Abra o browser em: http://127.0.0.1:3000\n")
    
    # Corre na porta 3000 como o teu README pedia
    app.run(host='0.0.0.0', port=3000, threaded=True)