from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .models import AccountRecord
from .utils import format_remaining, format_reset_abs


@dataclass
class AccountViewState:
    records: list[AccountRecord] = field(default_factory=list)
    filter_text: str = ""
    filter_status: str = "Todos"
    filter_workspace: str = "Todos"
    sort_column: str = "source"
    sort_descending: bool = False
    selected_record_id: str | None = None

    def set_records(self, records: Iterable[AccountRecord]) -> None:
        self.records = list(records)
        valid_ids = {record.record_id for record in self.records}
        if self.selected_record_id not in valid_ids:
            self.selected_record_id = None

    def visible_records(self) -> list[AccountRecord]:
        filtered = [record for record in self.records if self._matches_filters(record)]
        return sorted(
            filtered,
            key=lambda record: self._sort_value(record, self.sort_column),
            reverse=self.sort_descending,
        )

    def status_choices(self) -> list[str]:
        statuses = sorted({record.status for record in self.records})
        return ["Todos", *statuses]

    def workspace_choices(self) -> list[str]:
        workspaces = sorted({record.workspace_name for record in self.records if record.workspace_name and record.workspace_name != "-"})
        return ["Todos", *workspaces]

    def select(self, record_id: str | None) -> None:
        self.selected_record_id = record_id

    def selected_record(self) -> AccountRecord | None:
        if self.selected_record_id is None:
            return None
        for record in self.records:
            if record.record_id == self.selected_record_id:
                return record
        return None

    def detail_text(self) -> str:
        record = self.selected_record()
        if record is None:
            return "Nenhuma conta selecionada."
        lines = [
            f"Label: {record.label}",
            f"Ficheiro: {record.source_path}",
            f"Formato: {record.format_kind}",
            f"Status: {record.status}",
            f"HTTP: {record.http_status if record.http_status is not None else '-'}",
            f"Email: {record.email}",
            f"Account ID: {record.account_id or '-'}",
            f"Workspace: {record.workspace_name}",
            f"Expira: {record.token_expiry}",
            f"Cota 5H: {self._fmt_pct(record.five_hour_pct)}",
            f"Reset 5H: {format_remaining(record.five_hour_reset)}",
            f"Cota semanal: {self._fmt_pct(record.weekly_pct)}",
            f"Reset semanal: {format_reset_abs(record.weekly_reset)}",
            f"Pode renew: {'sim' if record.can_renew else 'nao'}",
            f"Ultimo erro: {record.last_error or '-'}",
        ]
        return "\n".join(lines)

    def toggle_sort(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False

    def _matches_filters(self, record: AccountRecord) -> bool:
        text = self.filter_text.strip().lower()
        if text:
            haystack = " ".join(
                [
                    record.label,
                    str(record.source_path.name),
                    record.email,
                    record.workspace_name,
                    record.status,
                ]
            ).lower()
            if text not in haystack:
                return False
        if self.filter_status != "Todos" and record.status != self.filter_status:
            return False
        if self.filter_workspace != "Todos" and record.workspace_name != self.filter_workspace:
            return False
        return True

    def _sort_value(self, record: AccountRecord, column: str) -> object:
        if column == "label":
            return record.label.lower()
        if column == "status":
            return record.status
        if column == "http":
            return -1 if record.http_status is None else record.http_status
        if column == "five_hour":
            return -1.0 if record.five_hour_pct is None else record.five_hour_pct
        if column == "weekly":
            return -1.0 if record.weekly_pct is None else record.weekly_pct
        if column == "workspace":
            return record.workspace_name.lower()
        if column == "email":
            return record.email.lower()
        if column == "expiry":
            return -1.0 if record.token_expiry_epoch is None else record.token_expiry_epoch
        return str(record.source_path).lower()

    def _fmt_pct(self, value: float | None) -> str:
        if value is None:
            return "-"
        return f"{int(round(value))}%"
