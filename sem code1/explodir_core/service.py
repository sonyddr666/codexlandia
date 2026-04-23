from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from .client import OpenAIAPIError, OpenAIAuthClient, OpenAITransportError
from .logging_utils import AppLogger
from .models import AccountRecord, CredentialEntry, CredentialRef, DeviceCodeSession
from .store import CredentialStore, StoreError
from .utils import (
    jwt_account_id,
    jwt_email,
    jwt_exp_display,
    jwt_exp_epoch,
    parse_quota,
)


class ExplodirService:
    def __init__(
        self,
        store: CredentialStore,
        client: OpenAIAuthClient,
        logger: AppLogger,
    ) -> None:
        self.store = store
        self.client = client
        self.logger = logger
        self._workspace_cache: dict[str, str] = {}

    def scan_directory(self, directory: Path) -> list[AccountRecord]:
        directory = Path(directory)
        self.logger.info(f"Scan iniciado em {directory}")
        records = [self.inspect_entry(entry) for entry in self.store.discover_entries(directory)]
        records.sort(key=lambda record: (str(record.source_path).lower(), record.label.lower()))
        self.logger.info(f"Scan concluido: {len(records)} conta(s) encontrada(s)")
        return records

    def inspect_entry(self, entry: CredentialEntry, status_override: str | None = None) -> AccountRecord:
        access_token = entry.access_token
        refresh_token = entry.refresh_token
        email = jwt_email(access_token)
        account_id = entry.account_id or jwt_account_id(access_token)
        token_expiry_epoch = jwt_exp_epoch(access_token)
        token_expiry = jwt_exp_display(access_token)
        can_renew = bool(refresh_token)
        workspace_name = self._workspace_cache.get(entry.ref.key, "-")
        http_status: int | None = None
        five_hour_pct: float | None = None
        five_hour_reset: float | None = None
        weekly_pct: float | None = None
        weekly_reset: float | None = None
        status = "OK"
        last_error = ""

        if not access_token:
            status = "SEM_TOKEN"
            last_error = "Entrada sem access token"
        else:
            now = time.time()
            if token_expiry_epoch is not None and token_expiry_epoch <= now:
                status = "EXPIRADO" if can_renew else "MORTO"
                last_error = "Token expirado"
            else:
                try:
                    response = self.client.get_usage(access_token, account_id)
                    http_status = response.status_code
                    if response.status_code == 200 and isinstance(response.json_body, dict):
                        quota = parse_quota(response.json_body)
                        five_hour_pct = self._float_or_none(quota.get("five_hour_pct"))
                        five_hour_reset = self._float_or_none(quota.get("five_hour_reset"))
                        weekly_pct = self._float_or_none(quota.get("weekly_pct"))
                        weekly_reset = self._float_or_none(quota.get("weekly_reset"))
                        status = "OK"
                    elif response.status_code == 429:
                        status = "ESGOTADO"
                        last_error = "HTTP 429"
                    elif response.status_code == 401:
                        status = "EXPIRADO" if can_renew else "MORTO"
                        last_error = "HTTP 401"
                    else:
                        status = f"HTTP_{response.status_code}"
                        last_error = f"HTTP {response.status_code}"
                except OpenAITransportError as exc:
                    status = "ERRO_REDE"
                    last_error = str(exc)

            if access_token and status not in {"SEM_TOKEN", "ERRO_REDE"}:
                workspace_name = self._resolve_workspace_name(entry.ref.key, access_token, account_id, workspace_name)

        if status_override and status not in {"SEM_TOKEN", "ERRO_REDE"}:
            status = status_override

        return AccountRecord(
            record_id=entry.ref.key,
            ref=entry.ref,
            source_path=entry.ref.source_path,
            label=entry.label,
            email=email,
            account_id=account_id,
            workspace_name=workspace_name,
            token_expiry=token_expiry,
            token_expiry_epoch=token_expiry_epoch,
            http_status=http_status,
            five_hour_pct=five_hour_pct,
            five_hour_reset=five_hour_reset,
            weekly_pct=weekly_pct,
            weekly_reset=weekly_reset,
            status=status,
            last_error=last_error,
            can_renew=can_renew,
            format_kind=entry.ref.format_kind,
        )

    def renew_entries(self, refs: Sequence[CredentialRef], directory: Path) -> list[AccountRecord]:
        renewed_keys: set[str] = set()
        for ref in refs:
            entry = self.store.load_entry(ref)
            if not entry.refresh_token:
                self.logger.warning(f"Renew ignorado em {ref.source_path.name}: sem refresh token")
                continue
            try:
                tokens = self.client.renew_token(entry.refresh_token)
                self.store.update_tokens(ref, tokens)
                renewed_keys.add(ref.key)
                self.logger.info(f"Renew gravado com sucesso em {ref.source_path.name}")
            except (OpenAIAPIError, OpenAITransportError, StoreError) as exc:
                self.logger.error(f"Renew falhou em {ref.source_path.name}: {exc}")
        records = self.scan_directory(directory)
        if renewed_keys:
            for record in records:
                if record.record_id in renewed_keys and record.status in {"OK", "EXPIRADO", "MORTO", "ESGOTADO"}:
                    record.status = "RENOVADO"
                    record.last_error = ""
        return records

    def delete_entries(self, refs: Sequence[CredentialRef], directory: Path) -> list[AccountRecord]:
        for ref in refs:
            try:
                self.store.delete_entry(ref)
                self.logger.info(f"Conta removida de {ref.source_path.name}")
            except StoreError as exc:
                self.logger.error(f"Remocao falhou em {ref.source_path.name}: {exc}")
        return self.scan_directory(directory)

    def request_device_code(self) -> DeviceCodeSession:
        session = self.client.request_device_code()
        self.logger.info("Device code gerado")
        return session

    def browser_login(
        self,
        directory: Path,
        progress_callback: Callable[[str], None],
        event_callback: Callable[..., None],
        cancel_event: threading.Event | None = None,
    ) -> list[AccountRecord]:
        session = self.client.create_browser_login_session()
        self.logger.info("Login por browser iniciado")
        event_callback("browser_login_ready", url=session.authorize_url)
        progress_callback("Browser pronto. Conclua o login na pagina aberta.")

        authorization_code = self.client.wait_for_browser_callback(
            session,
            cancel_event=cancel_event,
        )
        progress_callback("Login confirmado. A trocar codigo por tokens...")
        tokens = self.client.exchange_browser_code(authorization_code, session.code_verifier)
        saved_path = self.store.upsert_account(directory, tokens)
        self.logger.info(f"Conta adicionada/atualizada em {saved_path.name}")
        event_callback("browser_saved", path=str(saved_path))
        return self.scan_directory(directory)

    def device_code_login(
        self,
        directory: Path,
        progress_callback: Callable[[str], None],
        event_callback: Callable[..., None],
        cancel_event: threading.Event | None = None,
    ) -> list[AccountRecord]:
        session = self.request_device_code()
        event_callback("device_code", session=session)

        max_attempts = max(1, session.expires_in_seconds // max(session.interval_seconds, 1))
        wait_seconds = max(session.interval_seconds, 1)
        for attempt in range(max_attempts):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Fluxo cancelado")
            event_callback(
                "device_poll",
                attempt=attempt + 1,
                total=max_attempts,
                status=f"Aguardando aprovacao ({attempt + 1}/{max_attempts})",
            )
            poll_result = self.client.poll_device_token(session)
            if poll_result.status == "approved":
                progress_callback("Aprovado. A trocar codigo por tokens...")
                tokens = self.client.exchange_device_code(
                    poll_result.authorization_code or "",
                    poll_result.code_verifier or "",
                )
                saved_path = self.store.upsert_account(directory, tokens)
                self.logger.info(f"Conta adicionada/atualizada em {saved_path.name}")
                event_callback("device_saved", path=str(saved_path))
                return self.scan_directory(directory)
            if poll_result.status == "pending":
                progress_callback("A escutar a OpenAI...")
            elif poll_result.status == "slow_down":
                wait_seconds += 5
                progress_callback("A escutar a OpenAI... a abrandar polling.")
            elif poll_result.status in {"denied", "expired"}:
                raise RuntimeError(poll_result.message)
            else:
                self.logger.warning(f"Polling device code devolveu erro transitorio: {poll_result.message}")
                progress_callback("A escutar a OpenAI...")
            time.sleep(wait_seconds)
        raise RuntimeError("Tempo esgotado no device code")

    def complete_callback_login(self, directory: Path, callback_value: str) -> list[AccountRecord]:
        tokens = self.client.exchange_callback_url(callback_value)
        saved_path = self.store.upsert_account(directory, tokens)
        self.logger.info(f"Conta adicionada/atualizada via callback em {saved_path.name}")
        return self.scan_directory(directory)

    def _resolve_workspace_name(
        self,
        cache_key: str,
        access_token: str,
        account_id: str | None,
        fallback: str,
    ) -> str:
        try:
            name = self.client.get_workspace_name(access_token, account_id)
        except OpenAITransportError:
            return fallback
        if name:
            self._workspace_cache[cache_key] = name
            return name
        return fallback

    def _float_or_none(self, value: object) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
