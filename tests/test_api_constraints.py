"""Tests de restricciones de API para client_order_id de Binance.

Binance limita los clientOrderId a 36 caracteres.
Nuestro límite seguro es 32 caracteres (margen de 4 para emergencias).
"""

import random
import string
import unittest

from core.reconciliation import _MAX_BINANCE_ID_LEN, _MAX_SAFE_ID_LEN, generate_order_ids


def _random_string(length: int) -> str:
    """Genera un string aleatorio de longitud fija."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


class TestAPIConstraints(unittest.TestCase):
    """Verifica que los IDs cumplan con los límites de Binance."""

    def test_generate_1000_ids_within_limits(self):
        """Genera 1000 IDs aleatorios y verifica longitudes."""
        random.seed(42)  # Deterministic para el test
        failures = []

        for i in range(1000):
            symbol = f"{_random_string(3)}/USDT"
            side = random.choice(["BUY", "SELL"])
            signal_ts = random.uniform(1_700_000_000, 1_800_000_000)
            instance_id = _random_string(random.randint(8, 20))

            try:
                entry_id, sl_id, tp_id = generate_order_ids(symbol, side, signal_ts, instance_id)
            except Exception as e:
                failures.append(f"#{i}: Exception: {e}")
                continue

            for name, oid in [("entry", entry_id), ("sl", sl_id), ("tp", tp_id)]:
                length = len(oid)
                if length > _MAX_SAFE_ID_LEN:
                    failures.append(
                        f"#{i} {name}: {length} chars exceeds safe limit "
                        f"({_MAX_SAFE_ID_LEN}): '{oid}'"
                    )
                if length > _MAX_BINANCE_ID_LEN:
                    failures.append(
                        f"#{i} {name}: {length} chars exceeds Binance limit "
                        f"({_MAX_BINANCE_ID_LEN}): '{oid}'"
                    )

        if failures:
            self.fail(f"{len(failures)} IDs exceeded limits:\n" + "\n".join(failures[:10]))

    def test_ids_are_deterministic(self):
        """El mismo input debe producir el mismo output."""
        symbol = "BTC/USDT"
        side = "BUY"
        signal_ts = 1_750_000_000.123456
        instance_id = "test-instance-123"

        id1 = generate_order_ids(symbol, side, signal_ts, instance_id)
        id2 = generate_order_ids(symbol, side, signal_ts, instance_id)

        self.assertEqual(id1, id2)

    def test_different_inputs_produce_different_ids(self):
        """Diferentes inputs deben producir IDs distintos."""
        symbol = "BTC/USDT"
        side = "BUY"
        signal_ts = 1_750_000_000.123456
        instance_id = "test-instance-123"

        id1 = generate_order_ids(symbol, side, signal_ts, instance_id)

        # Cambiar symbol
        id2 = generate_order_ids("ETH/USDT", side, signal_ts, instance_id)
        self.assertNotEqual(id1, id2)

        # Cambiar side
        id3 = generate_order_ids(symbol, "SELL", signal_ts, instance_id)
        self.assertNotEqual(id1, id3)
        self.assertNotEqual(id2, id3)

    def test_id_format_and_length(self):
        """Verifica formato y longitud exacta."""
        symbol = "BTC/USDT"
        side = "BUY"
        signal_ts = 1_750_000_000.123456
        instance_id = "abc123"

        entry_id, sl_id, tp_id = generate_order_ids(symbol, side, signal_ts, instance_id)

        # Verificar prefijos
        self.assertTrue(
            entry_id.startswith("E_"),
            f"Entry ID debe empezar con 'E_': {entry_id}",
        )
        self.assertTrue(sl_id.startswith("S_"), f"SL ID debe empezar con 'S_': {sl_id}")
        self.assertTrue(tp_id.startswith("T_"), f"TP ID debe empezar con 'T_': {tp_id}")

        # Verificar longitudes <= 32
        for name, oid in [("entry", entry_id), ("sl", sl_id), ("tp", tp_id)]:
            self.assertLessEqual(
                len(oid),
                _MAX_SAFE_ID_LEN,
                f"{name} ID '{oid}' excede {_MAX_SAFE_ID_LEN} chars: {len(oid)}",
            )
            # Verificar que sea razonable (más de 10 chars)
            self.assertGreater(len(oid), 10, f"{name} ID '{oid}' es demasiado corto: {len(oid)}")

    def test_ids_do_not_contain_pipe_or_whitespace(self):
        """Los IDs no deben contener caracteres problemáticos."""
        symbol = "BTC/USDT"
        side = "BUY"
        signal_ts = 1_750_000_000.123456
        instance_id = "test-123"

        entry_id, sl_id, tp_id = generate_order_ids(symbol, side, signal_ts, instance_id)

        for name, oid in [("entry", entry_id), ("sl", sl_id), ("tp", tp_id)]:
            self.assertNotIn("|", oid, f"{name} ID contiene '|': {oid}")
            self.assertNotIn(" ", oid, f"{name} ID contiene espacio: {oid}")
            self.assertNotIn("\t", oid, f"{name} ID contiene tab: {oid}")


if __name__ == "__main__":
    unittest.main()
