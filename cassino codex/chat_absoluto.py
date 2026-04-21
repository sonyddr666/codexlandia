#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import base64
import copy
import json
import mimetypes
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import requests
except ImportError:
    print("Erro: instale a dependencia 'requests' com: pip install requests")
    raise

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    from PyQt6.QtCore import QBuffer, QIODevice, QSize, Qt, QThread, pyqtSignal
except ImportError:
    print("Erro: instale a dependencia 'PyQt6' com: pip install PyQt6")
    raise


APP_NAME = "Chat Absoluto Codex"
CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
RANDOM_ACCOUNT_ID = "__RANDOM_ACCOUNT__"
REQUEST_TIMEOUT = (20, 300)
MAX_TEXT_ATTACHMENT_BYTES = 500_000
MAX_IMAGE_ATTACHMENT_BYTES = 12 * 1024 * 1024
DEFAULT_MODELS = [
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.2",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex-mini",
]
TEXT_LIKE_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".csv",
    ".tsv",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".java",
    ".kt",
    ".c",
    ".cpp",
    ".cc",
    ".h",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".php",
    ".rb",
    ".swift",
    ".sql",
    ".sh",
    ".bat",
    ".ps1",
    ".dockerfile",
    ".gitignore",
    ".log",
    ".diff",
    ".patch",
}
LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".xml": "xml",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
    ".ps1": "powershell",
    ".sql": "sql",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".php": "php",
    ".rb": "ruby",
}
BASE_INSTRUCTIONS = (
    "You are a helpful assistant. "
    "When the user writes in Brazilian Portuguese, answer in Brazilian Portuguese. "
    "If files are attached as text blocks, treat them as part of the user's context."
)


