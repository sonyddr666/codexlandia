from __future__ import annotations

import base64
import json
import shutil
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from explodir_core.client import OpenAIAPIError
from explodir_core.logging_utils import AppLogger
from explodir_core.models import (
    AccountRecord,
    ApiResponse,
    BrowserLoginSession,
    CredentialRef,
    DeviceCodeSession,
    DevicePollResult,
)
from explodir_core.service import ExplodirService
from explodir_core.store import CredentialStore
from explodir_core.tasks import AutoRefreshController, EventBus, SerializedRunner
from explodir_core.ui_state import AccountViewState
from explodir_core.utils import jwt_email, jwt_exp_display, jwt_user_id, parse_quota


def make_token(email: str, account_id: str, exp: int | None = None) -> str:
    exp = exp or int(time.time()) + 3600
    payload = {
        "email": email,
        "exp": exp,
        "https://api.openai.com/profile": {"email": email},
        "https://api.openai.com/auth": {
            "chatgpt_account_id": account_id,
            "chatgpt_user_id": f"user-{email}",
            "user_id": f"user-{email}",
        },
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"header.{encoded}.sig"


class FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self) -> dict:
        if self._json_body is None:
            raise ValueError("no json")
        return self._json_body


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        return self.response


class FakeRenewFailureClient:
    def renew_token(self, refresh_token: str) -> dict:
        raise OpenAIAPIError(f"boom: {refresh_token}")

    def get_usage(self, access_token: str, account_id: str | None) -> ApiResponse:
        return ApiResponse(status_code=401, json_body=None, text="unauthorized")

    def get_workspace_name(self, access_token: str, account_id: str | None) -> str | None:
        return None


class FakeBrowserLoginClient:
    def __init__(self) -> None:
        self.wait_called = False
        self.exchange_args: tuple[str, str] | None = None

    def create_browser_login_session(self) -> BrowserLoginSession:
        return BrowserLoginSession(
            authorize_url="https://auth.openai.com/oauth/authorize?state=test-state",
            state="test-state",
            code_verifier="test-verifier",
        )

    def wait_for_browser_callback(
        self,
        session_data: BrowserLoginSession,
        cancel_event: threading.Event | None = None,
        timeout_seconds: int = 300,
    ) -> str:
        self.wait_called = True
        return "browser-auth-code"

    def exchange_browser_code(self, authorization_code: str, code_verifier: str) -> dict[str, str]:
        self.exchange_args = (authorization_code, code_verifier)
        return {
            "access_token": make_token("browser@example.com", "acc_browser"),
            "refresh_token": "refresh_browser",
        }

    def get_usage(self, access_token: str, account_id: str | None) -> ApiResponse:
        return ApiResponse(status_code=200, json_body={"percent_left": 88}, text="")

    def get_workspace_name(self, access_token: str, account_id: str | None) -> str | None:
        return "Workspace Browser"


