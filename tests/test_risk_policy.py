import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.risk_policy import (
    activate_runtime_protection,
    evaluate_entry_risk_decision,
    evaluate_neutral_agent_vote_decision,
    evaluate_runtime_entry_decision,
)


class RiskPolicyTests(unittest.TestCase):
    def _bot(self, **kwargs):
        return SimpleNamespace(
            stop_requested=kwargs.get("stop_requested", False),
            shutdown_in_progress=kwargs.get("shutdown_in_progress", False),
            integrity_lock_active=kwargs.get("integrity_lock_active", False),
            halt_system_active=kwargs.get("halt_system_active", False),
            confidence_stagnation_lock_active=kwargs.get(
                "confidence_stagnation_lock_active", False
            ),
            circuit_breaker_active=kwargs.get("circuit_breaker_active", False),
            is_paused=kwargs.get("is_paused", False),
            mandatory_train_pending=kwargs.get("mandatory_train_pending", False),
            log=MagicMock(),
        )

    @patch("core.risk_policy.shadow_logger.is_trading_halted", return_value=False)
    def test_entry_risk_returns_shutdown_first(self, _mock_halted):
        bot = self._bot(stop_requested=True)

        decision = evaluate_entry_risk_decision(bot, "BTC/USDT", False)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "SHUTDOWN_IN_PROGRESS")

    @patch("core.risk_policy.shadow_logger.is_trading_halted", return_value=False)
    def test_entry_risk_returns_recovery_pending_when_active_state_exists(self, _mock_halted):
        bot = self._bot()

        decision = evaluate_entry_risk_decision(
            bot,
            "BTC/USDT",
            False,
            existing_state={"status": "OPEN"},
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "RECOVERY_PENDING_STATE")

    @patch("core.risk_policy.shadow_logger.is_trading_halted", return_value=True)
    def test_entry_risk_blocks_real_on_shadow_logger_halt(self, _mock_halted):
        bot = self._bot()

        decision = evaluate_entry_risk_decision(bot, "BTC/USDT", False)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "TRADING_HALTED_DB_ERROR")

    @patch("core.risk_policy.shadow_logger.is_trading_halted", return_value=False)
    def test_entry_risk_ignores_real_only_blocks_for_shadow(self, _mock_halted):
        bot = self._bot(integrity_lock_active=True, halt_system_active=True)

        decision = evaluate_entry_risk_decision(bot, "BTC/USDT", True)

        self.assertIsNone(decision)

    @patch("core.risk_policy.shadow_logger.is_trading_halted", return_value=False)
    def test_entry_risk_blocks_stagnation_for_all_modes(self, _mock_halted):
        bot = self._bot(confidence_stagnation_lock_active=True)

        decision = evaluate_entry_risk_decision(bot, "BTC/USDT", True)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "CONFIDENCE_STAGNATION_LOCK")

    @patch("core.risk_policy.send_telegram_msg")
    @patch("core.risk_policy.append_execution_event")
    def test_activate_runtime_protection_sets_flags_and_sends_once(self, _mock_evt, mock_tg):
        bot = self._bot()
        bot._circuit_breaker_alert_sent = True

        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            pause=True,
            integrity_lock=True,
            halt_system=True,
            mandatory_train_pending=True,
            log_message="risk tripped",
            telegram_message="alert",
            alert_once_attr="risk_alert_sent",
            reason="TEST_RUNTIME_PROTECTION",
        )

        self.assertTrue(bot.circuit_breaker_active)
        self.assertTrue(bot.is_paused)
        self.assertTrue(bot.integrity_lock_active)
        self.assertTrue(bot.halt_system_active)
        self.assertTrue(bot.mandatory_train_pending)
        self.assertTrue(bot.risk_alert_sent)
        mock_tg.assert_called_once_with("alert")
        self.assertTrue(mock_tg.called)

        activate_runtime_protection(
            bot,
            log_message="risk tripped again",
            telegram_message="alert",
            alert_once_attr="risk_alert_sent",
            reason="TEST_RUNTIME_PROTECTION",
        )
        mock_tg.assert_called_once_with("alert")

    @patch("core.risk_policy.send_telegram_msg")
    @patch("core.risk_policy.append_execution_event")
    def test_circuit_breaker_hook_alerts_on_first_trigger(self, mock_evt, mock_tg):
        bot = self._bot()
        bot._circuit_breaker_alert_sent = False

        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            log_message="breaker tripped",
            reason="CIRCUIT_BREAKER_PANIC",
            source="test",
        )

        self.assertTrue(bot._circuit_breaker_alert_sent)
        events = [call.args[1] for call in mock_evt.call_args_list]
        self.assertIn("CIRCUIT_BREAKER_TRIGGER_ALERT", events)
        mock_tg.assert_any_call(
            "CIRCUIT BREAKER TRIGGER: reason=CIRCUIT_BREAKER_PANIC source=test. "
            "Nuevas entradas bloqueadas. Revisar dashboard y runbook."
        )

    @patch("core.risk_policy.send_telegram_msg")
    @patch("core.risk_policy.append_execution_event")
    def test_circuit_breaker_hook_does_not_alert_on_second_trigger(self, mock_evt, mock_tg):
        bot = self._bot()
        bot._circuit_breaker_alert_sent = True

        activate_runtime_protection(
            bot,
            circuit_breaker=True,
            log_message="breaker tripped again",
            reason="CIRCUIT_BREAKER_PANIC",
        )

        cb_alerts = [
            call
            for call in mock_evt.call_args_list
            if call.args[1] == "CIRCUIT_BREAKER_TRIGGER_ALERT"
        ]
        self.assertEqual(len(cb_alerts), 0)

    def test_runtime_entry_decision_blocks_paused_bot(self):
        bot = self._bot(is_paused=True)

        decision = evaluate_runtime_entry_decision(bot, "BTC/USDT", False)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "BOT_PAUSED")

    def test_runtime_entry_decision_blocks_circuit_breaker_for_all_modes(self):
        bot = self._bot(circuit_breaker_active=True)

        decision = evaluate_runtime_entry_decision(bot, "BTC/USDT", True)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "CIRCUIT_BREAKER_PANIC")

    def test_runtime_entry_decision_blocks_real_during_ws_reconciliation(self):
        bot = self._bot()
        bot.ws_reconciliation_in_progress = True

        decision = evaluate_runtime_entry_decision(bot, "BTC/USDT", False)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "WS_RECONCILIATION_IN_PROGRESS")

    def test_runtime_entry_decision_allows_shadow_during_ws_reconciliation(self):
        bot = self._bot()
        bot.ws_reconciliation_in_progress = True

        decision = evaluate_runtime_entry_decision(bot, "BTC/USDT", True)

        self.assertIsNone(decision)

    def test_neutral_agent_vote_decision_blocks_all_neutral_votes(self):
        decision = evaluate_neutral_agent_vote_decision(
            "BTC/USDT",
            False,
            prob_final=50.0,
            votes={"MT": 50.0, "SR": 50.0, "G": 50.0},
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "NEUTRAL_AGENT_VOTE")

    def test_neutral_agent_vote_decision_ignores_missing_votes(self):
        decision = evaluate_neutral_agent_vote_decision(
            "BTC/USDT",
            False,
            prob_final=50.0,
            votes={},
        )

        self.assertIsNone(decision)

    def test_neutral_agent_vote_decision_ignores_directional_votes(self):
        decision = evaluate_neutral_agent_vote_decision(
            "BTC/USDT",
            False,
            prob_final=55.0,
            votes={"MT": 60.0, "SR": 50.0, "G": 55.0},
        )

        self.assertIsNone(decision)

    def test_runtime_entry_decision_quarantines_real_symbol(self):
        execution = SimpleNamespace(
            is_symbol_quarantined=lambda _symbol: True,
            get_symbol_quarantine_remaining_seconds=lambda _symbol: 120,
        )
        bot = self._bot()
        bot.execution = execution

        decision = evaluate_runtime_entry_decision(bot, "BTC/USDT", False)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "SYMBOL_QUARANTINED")


if __name__ == "__main__":
    unittest.main()
