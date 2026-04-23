from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

FormatKind = Literal["credential_pool", "tokens", "access"]


@dataclass(frozen=True, slots=True)
class CredentialRef:
    source_path: Path
    format_kind: FormatKind
    entry_index: int | None = None

    @property
    def key(self) -> str:
        index = "-" if self.entry_index is None else str(self.entry_index)
        return f"{self.source_path}|{self.format_kind}|{index}"


@dataclass(slots=True)
class CredentialEntry:
    ref: CredentialRef
    label: str
    access_token: str | None
    refresh_token: str | None
    account_id: str | None
    raw: dict[str, Any]


@dataclass(slots=True)
class AccountRecord:
    record_id: str
    ref: CredentialRef
    source_path: Path
    label: str
    email: str
    account_id: str | None
    workspace_name: str
    token_expiry: str
    token_expiry_epoch: float | None
    http_status: int | None
    five_hour_pct: float | None
    five_hour_reset: float | None
    weekly_pct: float | None
    weekly_reset: float | None
    status: str
    last_error: str
    can_renew: bool
    format_kind: FormatKind


@dataclass(slots=True)
class ApiResponse:
    status_code: int
    json_body: dict[str, Any] | list[Any] | None
    text: str


@dataclass(slots=True)
class DeviceCodeSession:
    user_code: str
    device_auth_id: str
    verification_uri: str
    interval_seconds: int = 5
    expires_in_seconds: int = 300


@dataclass(slots=True)
class BrowserLoginSession:
    authorize_url: str
    state: str
    code_verifier: str


@dataclass(slots=True)
class DevicePollResult:
    status: Literal["approved", "pending", "slow_down", "denied", "expired", "error"]
    message: str
    authorization_code: str | None = None
    code_verifier: str | None = None
    error_code: str | None = None