def decode_jwt(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def jwt_email(token: str) -> str:
    claims = decode_jwt(token)
    profile = claims.get("https://api.openai.com/profile", {})
    return profile.get("email") or claims.get("email") or "Mutante_Sem_Email"


def jwt_exp_epoch(token: str) -> Optional[float]:
    claims = decode_jwt(token)
    exp = claims.get("exp")
    try:
        return float(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def normalize_epoch(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric


def ellipsis(text: str, size: int = 80) -> str:
    text = " ".join((text or "").split())
    if len(text) <= size:
        return text
    return text[: max(0, size - 1)] + "..."


def human_time(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def guess_language(filename: str) -> str:
    return LANGUAGE_BY_EXTENSION.get(Path(filename).suffix.lower(), "")


def bytes_to_data_url(mime_type: str, raw: bytes) -> str:
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def qimage_to_png_bytes(image: QtGui.QImage) -> bytes:
    if image.isNull():
        return b""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def image_from_mime_data(mime_data: QtCore.QMimeData) -> Optional[QtGui.QImage]:
    if not mime_data.hasImage():
        return None
    raw = mime_data.imageData()
    if isinstance(raw, QtGui.QImage):
        return raw
    if isinstance(raw, QtGui.QPixmap):
        return raw.toImage()
    return None


def looks_like_text_file(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type and (
        mime_type.startswith("text/")
        or mime_type in {
            "application/json",
            "application/xml",
            "application/javascript",
            "application/x-sh",
        }
    ):
        return True
    suffix = path.suffix.lower()
    return suffix in TEXT_LIKE_EXTENSIONS or path.name.lower() in {"dockerfile", ".gitignore"}


def extract_pdf_text(path: Path) -> Optional[str]:
    try:
        from pypdf import PdfReader
    except Exception:
        return None

    try:
        reader = PdfReader(str(path))
        text_parts: list[str] = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        text = "\n\n".join(part.strip() for part in text_parts if part.strip())
        return text or None
    except Exception:
        return None


def safe_json_load(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def do_renew(refresh_token: Optional[str]) -> Optional[dict[str, Any]]:
    if not refresh_token:
        return None
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "refresh_token": refresh_token,
    }
    try:
        response = requests.post(
            "https://auth.openai.com/oauth/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def update_json_file_token(file_path: str, old_access: str, new_auth: dict[str, Any]) -> bool:
    path = Path(file_path)
    data = safe_json_load(path)
    if data is None:
        return False

    updated = False
    expires_ms = None
    if new_auth.get("expires_in"):
        try:
            expires_ms = int((time.time() + int(new_auth["expires_in"])) * 1000)
        except Exception:
            expires_ms = None

    def patch_token_dict(obj: dict[str, Any], use_access_token: bool) -> None:
        nonlocal updated
        if use_access_token:
            obj["access_token"] = new_auth.get("access_token", obj.get("access_token"))
            if new_auth.get("refresh_token"):
                obj["refresh_token"] = new_auth["refresh_token"]
        else:
            obj["access"] = new_auth.get("access_token", obj.get("access"))
            if new_auth.get("refresh_token"):
                obj["refresh"] = new_auth["refresh_token"]
        if new_auth.get("id_token"):
            obj["id_token"] = new_auth["id_token"]
        if expires_ms is not None:
            obj["expires"] = expires_ms
        updated = True

    def walk(obj: Any) -> None:
        nonlocal updated
        if updated:
            return
        if isinstance(obj, dict):
            if obj.get("access_token") == old_access:
                patch_token_dict(obj, True)
                return
            if obj.get("access") == old_access:
                patch_token_dict(obj, False)
                return
            tokens = obj.get("tokens")
            if isinstance(tokens, dict):
                if tokens.get("access_token") == old_access:
                    patch_token_dict(tokens, True)
                    if expires_ms is not None:
                        obj["expires"] = expires_ms
                    return
                if tokens.get("access") == old_access:
                    patch_token_dict(tokens, False)
                    if expires_ms is not None:
                        obj["expires"] = expires_ms
                    return
            for value in obj.values():
                walk(value)
                if updated:
                    return
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
                if updated:
                    return

    walk(data)
    if not updated:
        return False

    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def extract_auth_fields(
    entry: dict[str, Any], root: Any
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[float], str]:
    tokens = entry.get("tokens") if isinstance(entry.get("tokens"), dict) else {}
    root_tokens = root.get("tokens") if isinstance(root, dict) and isinstance(root.get("tokens"), dict) else {}

    access = (
        entry.get("access_token")
        or entry.get("access")
        or tokens.get("access_token")
        or tokens.get("access")
        or root_tokens.get("access_token")
        or root_tokens.get("access")
        or (root.get("access") if isinstance(root, dict) else None)
        or (root.get("access_token") if isinstance(root, dict) else None)
    )
    refresh = (
        entry.get("refresh_token")
        or entry.get("refresh")
        or tokens.get("refresh_token")
        or tokens.get("refresh")
        or root_tokens.get("refresh_token")
        or root_tokens.get("refresh")
        or (root.get("refresh") if isinstance(root, dict) else None)
        or (root.get("refresh_token") if isinstance(root, dict) else None)
    )
    account_id = (
        entry.get("account_id")
        or entry.get("accountId")
        or tokens.get("account_id")
        or tokens.get("accountId")
        or root_tokens.get("account_id")
        or root_tokens.get("accountId")
        or (root.get("accountId") if isinstance(root, dict) else None)
        or (root.get("account_id") if isinstance(root, dict) else None)
    )
    expires = normalize_epoch(
        entry.get("expires")
        or entry.get("expires_at")
        or tokens.get("expires")
        or root_tokens.get("expires")
        or (root.get("expires") if isinstance(root, dict) else None)
    )
    label = entry.get("label") or entry.get("id") or entry.get("name") or ""
    return access, refresh, account_id, expires, label


def iter_auth_entries(data: Any) -> Iterable[tuple[int, dict[str, Any], Any]]:
    if isinstance(data, dict):
        credential_pool = data.get("credential_pool")
        if isinstance(credential_pool, dict) and isinstance(credential_pool.get("openai-codex"), list):
            for index, entry in enumerate(credential_pool["openai-codex"]):
                if isinstance(entry, dict):
                    yield index, entry, data
            return
        if any(key in data for key in ("tokens", "access", "access_token", "refresh", "refresh_token")):
            yield 0, data, data
            return
    if isinstance(data, list):
        for index, entry in enumerate(data):
            if isinstance(entry, dict):
                yield index, entry, data


@dataclass
class AccountInfo:
    id: str
    display_name: str
    email: str
    access_token: str
    refresh_token: Optional[str]
    account_id: Optional[str]
    source_file: str
    expires_at: Optional[float] = None

    def is_expired(self) -> bool:
        expiry = self.expires_at or jwt_exp_epoch(self.access_token)
        if expiry is None:
            return False
        return time.time() >= (float(expiry) - 30)

    def auth_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        }
        if self.account_id:
            headers["ChatGPT-Account-Id"] = self.account_id
        return headers


def load_accounts(search_dir: Path) -> list[AccountInfo]:
    accounts: list[AccountInfo] = []
    seen_tokens: set[str] = set()

    for json_file in sorted(search_dir.glob("*.json")):
        data = safe_json_load(json_file)
        if data is None:
            continue

        for entry_index, entry, root in iter_auth_entries(data):
            access, refresh, account_id, expires_at, label = extract_auth_fields(entry, root)
            if not access or access in seen_tokens:
                continue
            seen_tokens.add(access)

            email = jwt_email(access)
            parts = [email]
            if label:
                parts.append(str(label))
            parts.append(json_file.name)
            display_name = " | ".join(part for part in parts if part)

            accounts.append(
                AccountInfo(
                    id=f"{json_file.resolve()}::{entry_index}",
                    display_name=display_name,
                    email=email,
                    access_token=access,
                    refresh_token=refresh,
                    account_id=account_id,
                    source_file=str(json_file.resolve()),
                    expires_at=expires_at or jwt_exp_epoch(access),
                )
            )

    return accounts


def renew_account(account: AccountInfo) -> tuple[bool, str]:
    if not account.refresh_token:
        return False, "A conta nao tem refresh token para renovacao."

    auth = do_renew(account.refresh_token)
    if not auth or not auth.get("access_token"):
        return False, "Falha ao renovar o token da conta."

    old_access = account.access_token
    account.access_token = auth["access_token"]
    account.refresh_token = auth.get("refresh_token") or account.refresh_token
    account.expires_at = jwt_exp_epoch(account.access_token)
    update_json_file_token(account.source_file, old_access, auth)
    return True, f"Token renovado para {account.email}."


@dataclass
class AttachmentData:
    id: str
    name: str
    kind: str
    mime_type: str
    size_bytes: int
    raw_bytes: Optional[bytes] = None
    text_content: Optional[str] = None
    source_path: Optional[str] = None
    truncated: bool = False
    note: str = ""

    def clone(self) -> "AttachmentData":
        return copy.deepcopy(self)

    def summary(self) -> str:
        icon = "[IMG]" if self.kind == "image" else ("[TXT]" if self.kind == "text" else "[BIN]")
        suffix = f" ({human_size(self.size_bytes)})" if self.size_bytes else ""
        return f"{icon} {self.name}{suffix}"

    def image_data_url(self) -> Optional[str]:
        if self.kind != "image" or not self.raw_bytes:
            return None
        return bytes_to_data_url(self.mime_type or "image/png", self.raw_bytes)

    def to_api_parts(self) -> list[dict[str, Any]]:
        if self.kind == "image":
            url = self.image_data_url()
            if not url:
                return []
            return [{"type": "input_image", "image_url": url, "detail": "auto"}]

        if self.kind == "text":
            body = self.text_content or ""
            blocks = [f"[Arquivo anexado: {self.name} | {human_size(self.size_bytes)}]"]
            if self.note:
                blocks.append(self.note)
            fence = guess_language(self.name)
            blocks.append(f"```{fence}\n{body}\n```")
            return [{"type": "input_text", "text": "\n\n".join(blocks)}]

        descriptor = (
            f"[Arquivo binario anexado: {self.name} | {human_size(self.size_bytes)}]"
            "\nO conteudo binario nao foi embutido automaticamente."
        )
        if self.note:
            descriptor += f"\n{self.note}"
        return [{"type": "input_text", "text": descriptor}]


@dataclass
class ConversationMessage:
    role: str
    text: str = ""
    attachments: list[AttachmentData] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def clone(self) -> "ConversationMessage":
        return copy.deepcopy(self)

    def to_api_input(self) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        text_part_type = "output_text" if self.role == "assistant" else "input_text"
        if self.text.strip():
            parts.append({"type": text_part_type, "text": self.text.strip()})
        for attachment in self.attachments:
            parts.extend(attachment.to_api_parts())
        if not parts:
            parts.append({"type": text_part_type, "text": ""})
        return {"role": self.role, "content": parts}


def prepare_text_attachment(name: str, raw_bytes: bytes, source_path: Optional[str] = None) -> AttachmentData:
    truncated = False
    note = ""
    original_size = len(raw_bytes)
    if len(raw_bytes) > MAX_TEXT_ATTACHMENT_BYTES:
        raw_bytes = raw_bytes[:MAX_TEXT_ATTACHMENT_BYTES]
        truncated = True
        note = f"Arquivo truncado para os primeiros {human_size(MAX_TEXT_ATTACHMENT_BYTES)} antes do envio."

    text = raw_bytes.decode("utf-8", errors="replace")
    return AttachmentData(
        id=f"att-{time.time_ns()}-{random.randint(1000, 9999)}",
        name=name,
        kind="text",
        mime_type="text/plain",
        size_bytes=original_size,
        text_content=text,
        source_path=source_path,
        truncated=truncated,
        note=note,
    )


def prepare_attachment_from_path(path: Path) -> AttachmentData:
    if not path.exists() or not path.is_file():
        raise ValueError(f"Arquivo invalido: {path}")

    mime_type, _ = mimetypes.guess_type(str(path))
    mime_type = mime_type or "application/octet-stream"
    size_bytes = path.stat().st_size

    if mime_type.startswith("image/"):
        if size_bytes > MAX_IMAGE_ATTACHMENT_BYTES:
            raise ValueError(f"Imagem muito grande: {path.name} ({human_size(size_bytes)}).")
        return AttachmentData(
            id=f"att-{time.time_ns()}-{random.randint(1000, 9999)}",
            name=path.name,
            kind="image",
            mime_type=mime_type,
            size_bytes=size_bytes,
            raw_bytes=path.read_bytes(),
            source_path=str(path),
        )

    if path.suffix.lower() == ".pdf":
        pdf_text = extract_pdf_text(path)
        if pdf_text:
            return prepare_text_attachment(path.name, pdf_text.encode("utf-8"), str(path))
        return AttachmentData(
            id=f"att-{time.time_ns()}-{random.randint(1000, 9999)}",
            name=path.name,
            kind="binary",
            mime_type=mime_type,
            size_bytes=size_bytes,
            source_path=str(path),
            note="PDF anexado sem extracao textual automatica. Instale pypdf para extrair texto localmente.",
        )

    if looks_like_text_file(path):
        return prepare_text_attachment(path.name, path.read_bytes(), str(path))

    return AttachmentData(
        id=f"att-{time.time_ns()}-{random.randint(1000, 9999)}",
        name=path.name,
        kind="binary",
        mime_type=mime_type,
        size_bytes=size_bytes,
        source_path=str(path),
        note="Arquivo anexado so como metadado; o conteudo binario nao foi inserido no prompt.",
    )


def prepare_attachment_from_qimage(image: QtGui.QImage) -> AttachmentData:
    raw = qimage_to_png_bytes(image)
    return AttachmentData(
        id=f"att-{time.time_ns()}-{random.randint(1000, 9999)}",
        name=f"clipboard-image-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png",
        kind="image",
        mime_type="image/png",
        size_bytes=len(raw),
        raw_bytes=raw,
        source_path=None,
    )


def clear_layout(layout: QtWidgets.QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            clear_layout(child_layout)


def extract_response_output_text(response_obj: dict[str, Any]) -> str:
    pieces: list[str] = []
    for output in response_obj.get("output", []) or []:
        content = output.get("content")
        if isinstance(content, str):
            pieces.append(content)
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") in {"output_text", "text"} and item.get("text"):
                    pieces.append(str(item["text"]))
    return "".join(pieces)


def extract_text_from_event(event: dict[str, Any]) -> str:
    if event.get("type") == "response.output_text.delta" and event.get("delta"):
        return str(event.get("delta", ""))

    choices = event.get("choices") or []
    if choices:
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            return "".join(parts)
    return ""


def extract_error_from_event(event: dict[str, Any]) -> Optional[str]:
    if event.get("error"):
        error = event["error"]
        if isinstance(error, dict):
            return error.get("message") or json.dumps(error, ensure_ascii=False)
        return str(error)
    if event.get("type") == "error":
        return event.get("message") or json.dumps(event, ensure_ascii=False)
    return None


def extract_non_stream_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    if payload.get("response") and isinstance(payload["response"], dict):
        text = extract_response_output_text(payload["response"])
        if text:
            return text
    if payload.get("output"):
        text = extract_response_output_text(payload)
        if text:
            return text
    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return json.dumps(payload, ensure_ascii=False, indent=2)


def iter_sse_blocks(response: requests.Response) -> Iterable[list[str]]:
    block: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        if isinstance(raw_line, bytes):
            encoding = response.encoding or "utf-8"
            line = raw_line.decode(encoding, errors="replace").rstrip("\r")
        else:
            line = raw_line.rstrip("\r")
        if line == "":
            if block:
                yield block
                block = []
            continue
        block.append(line)
    if block:
        yield block


class ComposeTextEdit(QtWidgets.QTextEdit):
    sendRequested = pyqtSignal()
    filesReceived = pyqtSignal(list)
    imageReceived = pyqtSignal(object)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            event.accept()
            self.sendRequested.emit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source: QtCore.QMimeData) -> None:
        files = [url.toLocalFile() for url in source.urls() if url.isLocalFile()]
        image = image_from_mime_data(source)

        handled = False
        if files:
            self.filesReceived.emit(files)
            handled = True
        if image is not None:
            self.imageReceived.emit(image)
            handled = True

        if not handled:
            super().insertFromMimeData(source)

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasImage():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        mime = event.mimeData()
        files = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        image = image_from_mime_data(mime)

        if files:
            self.filesReceived.emit(files)
            event.acceptProposedAction()
            return
        if image is not None:
            self.imageReceived.emit(image)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MessageBubble(QtWidgets.QFrame):
    def __init__(
        self, role: str, header_text: str, text: str = "", attachments: Optional[list[AttachmentData]] = None
    ) -> None:
        super().__init__()
        self.role = role
        self._text = text

        self.setObjectName(f"bubble_{role}")
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        if role == "user":
            self.setMaximumWidth(520)
            self.setMinimumWidth(120)
        elif role == "welcome":
            self.setMaximumWidth(780)
            self.setMinimumWidth(620)
        else:
            self.setMaximumWidth(760)
            self.setMinimumWidth(420)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        self.header_label = QtWidgets.QLabel(header_text)
        self.header_label.setObjectName("bubbleHeader")
        self.header_label.setWordWrap(True)
        root.addWidget(self.header_label)

        self.attachments_layout = QtWidgets.QVBoxLayout()
        self.attachments_layout.setSpacing(6)
        root.addLayout(self.attachments_layout)

        self.body_label = QtWidgets.QLabel(text)
        self.body_label.setWordWrap(True)
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_label.setObjectName("bubbleBody")
        self.body_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        root.addWidget(self.body_label)

        self.set_header(header_text)
        self.set_text(text)
        self.set_attachments(attachments or [])

    def set_header(self, text: str) -> None:
        self.header_label.setText(text)

    def set_text(self, text: str) -> None:
        self._text = text
        self.body_label.setText(text)

    def append_text(self, delta: str) -> None:
        self._text += delta
        self.body_label.setText(self._text)

    def text(self) -> str:
        return self._text

    def set_attachments(self, attachments: list[AttachmentData]) -> None:
        clear_layout(self.attachments_layout)
        for attachment in attachments:
            if attachment.kind == "image" and attachment.raw_bytes:
                image_label = QtWidgets.QLabel()
                pixmap = QtGui.QPixmap()
                pixmap.loadFromData(attachment.raw_bytes)
                image_label.setPixmap(
                    pixmap.scaled(
                        260,
                        260,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                image_label.setObjectName("imagePreview")
                self.attachments_layout.addWidget(image_label)

            meta = QtWidgets.QLabel(attachment.summary())
            meta.setObjectName("attachmentMeta")
            meta.setWordWrap(True)
            self.attachments_layout.addWidget(meta)


class PendingAttachmentRow(QtWidgets.QFrame):
    removeRequested = pyqtSignal(str)

    def __init__(self, attachment: AttachmentData, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.attachment_id = attachment.id
        self.setObjectName("pendingAttachmentRow")

        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(10)

        if attachment.kind == "image" and attachment.raw_bytes:
            thumb = QtWidgets.QLabel()
            thumb.setFixedSize(QSize(52, 52))
            pixmap = QtGui.QPixmap()
            pixmap.loadFromData(attachment.raw_bytes)
            thumb.setPixmap(
                pixmap.scaled(
                    52,
                    52,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            thumb.setObjectName("pendingThumb")
            root.addWidget(thumb)

        info_layout = QtWidgets.QVBoxLayout()
        info_layout.setSpacing(2)
        title = QtWidgets.QLabel(attachment.summary())
        title.setObjectName("pendingTitle")
        title.setWordWrap(True)
        info_layout.addWidget(title)

        if attachment.note:
            note = QtWidgets.QLabel(attachment.note)
            note.setObjectName("pendingNote")
            note.setWordWrap(True)
            info_layout.addWidget(note)
        elif attachment.kind == "text" and attachment.text_content:
            preview = QtWidgets.QLabel(ellipsis(attachment.text_content, 120))
            preview.setObjectName("pendingNote")
            preview.setWordWrap(True)
            info_layout.addWidget(preview)

        root.addLayout(info_layout, 1)

        remove_button = QtWidgets.QPushButton("Remover")
        remove_button.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
        remove_button.clicked.connect(lambda: self.removeRequested.emit(self.attachment_id))
        root.addWidget(remove_button)


class ChatWorker(QThread):
    statusChanged = pyqtSignal(str)
    deltaReceived = pyqtSignal(str)
    completed = pyqtSignal(object)

    def __init__(
        self,
        account: AccountInfo,
        model: str,
        conversation_messages: list[ConversationMessage],
        previous_response_id: Optional[str],
        previous_account_id: Optional[str],
        store_preference: Optional[bool],
        parent: Optional[QtCore.QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.account = account
        self.model = model
        self.conversation_messages = [message.clone() for message in conversation_messages]
        self.previous_response_id = previous_response_id
        self.previous_account_id = previous_account_id
        self.store_preference = store_preference
        self._stop_event = threading.Event()
        self._response: Optional[requests.Response] = None

    def stop(self) -> None:
        self._stop_event.set()
        if self._response is not None:
            try:
                self._response.close()
            except Exception:
                pass

    def run(self) -> None:
        result = {
            "text": "",
            "error": None,
            "interrupted": False,
            "response_id": None,
            "used_store": False,
            "store_supported": None,
            "account_id": self.account.id,
            "account_display": self.account.display_name,
            "model": self.model,
        }

        try:
            if self.account.is_expired() and self.account.refresh_token:
                self.statusChanged.emit(f"Token expirado para {self.account.email}. Renovando...")
                ok, message = renew_account(self.account)
                self.statusChanged.emit(message)
                if not ok:
                    result["error"] = message
                    return

            full_history = [message.to_api_input() for message in self.conversation_messages]
            current_turn = [self.conversation_messages[-1].to_api_input()] if self.conversation_messages else []
            same_account = self.previous_account_id == self.account.id

            attempts: list[dict[str, Any]] = []
            if self.store_preference is not False:
                attempts.append(
                    {
                        "name": "native",
                        "payload": self._build_payload(
                            current_turn if (same_account and self.previous_response_id) else full_history,
                            store=True,
                            previous_response_id=self.previous_response_id
                            if (same_account and self.previous_response_id)
                            else None,
                        ),
                    }
                )
            attempts.append({"name": "stateless", "payload": self._build_payload(full_history, store=False)})

            succeeded = False
            for attempt in attempts:
                if self._stop_event.is_set():
                    result["interrupted"] = True
                    break

                mode_label = "contexto nativo" if attempt["payload"].get("store") else "stateless"
                self.statusChanged.emit(f"Usando {self.account.display_name} | modo {mode_label}...")
                response = self._post_stream(attempt["payload"])
                self._response = response

                if response.status_code == 200:
                    result["used_store"] = bool(attempt["payload"].get("store"))
                    if attempt["name"] == "native":
                        result["store_supported"] = True
                    elif self.store_preference is not False:
                        result["store_supported"] = False
                    self._consume_response(response, result)
                    succeeded = True
                    break

                body = self._safe_error_body(response)
                response.close()

                if attempt["name"] == "native" and self._should_fallback(response.status_code, body):
                    result["store_supported"] = False
                    self.statusChanged.emit(
                        "Conta rejeitou store ou previous_response_id. Caindo para modo stateless..."
                    )
                    continue

                result["error"] = f"HTTP {response.status_code}: {body}"
                break

            if not succeeded and not result["error"] and not result["interrupted"]:
                result["error"] = "A resposta terminou sem conteudo utilizavel."

        except Exception as exc:
            result["error"] = str(exc)
        finally:
            if self._response is not None:
                try:
                    self._response.close()
                except Exception:
                    pass
            self.completed.emit(result)

    def _build_payload(
        self, input_messages: list[dict[str, Any]], store: bool, previous_response_id: Optional[str] = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": BASE_INSTRUCTIONS,
            "input": input_messages,
            "store": store,
            "stream": True,
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        return payload

    def _post_stream(self, payload: dict[str, Any]) -> requests.Response:
        try:
            response = requests.post(
                CODEX_URL,
                json=payload,
                headers=self.account.auth_headers(),
                stream=True,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Falha de rede ao falar com Codex: {exc}") from exc

        if response.status_code == 401 and self.account.refresh_token and not self._stop_event.is_set():
            response.close()
            self.statusChanged.emit(f"401 para {self.account.email}. Tentando renovar token...")
            ok, message = renew_account(self.account)
            self.statusChanged.emit(message)
            if not ok:
                raise RuntimeError(message)
            try:
                response = requests.post(
                    CODEX_URL,
                    json=payload,
                    headers=self.account.auth_headers(),
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Falha de rede apos renovacao: {exc}") from exc
        return response

    def _consume_response(self, response: requests.Response, result: dict[str, Any]) -> None:
        content_type = (response.headers.get("Content-Type") or "").lower()
        raw_lines: list[str] = []
        saw_sse_markers = "event-stream" in content_type

        for block in iter_sse_blocks(response):
            if self._stop_event.is_set():
                result["interrupted"] = True
                break

            raw_lines.extend(block)
            if any(line.startswith(("event:", "data:")) for line in block):
                saw_sse_markers = True

            data_lines: list[str] = []
            for line in block:
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

            if not data_lines:
                continue

            data_text = "\n".join(data_lines).strip()
            if not data_text:
                continue
            if data_text == "[DONE]":
                return

            try:
                event = json.loads(data_text)
            except Exception:
                continue

            error_message = extract_error_from_event(event)
            if error_message:
                raise RuntimeError(error_message)

            response_obj = event.get("response") if isinstance(event.get("response"), dict) else None
            if response_obj and response_obj.get("id") and not result["response_id"]:
                result["response_id"] = response_obj.get("id")

            delta = extract_text_from_event(event)
            if delta:
                result["text"] += delta
                self.deltaReceived.emit(delta)
                continue

            if event.get("type") == "response.completed" and response_obj and not result["text"]:
                fallback_text = extract_response_output_text(response_obj)
                if fallback_text:
                    result["text"] = fallback_text

        if self._stop_event.is_set():
            result["interrupted"] = True
            return

        if saw_sse_markers:
            return

        raw_text = "\n".join(raw_lines).strip()
        if not raw_text:
            return

        try:
            payload = json.loads(raw_text)
        except Exception:
            payload = raw_text

        text = extract_non_stream_text(payload)
        if text:
            result["text"] = text

    @staticmethod
    def _should_fallback(status_code: int, body: str) -> bool:
        lowered = body.lower()
        if status_code in {400, 403, 422}:
            return True
        return any(token in lowered for token in ("previous_response_id", "store", "response id", "thread"))

    @staticmethod
    def _safe_error_body(response: requests.Response) -> str:
        try:
            body = response.text.strip()
        except Exception:
            body = ""
        if not body:
            return "resposta vazia"
        return ellipsis(body.replace("\n", " "), 320)


class ChatWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.accounts: list[AccountInfo] = []
        self.messages: list[ConversationMessage] = []
        self.pending_attachments: list[AttachmentData] = []
        self.store_support_by_account: dict[str, bool] = {}
        self.previous_response_id: Optional[str] = None
        self.previous_account_id: Optional[str] = None
        self.current_worker: Optional[ChatWorker] = None
        self.current_assistant_bubble: Optional[MessageBubble] = None
        self.current_stream_received_text = False
        self.pending_user_message_index: Optional[int] = None

        self.setWindowTitle(APP_NAME)
        self.resize(1360, 900)
        self.setMinimumSize(980, 680)

        self._build_ui()
        self._apply_styles()
        self.load_accounts_into_selector(show_status=False)
        self.start_new_chat(initial=True)

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        self.setCentralWidget(central)

        top_frame = QtWidgets.QFrame()
        top_frame.setObjectName("topBar")
        top_layout = QtWidgets.QHBoxLayout(top_frame)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.setSpacing(12)

        brand_card = QtWidgets.QFrame()
        brand_card.setObjectName("brandCard")
        brand_card.setMinimumWidth(280)
        brand_layout = QtWidgets.QVBoxLayout(brand_card)
        brand_layout.setContentsMargins(16, 14, 16, 14)
        brand_layout.setSpacing(2)

        title = QtWidgets.QLabel("Chat Absoluto Codex")
        title.setObjectName("appTitle")
        brand_layout.addWidget(title)

        subtitle = QtWidgets.QLabel("Desktop nativo para conversar com o Codex")
        subtitle.setObjectName("brandSubtitle")
        subtitle.setWordWrap(True)
        brand_layout.addWidget(subtitle)

        top_layout.addWidget(brand_card, 0)

        controls_panel = QtWidgets.QWidget()
        controls_panel.setObjectName("controlPanel")
        controls_grid = QtWidgets.QGridLayout(controls_panel)
        controls_grid.setContentsMargins(0, 0, 0, 0)
        controls_grid.setHorizontalSpacing(12)
        controls_grid.setVerticalSpacing(10)

        self.account_combo = QtWidgets.QComboBox()
        self.account_combo.setMinimumWidth(290)
        controls_grid.addWidget(self._labeled_widget("Conta", self.account_combo), 0, 0)

        self.refresh_accounts_button = QtWidgets.QPushButton("Atualizar contas")
        self.refresh_accounts_button.clicked.connect(self.load_accounts_into_selector)
        self.refresh_accounts_button.setMinimumWidth(150)
        controls_grid.addWidget(self.refresh_accounts_button, 0, 1)

        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(180)
        self.model_combo.addItems(DEFAULT_MODELS)
        self.model_combo.setCurrentText(DEFAULT_MODELS[0])
        controls_grid.addWidget(self._labeled_widget("Modelo", self.model_combo), 0, 2)

        self.new_chat_button = QtWidgets.QPushButton("Nova conversa")
        self.new_chat_button.clicked.connect(self.start_new_chat)
        self.new_chat_button.setMinimumWidth(140)
        controls_grid.addWidget(self.new_chat_button, 0, 3)

        self.stop_button = QtWidgets.QPushButton("Interromper")
        self.stop_button.clicked.connect(self.stop_generation)
        self.stop_button.setEnabled(False)
        self.stop_button.setMinimumWidth(120)
        controls_grid.addWidget(self.stop_button, 0, 4)

        self.mode_badge = QtWidgets.QLabel("Contexto: pronto")
        self.mode_badge.setObjectName("statusBadge")
        self.mode_badge.setMinimumWidth(150)
        controls_grid.addWidget(self.mode_badge, 1, 0, 1, 2)

        self.account_badge = QtWidgets.QLabel("Conta usada: -")
        self.account_badge.setObjectName("statusBadge")
        self.account_badge.setMinimumWidth(260)
        self.account_badge.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        controls_grid.addWidget(self.account_badge, 1, 2, 1, 3)

        controls_grid.setColumnStretch(0, 2)
        controls_grid.setColumnStretch(2, 1)
        controls_grid.setColumnStretch(4, 1)
        top_layout.addWidget(controls_panel, 1)
        root.addWidget(top_frame)

        chat_frame = QtWidgets.QFrame()
        chat_frame.setObjectName("chatFrame")
        chat_frame_layout = QtWidgets.QVBoxLayout(chat_frame)
        chat_frame_layout.setContentsMargins(6, 6, 6, 6)
        chat_frame_layout.setSpacing(0)

        self.chat_scroll = QtWidgets.QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_container = QtWidgets.QWidget()
        self.chat_layout = QtWidgets.QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(18, 18, 18, 18)
        self.chat_layout.setSpacing(16)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_scroll.setWidget(self.chat_container)
        chat_frame_layout.addWidget(self.chat_scroll)
        root.addWidget(chat_frame, 1)

        composer_frame = QtWidgets.QFrame()
        composer_frame.setObjectName("composerFrame")
        composer_layout = QtWidgets.QVBoxLayout(composer_frame)
        composer_layout.setContentsMargins(14, 14, 14, 14)
        composer_layout.setSpacing(10)

        self.attachments_area = QtWidgets.QFrame()
        self.attachments_area.setObjectName("attachmentsFrame")
        self.attachments_layout = QtWidgets.QVBoxLayout(self.attachments_area)
        self.attachments_layout.setContentsMargins(8, 8, 8, 8)
        self.attachments_layout.setSpacing(8)
        self.attachments_area.hide()
        composer_layout.addWidget(self.attachments_area)

        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(10)
        input_row.setAlignment(Qt.AlignmentFlag.AlignBottom)

        self.attach_button = QtWidgets.QPushButton("Arquivos / Imagens")
        self.attach_button.setMinimumWidth(160)
        self.attach_button.setMinimumHeight(48)
        self.attach_button.clicked.connect(self.pick_files)
        input_row.addWidget(self.attach_button)

        self.prompt_edit = ComposeTextEdit()
        self.prompt_edit.setPlaceholderText(
            "Escreva a mensagem aqui. Enter envia, Shift+Enter quebra linha. "
            "Tambem e possivel colar imagem ou arrastar arquivos."
        )
        self.prompt_edit.setMinimumHeight(110)
        self.prompt_edit.setMaximumHeight(190)
        self.prompt_edit.sendRequested.connect(self.send_message)
        self.prompt_edit.filesReceived.connect(self.add_files)
        self.prompt_edit.imageReceived.connect(self.add_clipboard_image)
        input_row.addWidget(self.prompt_edit, 1)

        self.send_button = QtWidgets.QPushButton("Enviar")
        self.send_button.setObjectName("sendButton")
        self.send_button.setMinimumWidth(130)
        self.send_button.setMinimumHeight(48)
        self.send_button.clicked.connect(self.send_message)
        input_row.addWidget(self.send_button)
        composer_layout.addLayout(input_row)

        root.addWidget(composer_frame)

        self.status_bar = QtWidgets.QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Pronto. O app le automaticamente os auth*.json da pasta atual.")

    def _labeled_widget(self, label_text: str, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        wrapper = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QtWidgets.QLabel(label_text)
        label.setObjectName("fieldLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return wrapper

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #0d1117;
                color: #e6edf3;
                font-family: "Segoe UI", "Consolas", monospace;
                font-size: 11pt;
            }
            #topBar, #chatFrame, #composerFrame, #attachmentsFrame {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 12px;
            }
            #brandCard {
                background: #11161d;
                border: 1px solid #30363d;
                border-radius: 12px;
            }
            #appTitle {
                font-size: 16pt;
                font-weight: 700;
                color: #58a6ff;
                padding-right: 10px;
            }
            #brandSubtitle {
                color: #8b949e;
                font-size: 9pt;
            }
            #controlPanel {
                background: transparent;
                border: none;
            }
            #fieldLabel {
                color: #8b949e;
                font-size: 9pt;
                font-weight: 600;
            }
            QComboBox, QTextEdit, QPushButton {
                background: #0d1117;
                border: 1px solid #30363d;
                border-radius: 10px;
                padding: 8px 10px;
            }
            QComboBox:focus, QTextEdit:focus {
                border: 1px solid #58a6ff;
            }
            QPushButton {
                background: #21262d;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #2b3138;
            }
            QPushButton:disabled {
                color: #7d8590;
                background: #11161c;
            }
            #sendButton {
                background: #238636;
                border: 1px solid #2ea043;
                min-width: 110px;
            }
            #sendButton:hover {
                background: #2ea043;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QStatusBar {
                background: #0b0f14;
                color: #8b949e;
                border-top: 1px solid #30363d;
            }
            #statusBadge {
                background: #0d1117;
                border: 1px solid #30363d;
                border-radius: 10px;
                padding: 10px 12px;
                color: #c9d1d9;
                font-size: 10pt;
            }
            #bubble_user {
                background: #1f6feb;
                border: 1px solid #388bfd;
                border-radius: 14px;
            }
            #bubble_assistant {
                background: #161b22;
                border: 1px solid #30363d;
                border-radius: 14px;
            }
            #bubbleHeader {
                color: #8b949e;
                font-size: 9pt;
                font-weight: 600;
                padding-bottom: 4px;
            }
            #bubbleBody {
                color: #e6edf3;
                font-size: 11pt;
                line-height: 1.45;
            }
            #imagePreview {
                border: 1px solid #30363d;
                border-radius: 10px;
                padding: 2px;
                background: #0d1117;
            }
            #attachmentMeta {
                color: #8b949e;
                font-size: 9pt;
            }
            #pendingAttachmentRow {
                background: #0d1117;
                border: 1px solid #30363d;
                border-radius: 10px;
            }
            #pendingTitle {
                font-weight: 700;
                color: #c9d1d9;
            }
            #pendingNote {
                color: #8b949e;
                font-size: 9pt;
            }
            #pendingThumb {
                border: 1px solid #30363d;
                border-radius: 8px;
                background: #161b22;
            }
            QScrollBar:vertical {
                background: #11161d;
                width: 12px;
                margin: 4px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #30363d;
                min-height: 30px;
                border-radius: 6px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )

    def load_accounts_into_selector(self, show_status: bool = True) -> None:
        previous_selection = self.account_combo.currentData()
        self.accounts = load_accounts(Path("."))

        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        self.account_combo.addItem("Aleatoria", RANDOM_ACCOUNT_ID)
        for account in self.accounts:
            self.account_combo.addItem(account.display_name, account.id)
        self.account_combo.blockSignals(False)

        index = self.account_combo.findData(previous_selection)
        if index >= 0:
            self.account_combo.setCurrentIndex(index)
        else:
            self.account_combo.setCurrentIndex(0)

        if show_status:
            if self.accounts:
                self.status_bar.showMessage(f"{len(self.accounts)} conta(s) detectada(s) nos JSON da pasta.")
            else:
                self.status_bar.showMessage("Nenhuma conta detectada. Coloque um auth*.json valido nesta pasta.")

    def choose_account_for_send(self) -> Optional[AccountInfo]:
        if not self.accounts:
            return None
        selected_id = self.account_combo.currentData()
        if selected_id == RANDOM_ACCOUNT_ID or selected_id is None:
            return random.choice(self.accounts)
        for account in self.accounts:
            if account.id == selected_id:
                return account
        return self.accounts[0]

    def start_new_chat(self, initial: bool = False) -> None:
        if self.current_worker is not None:
            QtWidgets.QMessageBox.information(
                self, APP_NAME, "Interrompa a geracao atual antes de comecar uma nova conversa."
            )
            return

        self.messages.clear()
        self.pending_user_message_index = None
        self.previous_response_id = None
        self.previous_account_id = None
        self.current_assistant_bubble = None
        self.current_stream_received_text = False

        clear_layout(self.chat_layout)

        intro = (
            "Programa Windows nativo pronto.\n\n"
            "- Escolha uma conta especifica ou deixe em aleatoria\n"
            "- Escolha o modelo Codex\n"
            "- Anexe imagens, arquivos de texto ou codigo, cole do clipboard ou arraste arquivos\n"
            "- Enter envia, Shift+Enter quebra linha\n"
            "- O botao Interromper cancela a stream atual"
        )
        self.add_message_widget("assistant", intro, [], "Codex | pronto")
        self.mode_badge.setText("Contexto: pronto")
        self.account_badge.setText("Conta usada: -")
        if initial:
            self.status_bar.showMessage("Pronto. Abra um auth*.json na pasta e converse.")
        else:
            self.status_bar.showMessage("Nova conversa iniciada.")

    def add_message_widget(
        self, role: str, text: str, attachments: list[AttachmentData], header_text: str
    ) -> MessageBubble:
        bubble = MessageBubble(role, header_text, text, attachments)
        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)

        if role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)

        self.chat_layout.addWidget(row)
        QtCore.QTimer.singleShot(0, self.scroll_chat_to_bottom)
        return bubble

    def scroll_chat_to_bottom(self) -> None:
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def refresh_attachment_widgets(self) -> None:
        clear_layout(self.attachments_layout)
        if not self.pending_attachments:
            self.attachments_area.hide()
            return

        self.attachments_area.show()
        for attachment in self.pending_attachments:
            row = PendingAttachmentRow(attachment)
            row.removeRequested.connect(self.remove_attachment)
            self.attachments_layout.addWidget(row)

    def remove_attachment(self, attachment_id: str) -> None:
        self.pending_attachments = [item for item in self.pending_attachments if item.id != attachment_id]
        self.refresh_attachment_widgets()

    def clear_pending_attachments(self) -> None:
        self.pending_attachments.clear()
        self.refresh_attachment_widgets()

    def pick_files(self) -> None:
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Selecionar arquivos", str(Path.cwd()))
        if files:
            self.add_files(files)

    def add_files(self, files: list[str]) -> None:
        added = 0
        errors: list[str] = []
        for raw_path in files:
            try:
                attachment = prepare_attachment_from_path(Path(raw_path))
                self.pending_attachments.append(attachment)
                added += 1
            except Exception as exc:
                errors.append(str(exc))

        self.refresh_attachment_widgets()
        if added:
            self.status_bar.showMessage(f"{added} anexo(s) preparado(s) para envio.")
        if errors:
            QtWidgets.QMessageBox.warning(self, APP_NAME, "\n".join(errors[:6]))

    def add_clipboard_image(self, image: object) -> None:
        if isinstance(image, QtGui.QImage):
            attachment = prepare_attachment_from_qimage(image)
            self.pending_attachments.append(attachment)
            self.refresh_attachment_widgets()
            self.status_bar.showMessage("Imagem do clipboard anexada.")

    def set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.attach_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.refresh_accounts_button.setEnabled(not busy)
        self.prompt_edit.setReadOnly(busy)

    def stop_generation(self) -> None:
        if self.current_worker is None:
            return
        self.status_bar.showMessage("Interrompendo geracao...")
        self.current_worker.stop()

    def send_message(self) -> None:
        if self.current_worker is not None:
            return

        text = self.prompt_edit.toPlainText().strip()
        if not text and not self.pending_attachments:
            return

        account = self.choose_account_for_send()
        if account is None:
            QtWidgets.QMessageBox.warning(
                self,
                APP_NAME,
                "Nenhuma conta foi encontrada. Coloque um auth*.json valido na pasta do programa.",
            )
            return

        model = self.model_combo.currentText().strip() or DEFAULT_MODELS[0]
        attachments = [attachment.clone() for attachment in self.pending_attachments]
        user_message = ConversationMessage(role="user", text=text, attachments=attachments)
        self.messages.append(user_message)
        self.pending_user_message_index = len(self.messages) - 1

        self.add_message_widget(
            "user",
            text or "(mensagem so com anexos)",
            attachments,
            f"Voce | {human_time(user_message.ts)}",
        )

        self.prompt_edit.clear()
        self.clear_pending_attachments()

        self.current_assistant_bubble = self.add_message_widget(
            "assistant",
            "...",
            [],
            f"Codex | {account.display_name} | {model}",
        )
        self.current_stream_received_text = False

        self.current_worker = ChatWorker(
            account=copy.deepcopy(account),
            model=model,
            conversation_messages=self.messages,
            previous_response_id=self.previous_response_id,
            previous_account_id=self.previous_account_id,
            store_preference=self.store_support_by_account.get(account.id),
        )
        self.current_worker.statusChanged.connect(self.on_worker_status)
        self.current_worker.deltaReceived.connect(self.on_worker_delta)
        self.current_worker.completed.connect(self.on_worker_completed)

        self.set_busy(True)
        self.status_bar.showMessage(f"Enviando com {account.display_name}...")
        self.current_worker.start()

    def on_worker_status(self, message: str) -> None:
        self.status_bar.showMessage(message)

    def on_worker_delta(self, delta: str) -> None:
        if not self.current_assistant_bubble:
            return
        if not self.current_stream_received_text:
            self.current_assistant_bubble.set_text("")
            self.current_stream_received_text = True
        self.current_assistant_bubble.append_text(delta)
        self.scroll_chat_to_bottom()

    def on_worker_completed(self, result: dict[str, Any]) -> None:
        worker = self.current_worker
        self.current_worker = None
        self.set_busy(False)
        self.prompt_edit.setFocus()

        if result.get("store_supported") is not None:
            self.store_support_by_account[result["account_id"]] = bool(result["store_supported"])

        self.previous_account_id = result.get("account_id")
        if result.get("interrupted") or not result.get("used_store") or not result.get("response_id"):
            self.previous_response_id = None
        else:
            self.previous_response_id = result.get("response_id")

        self.account_badge.setText(f"Conta usada: {result.get('account_display', '-')}")
        if result.get("used_store") and not result.get("interrupted"):
            self.mode_badge.setText("Contexto: nativo")
        else:
            self.mode_badge.setText("Contexto: stateless")

        bubble = self.current_assistant_bubble
        final_text = (result.get("text") or "").strip()
        error = result.get("error")
        interrupted = bool(result.get("interrupted"))
        model = result.get("model") or self.model_combo.currentText().strip()
        header = f"Codex | {result.get('account_display', '-')} | {model}"

        if bubble is not None:
            bubble.set_header(header)

        if final_text:
            if bubble is not None:
                bubble.set_text(final_text)
            self.messages.append(ConversationMessage(role="assistant", text=final_text))
        elif interrupted:
            partial = bubble.text().strip() if bubble is not None else ""
            if partial and partial != "...":
                self.messages.append(ConversationMessage(role="assistant", text=partial))
            elif bubble is not None:
                bubble.set_text("[Geracao interrompida]")
        elif error:
            if bubble is not None:
                bubble.set_text(f"[Erro] {error}")
            if self.pending_user_message_index is not None and self.pending_user_message_index < len(self.messages):
                self.messages.pop(self.pending_user_message_index)
        else:
            if bubble is not None:
                bubble.set_text("[Sem texto retornado]")

        if interrupted:
            self.status_bar.showMessage("Geracao interrompida pelo usuario.")
        elif error:
            self.status_bar.showMessage(f"Falha: {error}")
        else:
            self.status_bar.showMessage("Resposta concluida com sucesso.")

        self.pending_user_message_index = None
        self.current_assistant_bubble = None
        self.current_stream_received_text = False

        if worker is not None:
            worker.deleteLater()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.current_worker is not None:
            self.current_worker.stop()
            self.current_worker.wait(1500)
        super().closeEvent(event)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")

    window = ChatWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
