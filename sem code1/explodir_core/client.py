from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import requests
except ImportError:  # pragma: no cover - exercised indirectly in tests without requests installed
    requests = None

from .models import ApiResponse, BrowserLoginSession, DeviceCodeSession, DevicePollResult

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ACCOUNTS_URL = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
OAUTH_URL = "https://auth.openai.com/oauth/token"
DEVICE_USER_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
OAUTH_SCOPE = "openid profile email offline_access"
CALLBACK_PATH = "/auth/callback"


class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


class OpenAIAPIError(RuntimeError):
    pass


class OpenAITransportError(OpenAIAPIError):
    pass


class OpenAIAuthClient:
    def __init__(self, session: Any | None = None, timeout: int = 10) -> None:
        if session is None and requests is None:
            raise OpenAIAPIError("Dependencia ausente: instale 'requests' com 'pip install requests'.")
        self._custom_session = session is not None
        self.session = session or requests.Session()
        self.timeout = timeout

    def _request(self, method: str, url: str, **kwargs: Any) -> ApiResponse:
        kwargs.setdefault("timeout", self.timeout)
        try:
            response = self.session.request(method, url, **kwargs)
        except Exception as exc:
            if requests is not None and not isinstance(exc, requests.RequestException):
                raise
            raise OpenAITransportError(str(exc)) from exc
        try:
            json_body = response.json()
        except ValueError:
            json_body = None
        try:
            text = response.text
        except Exception:
            text = ""
        return ApiResponse(status_code=response.status_code, json_body=json_body, text=text)

    def get_usage(self, access_token: str, account_id: str | None) -> ApiResponse:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Origin": "https://chatgpt.com",
            "User-Agent": "Mozilla/5.0",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        return self._request("GET", USAGE_URL, headers=headers)

    def get_workspace_name(self, access_token: str, account_id: str | None) -> str | None:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "Mozilla/5.0",
        }
        response = self._request("GET", ACCOUNTS_URL, headers=headers)
        if response.status_code != 200 or not isinstance(response.json_body, dict):
            return None
        accounts = response.json_body.get("accounts", {})
        if not isinstance(accounts, dict):
            return None
        if account_id and account_id in accounts and isinstance(accounts[account_id], dict):
            name = accounts[account_id].get("name")
            if isinstance(name, str) and name:
                return name
        for value in accounts.values():
            if isinstance(value, dict) and value.get("is_active"):
                name = value.get("name")
                if isinstance(name, str) and name:
                    return name
        return None

    def renew_token(self, refresh_token: str) -> dict[str, Any]:
        payload = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "refresh_token": refresh_token,
        }
        response = self._request(
            "POST",
            OAUTH_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200 or not isinstance(response.json_body, dict):
            raise OpenAIAPIError(f"Renew falhou com HTTP {response.status_code}")
        return response.json_body

    def create_browser_login_session(self) -> BrowserLoginSession:
        code_verifier = self._generate_code_verifier()
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "code_challenge": self._pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": secrets.token_urlsafe(32),
        }
        return BrowserLoginSession(
            authorize_url=f"{AUTHORIZE_URL}?{urlencode(params)}",
            state=str(params["state"]),
            code_verifier=code_verifier,
        )

    def wait_for_browser_callback(
        self,
        session_data: BrowserLoginSession,
        cancel_event: Event | None = None,
        timeout_seconds: int = 300,
    ) -> str:
        result: dict[str, str] = {}
        server: _ReusableHTTPServer | None = None

        client = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                if parsed.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    result["code"] = client.parse_browser_callback(self.path, session_data.state)
                    self._write_page(
                        200,
                        "Login concluido",
                        "Conta autorizada. Pode fechar esta pagina e voltar ao app.",
                    )
                except OpenAIAPIError as exc:
                    result["error"] = str(exc)
                    self._write_page(400, "Falha no login", str(exc))

            def log_message(self, format: str, *args: object) -> None:
                return

            def _write_page(self, status: int, title: str, message: str) -> None:
                html = (
                    "<html><head><meta charset='utf-8'><title>{title}</title></head>"
                    "<body style=\"font-family:Segoe UI,Arial,sans-serif;padding:24px;\">"
                    "<h2>{title}</h2><p>{message}</p></body></html>"
                ).format(title=title, message=message)
                encoded = html.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        try:
            server = _ReusableHTTPServer(("127.0.0.1", 1455), CallbackHandler)
        except OSError as exc:
            raise OpenAIAPIError(
                "Nao consegui escutar em http://localhost:1455/auth/callback. "
                "Feche o processo que estiver a usar a porta 1455 e tente novamente."
            ) from exc

        server.timeout = 0.5
        deadline = time.time() + timeout_seconds
        try:
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise OpenAIAPIError("Fluxo cancelado.")
                server.handle_request()
                if "error" in result:
                    raise OpenAIAPIError(result["error"])
                code = result.get("code")
                if code:
                    return code
            raise OpenAIAPIError("Tempo esgotado a aguardar a autenticacao no browser.")
        finally:
            server.server_close()

    def exchange_browser_code(self, authorization_code: str, code_verifier: str) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": authorization_code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
        }
        response = self._request(
            "POST",
            OAUTH_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200 or not isinstance(response.json_body, dict):
            error_text = response.text
            if response.json_body is not None:
                try:
                    error_text = json.dumps(response.json_body)
                except TypeError:
                    pass
            raise OpenAIAPIError(f"Troca de codigo falhou: {error_text}")
        return response.json_body

    def request_device_code(self) -> DeviceCodeSession:
        response = self._post_device_auth(DEVICE_USER_CODE_URL, json={"client_id": CLIENT_ID})
        if response.status_code != 200 or not isinstance(response.json_body, dict):
            raise OpenAIAPIError(f"Device code falhou com HTTP {response.status_code}")
        body = response.json_body
        user_code = body.get("user_code")
        device_auth_id = body.get("device_auth_id")
        verify_url = body.get("verification_uri", "https://auth.openai.com/codex/device")
        if not isinstance(user_code, str) or not isinstance(device_auth_id, str):
            raise OpenAIAPIError("Resposta invalida ao pedir device code")
        interval = body.get("interval", 5)
        expires_in = body.get("expires_in", 300)
        return DeviceCodeSession(
            user_code=user_code,
            device_auth_id=device_auth_id,
            verification_uri=str(verify_url),
            interval_seconds=int(interval) if isinstance(interval, (int, float)) else 5,
            expires_in_seconds=int(expires_in) if isinstance(expires_in, (int, float)) else 300,
        )

    def poll_device_token(self, session_data: DeviceCodeSession) -> DevicePollResult:
        payload = {
            "client_id": CLIENT_ID,
            "device_auth_id": session_data.device_auth_id,
            "user_code": session_data.user_code,
        }
        response = self._post_device_auth(DEVICE_TOKEN_URL, json=payload)
        if response.status_code == 200 and isinstance(response.json_body, dict):
            body = response.json_body
            authorization_code = body.get("authorization_code")
            code_verifier = body.get("code_verifier")
            if isinstance(authorization_code, str) and isinstance(code_verifier, str):
                return DevicePollResult(
                    status="approved",
                    message="Aprovado",
                    authorization_code=authorization_code,
                    code_verifier=code_verifier,
                )
            return DevicePollResult(status="error", message="Resposta invalida na aprovacao")

        error_code = ""
        message = response.text or f"HTTP {response.status_code}"
        if isinstance(response.json_body, dict):
            nested_error = response.json_body.get("error")
            if isinstance(nested_error, dict):
                maybe_error = nested_error.get("code") or nested_error.get("type")
                maybe_message = nested_error.get("message") or nested_error.get("error_description")
                if isinstance(maybe_error, str):
                    error_code = maybe_error
                if isinstance(maybe_message, str) and maybe_message:
                    message = maybe_message
            else:
                maybe_error = response.json_body.get("error") or response.json_body.get("code")
                maybe_message = response.json_body.get("error_description") or response.json_body.get("message")
                if isinstance(maybe_error, str):
                    error_code = maybe_error
                if isinstance(maybe_message, str) and maybe_message:
                    message = maybe_message

        if error_code in {"authorization_pending", "pending", "deviceauth_authorization_unknown"}:
            return DevicePollResult(status="pending", message=message, error_code=error_code or None)
        if error_code == "slow_down":
            return DevicePollResult(status="slow_down", message=message, error_code=error_code)
        if error_code in {"access_denied", "authorization_declined"}:
            return DevicePollResult(status="denied", message=message, error_code=error_code)
        if error_code in {"expired_token", "code_expired"}:
            return DevicePollResult(status="expired", message=message, error_code=error_code)
        return DevicePollResult(status="error", message=message, error_code=error_code or None)

    def exchange_device_code(self, authorization_code: str, code_verifier: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": authorization_code,
            "redirect_uri": DEVICE_REDIRECT_URI,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        response = self._post_device_auth(
            OAUTH_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200 or not isinstance(response.json_body, dict):
            error_text = response.text
            if response.json_body is not None:
                try:
                    error_text = json.dumps(response.json_body)
                except TypeError:
                    pass
            raise OpenAIAPIError(f"Troca de codigo falhou: {error_text}")
        return response.json_body

    def exchange_callback_url(self, callback_value: str) -> dict[str, Any]:
        authorization_code = self.extract_authorization_code(callback_value)
        return self.exchange_device_code(authorization_code, "")

    def parse_browser_callback(self, callback_value: str, expected_state: str | None = None) -> str:
        value = callback_value.strip()
        if not value:
            raise OpenAIAPIError("Callback vazio.")
        if "://" not in value:
            if value.startswith("/"):
                value = f"http://localhost:1455{value}"
            else:
                value = f"http://localhost:1455/auth/callback?{value}"

        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        if "error" in query:
            error_message = query.get("error_description", query["error"])[0]
            raise OpenAIAPIError(str(error_message))
        if expected_state:
            state = query.get("state", [""])[0]
            if state != expected_state:
                raise OpenAIAPIError("State invalido no retorno do browser.")
        code_values = query.get("code")
        if code_values and code_values[0]:
            return code_values[0]
        raise OpenAIAPIError("Nao encontrei o parametro 'code' no callback.")

    def extract_authorization_code(self, callback_value: str) -> str:
        value = callback_value.strip()
        if not value:
            raise OpenAIAPIError("Cole a URL de callback ou o codigo retornado pelo browser.")
        if "://" not in value:
            return value

        parsed = urlparse(value)
        query = parse_qs(parsed.query)
        if "error" in query:
            error_message = query.get("error_description", query["error"])[0]
            raise OpenAIAPIError(str(error_message))
        code_values = query.get("code")
        if code_values and code_values[0]:
            return code_values[0]
        raise OpenAIAPIError("Nao encontrei o parametro 'code' na URL de callback.")

    def _post_device_auth(self, url: str, **kwargs: Any) -> ApiResponse:
        kwargs.setdefault("timeout", self.timeout)
        if self._custom_session or requests is None:
            return self._request("POST", url, **kwargs)
        try:
            response = requests.post(url, **kwargs)
        except requests.RequestException as exc:
            raise OpenAITransportError(str(exc)) from exc
        try:
            json_body = response.json()
        except ValueError:
            json_body = None
        return ApiResponse(status_code=response.status_code, json_body=json_body, text=response.text)

    def _generate_code_verifier(self) -> str:
        token = secrets.token_urlsafe(64)
        return token[:96]

    def _pkce_challenge(self, code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
