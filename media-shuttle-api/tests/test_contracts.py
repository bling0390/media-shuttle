from __future__ import annotations

import unittest

from app.contracts import DEFAULT_RCLONE_DESTINATION, validate_create_request


class ValidateCreateRequestTests(unittest.TestCase):
    """Unit tests for the new optional-destination behavior.

    The RCLONE target now accepts a missing/empty ``destination`` and
    fills in ``DEFAULT_RCLONE_DESTINATION`` (``115:/``) so the final
    upload path is ``115:/<date>/<folder>/<file>`` rather than
    ``115:/<some-intermediate-prefix>/<date>/<folder>/<file>``.
    """

    def _base(self, **overrides):
        data = {
            "url": "https://bunkr.cr/f/xkdwMPFHh372y",
            "requester_id": "u1",
            "target": "RCLONE",
        }
        data.update(overrides)
        return data

    def test_rclone_default_destination_applied_when_missing(self) -> None:
        data = self._base()
        validate_create_request(data)
        self.assertEqual(data["destination"], DEFAULT_RCLONE_DESTINATION)
        self.assertEqual(data["destination"], "115:/")

    def test_rclone_default_destination_applied_when_empty(self) -> None:
        data = self._base(destination="")
        validate_create_request(data)
        self.assertEqual(data["destination"], "115:/")

    def test_rclone_default_destination_applied_when_whitespace(self) -> None:
        data = self._base(destination="   ")
        validate_create_request(data)
        self.assertEqual(data["destination"], "115:/")

    def test_rclone_explicit_destination_is_preserved(self) -> None:
        data = self._base(destination="115:/my-app")
        validate_create_request(data)
        self.assertEqual(data["destination"], "115:/my-app")

    def test_rclone_default_destination_constant_is_correct(self) -> None:
        # If this changes, the production upload path format changes too.
        # Lock it down with a test.
        self.assertEqual(DEFAULT_RCLONE_DESTINATION, "115:/")

    def test_rclone_missing_url_raises(self) -> None:
        data = self._base(url="")
        with self.assertRaises(ValueError) as ctx:
            validate_create_request(data)
        self.assertIn("invalid field: url", str(ctx.exception))

    def test_rclone_missing_requester_id_raises(self) -> None:
        data = self._base(requester_id="")
        with self.assertRaises(ValueError) as ctx:
            validate_create_request(data)
        self.assertIn("invalid field: requester_id", str(ctx.exception))

    def test_rclone_missing_target_raises(self) -> None:
        data = self._base(target="")
        with self.assertRaises(ValueError) as ctx:
            validate_create_request(data)
        self.assertIn("invalid field: target", str(ctx.exception))

    def test_unknown_target_raises(self) -> None:
        data = self._base(target="FTP")
        with self.assertRaises(ValueError) as ctx:
            validate_create_request(data)
        self.assertIn("invalid field: target", str(ctx.exception))

    def test_telegram_destination_required(self) -> None:
        data = self._base(target="TELEGRAM")
        with self.assertRaises(ValueError) as ctx:
            validate_create_request(data)
        self.assertIn("invalid field: destination", str(ctx.exception))

    def test_telegram_destination_default_is_not_applied(self) -> None:
        # ``115:/`` is not a valid tg destination; even if the caller
        # tried, validation must reject it (TELEGRAM has no default).
        data = self._base(target="TELEGRAM", destination="115:/")
        with self.assertRaises(ValueError) as ctx:
            validate_create_request(data)
        self.assertIn("invalid field: destination", str(ctx.exception))

    def test_telegram_valid_destination_is_accepted(self) -> None:
        # ``urlparse`` requires the ``//`` form for ``netloc`` to be
        # populated, so the canonical form is ``tg://chat/<id>``.
        data = self._base(target="TELEGRAM", destination="tg://chat/1234567890")
        validate_create_request(data)
        self.assertEqual(data["destination"], "tg://chat/1234567890")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
