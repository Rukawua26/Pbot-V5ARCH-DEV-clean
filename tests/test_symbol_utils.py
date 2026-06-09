import unittest

from core.symbol_utils import normalize_position_symbol


class SymbolUtilsTest(unittest.TestCase):
    def test_normalizes_exchange_position_symbols(self):
        self.assertEqual(normalize_position_symbol("BTCUSDT"), "BTC/USDT")
        self.assertEqual(normalize_position_symbol("ETH/USDT:USDT"), "ETH/USDT")
        self.assertEqual(normalize_position_symbol("wlfiusdt"), "WLFI/USDT")

    def test_normalizes_legacy_whitespace_symbol(self):
        self.assertEqual(normalize_position_symbol("WLF I/USDT"), "WLFI/USDT")

    def test_strict_mode_rejects_invalid_or_short_base(self):
        self.assertEqual(normalize_position_symbol("", default_quote="USDT", strict=True), "")
        self.assertEqual(normalize_position_symbol("X", default_quote="USDT", strict=True), "")
        self.assertEqual(
            normalize_position_symbol("BTC", default_quote="USDT", strict=True), "BTC/USDT"
        )


if __name__ == "__main__":
    unittest.main()
