from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .logging_utils import AppLogger
from .models import CredentialEntry, CredentialRef, FormatKind
from .utils import email_label, jwt_account_id, jwt_email, jwt_user_id


class StoreError(RuntimeError):
    pass


class CredentialStore:
    def __init__(self, logger: AppLogger | None = None) -> None:
        self.logger = logger

    def discover_entries(self, directory: Path) -> list[CredentialEntry]:
        entries: list[CredentialEntry] = []
        for path in sorted(Path(directory).glob("*.json")):
            if not self._should_scan_file(path):
                continue
            try:
                data = self._load_json(path)
                if self._normalize_labels(path, data):
                    self._write_json(path, data)
                entries.extend(self._extract_entries(path, data))
            except StoreError as exc:
                if self.logger is not None:
                    self.logger.warning(str(exc))
        return entries

    def load_entry(self, ref: CredentialRef) -> CredentialEntry:
        data = self._load_json(ref.source_path)
        entries = self._extract_entries(ref.source_path, data)
        for entry in entries:
            if entry.ref == ref:
                return entry
        raise StoreError(f"Entrada nao encontrada em {ref.source_path}")

    def update_tokens(self, ref: CredentialRef, new_tokens: dict[str, Any]) -> None:
        data = self._load_json(ref.source_path)
        if ref.format_kind == "credential_pool":
            pool = data.get("credential_pool", {}).get("openai-codex")
            if not isinstance(pool, list) or ref.entry_index is None or ref.entry_index >= len(pool):
                raise StoreError("Entrada de credential_pool invalida para renew")
            entry = pool[ref.entry_index]
            if not isinstance(entry, dict):
                raise StoreError("Entrada de credential_pool invalida")
            entry["access_token"] = new_tokens.get("access_token")
            entry["refresh_token"] = new_tokens.get("refresh_token")
            if "id_token" in new_tokens:
                entry["id_token"] = new_tokens["id_token"]
        elif ref.format_kind == "tokens":
            tokens = data.get("tokens")
            if not isinstance(tokens, dict):
                raise StoreError("Estrutura tokens invalida para renew")
            self._update_token_container(tokens, new_tokens)
            self._update_expiry(data, new_tokens)
        elif ref.format_kind == "access":
            if not isinstance(data, dict):
                raise StoreError("Estrutura access invalida para renew")
            data["access"] = new_tokens.get("access_token")
            data["refresh"] = new_tokens.get("refresh_token")
            if "id_token" in new_tokens:
                data["id_token"] = new_tokens["id_token"]
            self._update_expiry(data, new_tokens)
        else:
            raise StoreError(f"Formato nao suportado: {ref.format_kind}")
        self._write_json(ref.source_path, data)

    def delete_entry(self, ref: CredentialRef) -> None:
        if ref.format_kind in {"tokens", "access"}:
            try:
                ref.source_path.unlink(missing_ok=True)
                return
            except OSError as exc:
                raise StoreError(f"Falha ao remover {ref.source_path.name}: {exc}") from exc

        data = self._load_json(ref.source_path)
        if ref.format_kind != "credential_pool":
            raise StoreError(f"Formato nao suportado para remocao: {ref.format_kind}")
        pool = data.get("credential_pool", {}).get("openai-codex")
        if not isinstance(pool, list) or ref.entry_index is None or ref.entry_index >= len(pool):
            raise StoreError("Entrada de credential_pool invalida para remocao")
        pool.pop(ref.entry_index)
        self._backup_file(ref.source_path)
        self._write_json(ref.source_path, data)

    def upsert_account(self, directory: Path, tokens: dict[str, Any]) -> Path:
        directory = Path(directory)
        file_path = directory / "auth(infinity).json"
        if file_path.exists():
            try:
                data = self._load_json(file_path)
            except StoreError:
                data = {}
        else:
            data = {}

        if not isinstance(data, dict):
            data = {}
        credential_pool = data.setdefault("credential_pool", {})
        if not isinstance(credential_pool, dict):
            credential_pool = {}
            data["credential_pool"] = credential_pool
        pool = credential_pool.setdefault("openai-codex", [])
        if not isinstance(pool, list):
            pool = []
            credential_pool["openai-codex"] = pool

        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise StoreError("Novo access token invalido")

        email = jwt_email(access_token)
        account_id = jwt_account_id(access_token)
        user_id = jwt_user_id(access_token)
        fallback_label = f"Injetada_{time.strftime('%H%M')}"
        entry_label = email_label(email, fallback_label)

        new_entry = {
            "label": entry_label,
            "auth_type": "oauth",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "account_id": account_id,
            "user_id": user_id,
        }

        match_index: int | None = None
        for index, entry in enumerate(pool):
            if not isinstance(entry, dict):
                continue
            decision = self._match_decision(entry, account_id, user_id, email)
            if decision == "update":
                match_index = index
                break

        self._backup_file(file_path)
        if match_index is None:
            new_entry["label"] = self._unique_label(pool, new_entry["label"])
            pool.append(new_entry)
        else:
            existing = pool[match_index]
            if isinstance(existing, dict):
                merged = dict(existing)
                merged.update(new_entry)
                pool[match_index] = merged
            else:
                pool[match_index] = new_entry

        self._write_json(file_path, data)
        return file_path

    def _extract_entries(self, path: Path, data: Any) -> list[CredentialEntry]:
        if not isinstance(data, dict):
            return []
        entries: list[CredentialEntry] = []
        credential_pool = data.get("credential_pool")
        if isinstance(credential_pool, dict):
            pool = credential_pool.get("openai-codex")
            if isinstance(pool, list):
                for index, entry in enumerate(pool):
                    if not isinstance(entry, dict):
                        continue
                    label = self._label_for_entry(path, entry)
                    ref = CredentialRef(path, "credential_pool", index)
                    entries.append(
                        CredentialEntry(
                            ref=ref,
                            label=label,
                            access_token=self._get_token(entry, "access_token"),
                            refresh_token=self._get_token(entry, "refresh_token"),
                            account_id=self._entry_account_id(entry),
                            raw=entry,
                        )
                    )
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            ref = CredentialRef(path, "tokens")
            entries.append(
                CredentialEntry(
                    ref=ref,
                    label=str(data.get("label") or path.stem),
                    access_token=self._get_token(tokens, "access_token", "access"),
                    refresh_token=self._get_token(tokens, "refresh_token", "refresh"),
                    account_id=self._get_token(tokens, "account_id", "accountId") or self._get_token(data, "account_id", "accountId"),
                    raw=tokens,
                )
            )
        elif isinstance(data.get("access"), str):
            ref = CredentialRef(path, "access")
            entries.append(
                CredentialEntry(
                    ref=ref,
                    label=str(data.get("label") or path.stem),
                    access_token=self._get_token(data, "access"),
                    refresh_token=self._get_token(data, "refresh"),
                    account_id=self._get_token(data, "account_id", "accountId"),
                    raw=data,
                )
            )
        return entries

    def _entry_account_id(self, entry: dict[str, Any]) -> str | None:
        return self._get_token(entry, "account_id", "accountId") or self._get_token(entry.get("extra", {}), "account_id")

    def _entry_user_id(self, entry: dict[str, Any]) -> str | None:
        direct = self._get_token(entry, "user_id", "userId")
        if direct:
            return direct
        access_token = self._get_token(entry, "access_token", "access")
        return jwt_user_id(access_token)

    def _label_for_entry(self, path: Path, entry: dict[str, Any]) -> str:
        access_token = self._get_token(entry, "access_token", "access")
        email = jwt_email(access_token)
        if email != "-":
            return email_label(email, path.stem)
        label = entry.get("label") or entry.get("id")
        return str(label) if label else path.stem

    def _get_token(self, source: Any, *names: str) -> str | None:
        if not isinstance(source, dict):
            return None
        for name in names:
            value = source.get(name)
            if isinstance(value, str) and value:
                return value
        return None

    def _update_token_container(self, token_container: dict[str, Any], new_tokens: dict[str, Any]) -> None:
        if "access_token" in token_container:
            token_container["access_token"] = new_tokens.get("access_token")
        if "access" in token_container:
            token_container["access"] = new_tokens.get("access_token")
        if "refresh_token" in token_container:
            token_container["refresh_token"] = new_tokens.get("refresh_token")
        if "refresh" in token_container:
            token_container["refresh"] = new_tokens.get("refresh_token")
        if "id_token" in token_container and "id_token" in new_tokens:
            token_container["id_token"] = new_tokens["id_token"]

    def _update_expiry(self, target: dict[str, Any], new_tokens: dict[str, Any]) -> None:
        expires_in = new_tokens.get("expires_in")
        if not isinstance(expires_in, (int, float)):
            return
        expires = int(time.time() * 1000) + int(expires_in * 1000)
        if "expires" in target:
            target["expires"] = expires

    def _load_json(self, path: Path) -> dict[str, Any]:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StoreError(f"Falha ao ler {path.name}: {exc}") from exc
        if not content.strip():
            raise StoreError(f"Ficheiro vazio ignorado: {path.name}")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise StoreError(f"JSON invalido em {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise StoreError(f"Formato JSON nao suportado em {path.name}")
        return data

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        try:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            raise StoreError(f"Falha ao gravar {path.name}: {exc}") from exc

    def _match_decision(
        self,
        entry: dict[str, Any],
        new_account_id: str | None,
        new_user_id: str | None,
        new_email: str,
    ) -> str:
        existing_token = entry.get("access_token")
        existing_email = jwt_email(existing_token) if isinstance(existing_token, str) else "-"
        existing_account_id = self._entry_account_id(entry)
        existing_user_id = self._entry_user_id(entry)

        if existing_user_id and new_user_id:
            if existing_user_id == new_user_id:
                return "update"
            return "append"

        if existing_email != "-" and new_email != "-" and existing_email == new_email:
            if existing_user_id and new_user_id and existing_user_id != new_user_id:
                return "append"
            return "update"

        if existing_account_id and new_account_id and existing_account_id == new_account_id:
            if self.logger is not None:
                self.logger.warning(
                    "Mesmo account_id encontrado em emails diferentes. Vou tratar as contas por user_id/email e preservar ambas."
                )
            if (existing_email == "-" or new_email == "-") and (not existing_user_id or not new_user_id):
                return "update"
            return "append"

        if not existing_user_id and not new_user_id and existing_email == "-" and new_email == "-" and existing_account_id and new_account_id and existing_account_id == new_account_id:
            return "update"

        return "append"

    def _should_scan_file(self, path: Path) -> bool:
        lower_name = path.name.lower()
        if lower_name.endswith(".bak.json"):
            return False
        if lower_name.endswith(".backup.json"):
            return False
        return True

    def _normalize_labels(self, path: Path, data: dict[str, Any]) -> bool:
        changed = False
        credential_pool = data.get("credential_pool")
        if isinstance(credential_pool, dict):
            pool = credential_pool.get("openai-codex")
            if isinstance(pool, list):
                used_labels: set[str] = set()
                for entry in pool:
                    if not isinstance(entry, dict):
                        continue
                    access_token = self._get_token(entry, "access_token")
                    email = jwt_email(access_token)
                    if email == "-":
                        continue
                    desired = email_label(email, str(entry.get("label") or path.stem))
                    unique = desired
                    suffix = 2
                    while unique in used_labels:
                        unique = f"{desired}_{suffix}"
                        suffix += 1
                    used_labels.add(unique)
                    if entry.get("label") != unique:
                        entry["label"] = unique
                        changed = True
        elif isinstance(data.get("access"), str) or isinstance(data.get("tokens"), dict):
            access_token = None
            tokens = data.get("tokens")
            if isinstance(tokens, dict):
                access_token = self._get_token(tokens, "access_token", "access")
            else:
                access_token = self._get_token(data, "access")
            email = jwt_email(access_token)
            if email != "-":
                desired = email_label(email, str(data.get("label") or path.stem))
                if data.get("label") != desired:
                    data["label"] = desired
                    changed = True
        if changed and self.logger is not None:
            self.logger.info(f"Labels normalizados em {path.name}")
        return changed

    def _unique_label(self, pool: list[Any], base_label: str) -> str:
        seen: set[str] = set()
        for entry in pool:
            if isinstance(entry, dict):
                label = entry.get("label")
                if isinstance(label, str) and label:
                    seen.add(label)
        if base_label not in seen:
            return base_label
        suffix = 2
        while f"{base_label}_{suffix}" in seen:
            suffix += 1
        return f"{base_label}_{suffix}"

    def _backup_file(self, path: Path) -> None:
        if not path.exists():
            return
        backup_path = path.with_name(f"{path.stem}.bak{path.suffix}")
        try:
            backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as exc:
            if self.logger is not None:
                self.logger.warning(f"Falha ao criar backup de {path.name}: {exc}")