class FakeDeviceCodeClient:
    def __init__(self) -> None:
        self.poll_count = 0
        self.exchange_args: tuple[str, str] | None = None

    def request_device_code(self) -> DeviceCodeSession:
        return DeviceCodeSession(
            user_code="YLML-NRA3P",
            device_auth_id="device-auth-id",
            verification_uri="https://auth.openai.com/codex/device",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    def poll_device_token(self, session_data: DeviceCodeSession) -> DevicePollResult:
        self.poll_count += 1
        if self.poll_count == 1:
            return DevicePollResult(status="pending", message="A escutar a OpenAI...")
        return DevicePollResult(
            status="approved",
            message="Aprovado",
            authorization_code="device-auth-code",
            code_verifier="device-verifier",
        )

    def exchange_device_code(self, authorization_code: str, code_verifier: str) -> dict[str, str]:
        self.exchange_args = (authorization_code, code_verifier)
        return {
            "access_token": make_token("device@example.com", "acc_device"),
            "refresh_token": "refresh_device",
        }

    def get_usage(self, access_token: str, account_id: str | None) -> ApiResponse:
        return ApiResponse(status_code=200, json_body={"percent_left": 75}, text="")

    def get_workspace_name(self, access_token: str, account_id: str | None) -> str | None:
        return "Workspace Device"


class ExplodirCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_root = ROOT / ".tmp-tests"
        temp_root.mkdir(parents=True, exist_ok=True)
        self.base_path = temp_root / f"case-{uuid.uuid4().hex}"
        self.base_path.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.base_path, ignore_errors=True)

    def _logger(self) -> AppLogger:
        return AppLogger(self.base_path / "hermes_monitor.log")

    def test_discover_entries_supports_all_formats(self) -> None:
        access_a = make_token("pool@example.com", "acc_pool")
        access_b = make_token("tokens@example.com", "acc_tokens")
        access_c = make_token("access@example.com", "acc_access")

        (self.base_path / "pool.json").write_text(
            json.dumps(
                {
                    "credential_pool": {
                        "openai-codex": [
                            {
                                "label": "pool-entry",
                                "access_token": access_a,
                                "refresh_token": "refresh_pool",
                                "account_id": "acc_pool",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.base_path / "tokens.json").write_text(
            json.dumps(
                {
                    "label": "tokens-entry",
                    "tokens": {
                        "access_token": access_b,
                        "refresh_token": "refresh_tokens",
                        "account_id": "acc_tokens",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.base_path / "access.json").write_text(
            json.dumps(
                {
                    "label": "access-entry",
                    "access": access_c,
                    "refresh": "refresh_access",
                    "accountId": "acc_access",
                }
            ),
            encoding="utf-8",
        )

        entries = CredentialStore(logger=self._logger()).discover_entries(self.base_path)
        self.assertEqual(3, len(entries))
        self.assertEqual({"credential_pool", "tokens", "access"}, {entry.ref.format_kind for entry in entries})

    def test_discover_entries_ignores_backup_json(self) -> None:
        access = make_token("pool@example.com", "acc_pool")
        payload = {
            "credential_pool": {
                "openai-codex": [
                    {
                        "label": "pool-entry",
                        "access_token": access,
                        "refresh_token": "refresh_pool",
                        "account_id": "acc_pool",
                    }
                ]
            }
        }
        (self.base_path / "auth(infinity).json").write_text(json.dumps(payload), encoding="utf-8")
        (self.base_path / "auth(infinity).bak.json").write_text(json.dumps(payload), encoding="utf-8")

        entries = CredentialStore(logger=self._logger()).discover_entries(self.base_path)
        self.assertEqual(1, len(entries))

    def test_update_tokens_preserves_original_shape(self) -> None:
        new_tokens = {"access_token": "new_access", "refresh_token": "new_refresh", "expires_in": 300}
        store = CredentialStore(logger=self._logger())

        cases = {
            "credential_pool": (
                {
                    "credential_pool": {
                        "openai-codex": [
                            {
                                "label": "pool-entry",
                                "access_token": "old_access",
                                "refresh_token": "old_refresh",
                                "account_id": "acc_pool",
                            }
                        ]
                    }
                },
                CredentialRef(self.base_path / "pool.json", "credential_pool", 0),
            ),
            "tokens": (
                {
                    "label": "tokens-entry",
                    "expires": 0,
                    "tokens": {
                        "access_token": "old_access",
                        "refresh_token": "old_refresh",
                        "account_id": "acc_tokens",
                    },
                },
                CredentialRef(self.base_path / "tokens.json", "tokens"),
            ),
            "access": (
                {
                    "label": "access-entry",
                    "access": "old_access",
                    "refresh": "old_refresh",
                    "expires": 0,
                    "accountId": "acc_access",
                },
                CredentialRef(self.base_path / "access.json", "access"),
            ),
        }

        for _name, (payload, ref) in cases.items():
            ref.source_path.write_text(json.dumps(payload), encoding="utf-8")
            store.update_tokens(ref, new_tokens)
            updated = json.loads(ref.source_path.read_text(encoding="utf-8"))
            if ref.format_kind == "credential_pool":
                entry = updated["credential_pool"]["openai-codex"][0]
                self.assertEqual("new_access", entry["access_token"])
                self.assertEqual("new_refresh", entry["refresh_token"])
            elif ref.format_kind == "tokens":
                self.assertIn("tokens", updated)
                self.assertEqual("new_access", updated["tokens"]["access_token"])
                self.assertEqual("new_refresh", updated["tokens"]["refresh_token"])
                self.assertIn("expires", updated)
            else:
                self.assertIn("access", updated)
                self.assertEqual("new_access", updated["access"])
                self.assertEqual("new_refresh", updated["refresh"])
                self.assertIn("expires", updated)

    def test_parse_quota_and_jwt_expiry_helpers(self) -> None:
        quota = parse_quota(
            {
                "plan_type": "plus",
                "limits": {
                    "primary_window": {"reset_time_ms": 1_900_000_000_000},
                    "percent_left": 55,
                },
                "weekly": {
                    "remaining_percent": 25,
                    "reset_at": 1_900_000_100,
                },
            }
        )
        self.assertEqual("plus", quota["plan"])
        self.assertEqual(55.0, quota["five_hour_pct"])
        self.assertEqual(25.0, quota["weekly_pct"])

        token = make_token("expiry@example.com", "acc_expiry", exp=int(time.time()) + 600)
        self.assertNotEqual("-", jwt_exp_display(token))

    def test_renew_failure_keeps_file_and_entry(self) -> None:
        token = make_token("user@example.com", "acc_user", exp=int(time.time()) - 60)
        path = self.base_path / "tokens.json"
        payload = {
            "label": "tokens-entry",
            "tokens": {
                "access_token": token,
                "refresh_token": "refresh_user",
                "account_id": "acc_user",
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

        service = ExplodirService(
            store=CredentialStore(logger=self._logger()),
            client=FakeRenewFailureClient(),
            logger=self._logger(),
        )
        records = service.renew_entries([CredentialRef(path, "tokens")], self.base_path)
        self.assertTrue(path.exists())
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("refresh_user", persisted["tokens"]["refresh_token"])
        self.assertEqual(1, len(records))
        self.assertNotEqual("RENOVADO", records[0].status)

    def test_upsert_account_matches_by_account_id_then_email(self) -> None:
        store = CredentialStore(logger=self._logger())
        first = {
            "access_token": make_token("same@example.com", "acc_same"),
            "refresh_token": "refresh_1",
        }
        second_same_account = {
            "access_token": make_token("same@example.com", "acc_same"),
            "refresh_token": "refresh_2",
        }
        first_path = store.upsert_account(self.base_path, first)
        self.assertTrue(first_path.exists())
        store.upsert_account(self.base_path, second_same_account)
        saved = json.loads(first_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(saved["credential_pool"]["openai-codex"]))
        self.assertEqual("refresh_2", saved["credential_pool"]["openai-codex"][0]["refresh_token"])
        self.assertEqual("same", saved["credential_pool"]["openai-codex"][0]["label"])

        email_file = self.base_path / "auth(infinity).json"
        email_file.write_text(
            json.dumps(
                {
                    "credential_pool": {
                        "openai-codex": [
                            {
                                "label": "manual",
                                "auth_type": "oauth",
                                "access_token": make_token("email-only@example.com", "old_acc"),
                                "refresh_token": "refresh_old",
                                "account_id": None,
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        store.upsert_account(
            self.base_path,
            {
                "access_token": make_token("email-only@example.com", "new_acc"),
                "refresh_token": "refresh_new",
            },
        )
        saved = json.loads(email_file.read_text(encoding="utf-8"))
        self.assertEqual(1, len(saved["credential_pool"]["openai-codex"]))
        self.assertEqual("refresh_new", saved["credential_pool"]["openai-codex"][0]["refresh_token"])
        self.assertEqual("email-only", saved["credential_pool"]["openai-codex"][0]["label"])

    def test_upsert_account_rewrites_stale_label_from_current_email(self) -> None:
        store = CredentialStore(logger=self._logger())
        auth_file = self.base_path / "auth(infinity).json"
        auth_file.write_text(
            json.dumps(
                {
                    "credential_pool": {
                        "openai-codex": [
                            {
                                "label": "oldlabel",
                                "auth_type": "oauth",
                                "access_token": make_token("real@example.com", "acc_real"),
                                "refresh_token": "refresh_old",
                                "account_id": "acc_real",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        store.upsert_account(
            self.base_path,
            {
                "access_token": make_token("real@example.com", "acc_real"),
                "refresh_token": "refresh_new",
            },
        )

        saved = json.loads(auth_file.read_text(encoding="utf-8"))
        self.assertEqual("real", saved["credential_pool"]["openai-codex"][0]["label"])

    def test_upsert_account_does_not_overwrite_conflicting_identity(self) -> None:
        store = CredentialStore(logger=self._logger())
        auth_file = self.base_path / "auth(infinity).json"
        auth_file.write_text(
            json.dumps(
                {
                    "credential_pool": {
                        "openai-codex": [
                            {
                                "label": "larriduarte34",
                                "auth_type": "oauth",
                                "access_token": make_token("old@example.com", "acc_old"),
                                "refresh_token": "refresh_old",
                                "account_id": "acc_old",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        store.upsert_account(
            self.base_path,
            {
                "access_token": make_token("new@example.com", "acc_new"),
                "refresh_token": "refresh_new",
            },
        )

        saved = json.loads(auth_file.read_text(encoding="utf-8"))
        entries = saved["credential_pool"]["openai-codex"]
        self.assertEqual(2, len(entries))
        self.assertEqual("larriduarte34", entries[0]["label"])
        self.assertEqual("old@example.com", jwt_email(entries[0]["access_token"]))
        self.assertEqual("new", entries[1]["label"])
        self.assertEqual("new@example.com", jwt_email(entries[1]["access_token"]))
        self.assertNotEqual(jwt_user_id(entries[0]["access_token"]), jwt_user_id(entries[1]["access_token"]))

    def test_upsert_account_creates_backup_before_write(self) -> None:
        store = CredentialStore(logger=self._logger())
        auth_file = self.base_path / "auth(infinity).json"
        auth_file.write_text(
            json.dumps(
                {
                    "credential_pool": {
                        "openai-codex": [
                            {
                                "label": "demo",
                                "auth_type": "oauth",
                                "access_token": make_token("demo@example.com", "acc_demo"),
                                "refresh_token": "refresh_demo",
                                "account_id": "acc_demo",
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        store.upsert_account(
            self.base_path,
            {
                "access_token": make_token("demo@example.com", "acc_demo"),
                "refresh_token": "refresh_new",
            },
        )

        backup_file = self.base_path / "auth(infinity).bak.json"
        self.assertTrue(backup_file.exists())
        backed_up = json.loads(backup_file.read_text(encoding="utf-8"))
        self.assertEqual("refresh_demo", backed_up["credential_pool"]["openai-codex"][0]["refresh_token"])

    def test_delete_entry_removes_selected_pool_entry(self) -> None:
        store = CredentialStore(logger=self._logger())
        auth_file = self.base_path / "auth(infinity).json"
        auth_file.write_text(
            json.dumps(
                {
                    "credential_pool": {
                        "openai-codex": [
                            {
                                "label": "one",
                                "auth_type": "oauth",
                                "access_token": make_token("one@example.com", "acc_one"),
                                "refresh_token": "refresh_one",
                                "account_id": "acc_one",
                            },
                            {
                                "label": "two",
                                "auth_type": "oauth",
                                "access_token": make_token("two@example.com", "acc_two"),
                                "refresh_token": "refresh_two",
                                "account_id": "acc_two",
                            },
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        store.delete_entry(CredentialRef(auth_file, "credential_pool", 0))

        saved = json.loads(auth_file.read_text(encoding="utf-8"))
        entries = saved["credential_pool"]["openai-codex"]
        self.assertEqual(1, len(entries))
        self.assertEqual("two@example.com", jwt_email(entries[0]["access_token"]))

    def test_serialized_runner_posts_events_and_blocks_overlap(self) -> None:
        bus = EventBus()
        runner = SerializedRunner(bus)
        hold = threading.Event()
        release = threading.Event()

        def long_task() -> str:
            hold.set()
            release.wait(timeout=2)
            return "done"

        self.assertTrue(runner.run("scan", long_task))
        hold.wait(timeout=2)
        self.assertFalse(runner.run("renew", lambda: "nope"))
        release.set()

        deadline = time.time() + 3
        events = []
        while time.time() < deadline and len(events) < 3:
            try:
                events.append(bus.queue.get(timeout=0.5).kind)
            except Exception:
                break
        self.assertIn("worker_started", events)
        self.assertIn("worker_result", events)
        self.assertIn("worker_finished", events)

    def test_view_state_detail_tracks_selection(self) -> None:
        record = AccountRecord(
            record_id="record-1",
            ref=CredentialRef(self.base_path / "tokens.json", "tokens"),
            source_path=self.base_path / "tokens.json",
            label="demo",
            email="demo@example.com",
            account_id="acc_demo",
            workspace_name="Workspace Demo",
            token_expiry="01/01 10:00",
            token_expiry_epoch=123.0,
            http_status=200,
            five_hour_pct=88.0,
            five_hour_reset=999999999.0,
            weekly_pct=44.0,
            weekly_reset=1000009999.0,
            status="OK",
            last_error="",
            can_renew=True,
            format_kind="tokens",
        )
        state = AccountViewState()
        state.set_records([record])
        state.select("record-1")
        detail = state.detail_text()
        self.assertIn("demo@example.com", detail)
        self.assertIn("Workspace Demo", detail)
        self.assertIn(str(self.base_path / "tokens.json"), detail)

    def test_auto_refresh_controller_avoids_duplicate_schedule(self) -> None:
        controller = AutoRefreshController(interval_seconds=10)
        controller.set_enabled(True)
        self.assertTrue(controller.request_schedule(worker_busy=False))
        self.assertFalse(controller.request_schedule(worker_busy=False))
        controller.mark_fired()
        self.assertFalse(controller.request_schedule(worker_busy=True))
        self.assertTrue(controller.request_schedule(worker_busy=False))

    def test_extract_authorization_code_from_callback_url(self) -> None:
        from explodir_core.client import OpenAIAuthClient

        client = OpenAIAuthClient(session=FakeSession(FakeResponse(200, {})))
        callback_url = (
            "https://auth.openai.com/deviceauth/callback?"
            "code=abc123&scope=openid+profile+email+offline_access&state=test"
        )
        self.assertEqual("abc123", client.extract_authorization_code(callback_url))
        self.assertEqual("plain-code", client.extract_authorization_code("plain-code"))

    def test_poll_device_token_treats_unknown_authorization_as_pending(self) -> None:
        from explodir_core.client import OpenAIAuthClient
        from explodir_core.models import DeviceCodeSession

        response = FakeResponse(
            400,
            {
                "error": {
                    "message": "Device authorization is unknown. Please try again.",
                    "type": "invalid_request_error",
                    "code": "deviceauth_authorization_unknown",
                }
            },
            text='{"error":{"message":"Device authorization is unknown. Please try again.","code":"deviceauth_authorization_unknown"}}',
        )
        client = OpenAIAuthClient(session=FakeSession(response))
        result = client.poll_device_token(
            DeviceCodeSession(
                user_code="CODE-123",
                device_auth_id="device-auth-id",
                verification_uri="https://auth.openai.com/codex/device",
            )
        )
        self.assertEqual("pending", result.status)
        self.assertEqual("deviceauth_authorization_unknown", result.error_code)
        self.assertIn("unknown", result.message.lower())

    def test_create_browser_login_session_uses_localhost_pkce_flow(self) -> None:
        from explodir_core.client import OpenAIAuthClient

        client = OpenAIAuthClient(session=FakeSession(FakeResponse(200, {})))
        session = client.create_browser_login_session()
        parsed = urlparse(session.authorize_url)
        query = parse_qs(parsed.query)

        self.assertEqual("https", parsed.scheme)
        self.assertEqual("/oauth/authorize", parsed.path)
        self.assertEqual(["code"], query["response_type"])
        self.assertEqual(["http://localhost:1455/auth/callback"], query["redirect_uri"])
        self.assertEqual(["S256"], query["code_challenge_method"])
        self.assertEqual(["true"], query["codex_cli_simplified_flow"])
        self.assertEqual([session.state], query["state"])
        self.assertTrue(session.code_verifier)
        self.assertTrue(query["code_challenge"][0])

    def test_parse_browser_callback_validates_state(self) -> None:
        from explodir_core.client import OpenAIAuthClient

        client = OpenAIAuthClient(session=FakeSession(FakeResponse(200, {})))
        callback_url = "/auth/callback?code=abc123&state=ok-state"
        self.assertEqual("abc123", client.parse_browser_callback(callback_url, expected_state="ok-state"))
        with self.assertRaises(OpenAIAPIError):
            client.parse_browser_callback(callback_url, expected_state="other-state")

    def test_browser_login_saves_account_without_device_polling(self) -> None:
        client = FakeBrowserLoginClient()
        service = ExplodirService(
            store=CredentialStore(logger=self._logger()),
            client=client,
            logger=self._logger(),
        )
        events: list[tuple[str, dict[str, object]]] = []
        progress: list[str] = []

        records = service.browser_login(
            self.base_path,
            progress_callback=progress.append,
            event_callback=lambda kind, **payload: events.append((kind, payload)),
            cancel_event=threading.Event(),
        )

        self.assertTrue(client.wait_called)
        self.assertEqual(("browser-auth-code", "test-verifier"), client.exchange_args)
        self.assertEqual("browser_login_ready", events[0][0])
        self.assertEqual("browser_saved", events[-1][0])
        self.assertTrue(any("Browser pronto" in message for message in progress))
        self.assertTrue((self.base_path / "auth(infinity).json").exists())
        self.assertEqual(1, len(records))
        self.assertEqual("browser@example.com", records[0].email)

    def test_device_code_login_saves_account_with_visible_code_flow(self) -> None:
        client = FakeDeviceCodeClient()
        service = ExplodirService(
            store=CredentialStore(logger=self._logger()),
            client=client,
            logger=self._logger(),
        )
        events: list[tuple[str, dict[str, object]]] = []
        progress: list[str] = []

        records = service.device_code_login(
            self.base_path,
            progress_callback=progress.append,
            event_callback=lambda kind, **payload: events.append((kind, payload)),
            cancel_event=threading.Event(),
        )

        self.assertGreaterEqual(client.poll_count, 2)
        self.assertEqual(("device-auth-code", "device-verifier"), client.exchange_args)
        self.assertEqual("device_code", events[0][0])
        self.assertTrue(any(kind == "device_poll" for kind, _payload in events))
        self.assertEqual("device_saved", events[-1][0])
        self.assertTrue(any("A escutar a OpenAI" in message for message in progress))
        self.assertTrue((self.base_path / "auth(infinity).json").exists())
        self.assertEqual(1, len(records))
        self.assertEqual("device@example.com", records[0].email)


if __name__ == "__main__":
    unittest.main()
