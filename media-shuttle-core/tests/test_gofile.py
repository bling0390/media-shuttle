from __future__ import annotations

import unittest

from core.providers.parsers_sites.gofile import _gofile_build_website_token


class GofileWebsiteTokenTests(unittest.TestCase):
    def test_build_website_token_matches_site_logic(self) -> None:
        token = "VMEYDC4vCkynsZoeER35DVpurUnXMpxF"
        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "HeadlessChrome/145.0.0.0 Safari/537.36"
        )
        language = "zh-CN"
        now = 1773043200

        self.assertEqual(
            _gofile_build_website_token(token, user_agent, language, now=now),
            "44f2dfaadd9157f4dca3561aa771459da42939039b3a8f6137290b20d35d31e5",
        )


if __name__ == "__main__":
    unittest.main()
