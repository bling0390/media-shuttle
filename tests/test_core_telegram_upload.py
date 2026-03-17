import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path("media-shuttle-core").resolve()))

from core.models import DownloadResult
from core.providers.uploaders_sites import telegram
from core.providers.uploaders_sites.telegram import parse_telegram_destination, upload_telegram_live


class _FakeMessage:
    def __init__(self, chat_id: int, message_id: int) -> None:
        self.chat = type("Chat", (), {"id": chat_id})()
        self.id = message_id


class _FakeClient:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.started = False
        self.stopped = False
        self.calls = []

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def send_document(self, **kwargs):
        self.calls.append(kwargs)
        return self.message


class _ConnectionBrokenClient(_FakeClient):
    def send_document(self, **kwargs):
        self.calls.append(kwargs)
        raise ConnectionError("connection lost")


class _PermissionDeniedClient(_FakeClient):
    def send_document(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("chat write forbidden")


class TestCoreTelegramUpload(unittest.TestCase):
    def tearDown(self):
        telegram._close_telegram_client()

    def test_parse_telegram_destination_should_accept_username(self):
        target = parse_telegram_destination("tg://chat/@media_shuttle")
        self.assertEqual(target.chat_ref, "@media_shuttle")

    def test_parse_telegram_destination_should_accept_numeric_chat_id(self):
        target = parse_telegram_destination("tg://chat/-1001234567890")
        self.assertEqual(target.chat_ref, "-1001234567890")

    def test_parse_telegram_destination_should_reject_invalid_format(self):
        with self.assertRaises(ValueError):
            parse_telegram_destination("channel:@media_shuttle")

    def test_upload_telegram_live_should_require_credentials(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "demo.bin"
            path.write_bytes(b"demo")
            download = DownloadResult(
                site="GOFILE",
                source_url="https://example.com/demo.bin",
                local_path=str(path),
                size_bytes=4,
                file_name="demo.bin",
            )

            env_patch = {
                "MEDIA_SHUTTLE_TG_API_ID": "",
                "MEDIA_SHUTTLE_TG_API_HASH": "",
                "MEDIA_SHUTTLE_TG_BOT_TOKEN": "",
                "TELEGRAM_API_ID": "",
                "TELEGRAM_API_HASH": "",
                "TELEGRAM_BOT_TOKEN": "",
            }
            with patch.dict(os.environ, env_patch, clear=False):
                with self.assertRaises(RuntimeError):
                    upload_telegram_live(download, "tg://chat/@media_shuttle")

    def test_upload_telegram_live_should_send_document_and_return_location(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "demo.bin"
            path.write_bytes(b"demo")
            download = DownloadResult(
                site="GOFILE",
                source_url="https://example.com/demo.bin",
                local_path=str(path),
                size_bytes=4,
                file_name="demo.bin",
            )
            client = _FakeClient(_FakeMessage(chat_id=-1001234567890, message_id=321))

            with patch(
                "core.providers.uploaders_sites.telegram._build_telegram_client",
                return_value=client,
            ):
                out = upload_telegram_live(download, "tg://chat/@media_shuttle")

            self.assertTrue(client.started)
            self.assertFalse(client.stopped)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0]["chat_id"], "@media_shuttle")
            self.assertEqual(client.calls[0]["document"], str(path))
            self.assertEqual(out.location, "telegram://chat/-1001234567890/message/321")

    def test_upload_telegram_live_should_reuse_process_local_client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "demo.bin"
            path.write_bytes(b"demo")
            download = DownloadResult(
                site="GOFILE",
                source_url="https://example.com/demo.bin",
                local_path=str(path),
                size_bytes=4,
                file_name="demo.bin",
            )
            client = _FakeClient(_FakeMessage(chat_id=-1001234567890, message_id=321))

            with patch(
                "core.providers.uploaders_sites.telegram._build_telegram_client",
                return_value=client,
            ) as build_client:
                first = upload_telegram_live(download, "tg://chat/@media_shuttle")
                second = upload_telegram_live(download, "tg://chat/@media_shuttle")

            self.assertEqual(build_client.call_count, 1)
            self.assertTrue(client.started)
            self.assertFalse(client.stopped)
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(first.location, "telegram://chat/-1001234567890/message/321")
            self.assertEqual(second.location, "telegram://chat/-1001234567890/message/321")

    def test_upload_telegram_live_should_invalidate_singleton_on_connection_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "demo.bin"
            path.write_bytes(b"demo")
            download = DownloadResult(
                site="GOFILE",
                source_url="https://example.com/demo.bin",
                local_path=str(path),
                size_bytes=4,
                file_name="demo.bin",
            )
            client = _ConnectionBrokenClient(_FakeMessage(chat_id=-1001234567890, message_id=321))

            with patch(
                "core.providers.uploaders_sites.telegram._build_telegram_client",
                return_value=client,
            ):
                with self.assertRaises(ConnectionError):
                    upload_telegram_live(download, "tg://chat/@media_shuttle")

            self.assertTrue(client.started)
            self.assertTrue(client.stopped)
            self.assertIsNone(telegram._TELEGRAM_CLIENT)

    def test_upload_telegram_live_should_keep_singleton_on_non_connection_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "demo.bin"
            path.write_bytes(b"demo")
            download = DownloadResult(
                site="GOFILE",
                source_url="https://example.com/demo.bin",
                local_path=str(path),
                size_bytes=4,
                file_name="demo.bin",
            )
            client = _PermissionDeniedClient(_FakeMessage(chat_id=-1001234567890, message_id=321))

            with patch(
                "core.providers.uploaders_sites.telegram._build_telegram_client",
                return_value=client,
            ):
                with self.assertRaises(RuntimeError):
                    upload_telegram_live(download, "tg://chat/@media_shuttle")

            self.assertTrue(client.started)
            self.assertFalse(client.stopped)
            self.assertIs(telegram._TELEGRAM_CLIENT, client)


if __name__ == "__main__":
    unittest.main()
