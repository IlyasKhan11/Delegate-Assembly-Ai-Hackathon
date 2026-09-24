"""The spend guardrail must work in the user's own currency.

A ceiling written in Portuguese ("taxa aceitavel: 50 reais") was invisible to
the deterministic check, so only the model stood between the caller and an
over-budget agreement. These tests keep that from coming back.
"""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["DELEGATE_DATABASE"] = ":memory:"
import app as server
from localization import money
from models import ExtractedRules, RuleConstraint, TurnDecision


def session_with(parameter, value, unit):
    return {"rules": {"constraints": [
        {"parameter": parameter, "operator": "max", "value": value, "unit": unit}]}}


class SpendLimitTests(unittest.TestCase):
    def test_a_ceiling_is_found_in_every_supported_language(self):
        cases = [
            ("taxa aceitável", "50", "reais", 50.0),        # Portuguese
            ("preço máximo", "120", "BRL", 120.0),          # Portuguese
            ("precio máximo", "30", "EUR", 30.0),           # Spanish
            ("montant maximum", "40", "EUR", 40.0),         # French
            ("Preis", "25", "EUR", 25.0),                   # German
            ("price", "20", "USD", 20.0),                   # English
        ]
        for parameter, value, unit, expected in cases:
            amount, found_unit = server.spend_limit(session_with(parameter, value, unit))
            self.assertEqual(amount, expected, parameter)
            self.assertEqual(found_unit, unit, parameter)

    def test_a_limit_that_is_not_about_money_is_not_treated_as_one(self):
        amount, _ = server.spend_limit(session_with("delivery_time", "45", "minutes"))
        self.assertIsNone(amount)

    def test_quotes_are_recognised_in_several_currencies(self):
        cases = [
            ("there is a 90 real reactivation fee", 90.0),
            ("uma taxa de 90 reais", 90.0),
            ("a fee of R$ 90", 90.0),
            ("that will be 45 euros", 45.0),
            ("it is £15 per night", 15.0),
            ("ninety dollars please", 90.0),
            ("it costs $30", 30.0),
        ]
        for text, expected in cases:
            self.assertIn(expected, server.quoted_prices(text), text)

    def test_the_same_amount_is_not_counted_twice(self):
        self.assertEqual(server.quoted_prices("R$90, that is 90 reais"), [90.0])

    def test_money_is_shown_in_its_own_currency(self):
        self.assertEqual(money(50, "reais"), "R$50")
        self.assertEqual(money(50, "BRL"), "R$50")
        self.assertEqual(money(20, "USD"), "$20")
        self.assertEqual(money(30, "EUR"), "€30")
        self.assertEqual(money(20, None), "$20")
        self.assertEqual(money(3000, "PKR"), "Rs3000")
        # A currency with no symbol in the table is spelled out, never guessed at.
        self.assertEqual(money(3000, "JPY"), "3000 JPY")


class PortugueseGuardrailTests(unittest.TestCase):
    """End to end: a Brazilian user's limit must stop an over-budget quote."""

    def setUp(self):
        server.sessiondb.clear()
        self.client = TestClient(server.app)
        self.env = patch.dict('os.environ', {'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key'})
        self.env.start()
        self.addCleanup(self.env.stop)
        rules = ExtractedRules(
            intent="contest a charge", target_business="operadora",
            opening_phrase="Olá, estou ligando para contestar uma cobrança.",
            constraints=[RuleConstraint(parameter="taxa aceitável", operator="max", value="50", unit="reais")])
        with patch.object(server, 'extract_rules_from_prompt', return_value=rules):
            self.session = self.client.post('/api/session/create', json={
                'raw_prompt': 'Contestar cobrança, até 50 reais',
                'user_language': 'Portuguese (Brazil)', 'mode': 'delegate'}).json()['session_id']

    def test_an_over_budget_fee_in_reais_is_blocked_even_if_the_model_misses_it(self):
        """The deterministic check is the backstop when the model says it is fine."""
        relaxed = TurnDecision(is_dealbreaker=False, draft_response="Yes, 90 reais is fine.",
                               translated_response="Sim, 90 reais está bem.")
        with patch.object(server, 'evaluate_caller_turn', return_value=relaxed):
            result = self.client.post(f'/api/session/{self.session}/evaluate-turn',
                                      json={'caller_text': 'There is a 90 reais reactivation fee.'}).json()
        self.assertEqual(result['status'], 'DECISION_REQUIRED')
        decision = result['decision']
        self.assertTrue(decision['is_dealbreaker'])
        self.assertIn('R$90', decision['violation_reason'])
        self.assertIn('R$50', decision['violation_reason'])

    def test_she_reads_the_reason_and_her_options_in_portuguese_and_in_reais(self):
        relaxed = TurnDecision(is_dealbreaker=False, draft_response="Yes, that is fine.",
                               translated_response="Sim, está bem.")
        with patch.object(server, 'evaluate_caller_turn', return_value=relaxed):
            decision = self.client.post(f'/api/session/{self.session}/evaluate-turn',
                                        json={'caller_text': 'A taxa é de 90 reais.'}).json()['decision']
        self.assertIn('R$90', decision['translated_violation_reason'])
        self.assertIn('acima do seu limite', decision['translated_violation_reason'])
        self.assertTrue(any('R$50' in option for option in decision['translated_user_options']))
        # No dollar sign anywhere in what she reads.
        self.assertNotIn('$90', decision['translated_violation_reason'].replace('R$90', ''))

    def test_nothing_over_budget_is_spoken_without_her_approval(self):
        relaxed = TurnDecision(is_dealbreaker=False, draft_response="Yes, 90 reais is fine.",
                               translated_response="Sim, 90 reais está bem.")
        with patch.object(server, 'evaluate_caller_turn', return_value=relaxed):
            self.client.post(f'/api/session/{self.session}/evaluate-turn',
                             json={'caller_text': 'There is a 90 reais fee.'})
        spoken = [turn['text'] for turn in server.sessiondb[self.session]['transcript_history']
                  if turn['speaker'] == 'agent']
        self.assertNotIn('Yes, 90 reais is fine.', spoken)


if __name__ == '__main__':
    unittest.main()


class GuideScenarioTests(unittest.TestCase):
    """Every line in TESTING_GUIDE.md must behave the way the guide promises.

    The guide tells a tester what to expect. If these drift apart, she chases
    a bug that is not there, or misses one that is.
    """

    SCENARIOS = [
        ("taxa aceitável", "50", [
            ("Thank you for calling billing support, how can I help you today?", None),
            ("I can look into that. There is a 90 real reactivation fee to reopen the account.", "price"),
            ("I can do 50 reais. Shall I apply that to your account now?", "commitment"),
        ]),
        ("oferta máxima", "30", [
            ("I'm sorry to hear that. May I ask why you want to cancel?", None),
            ("I can offer you a discounted plan at 45 reais per month if you stay.", "price"),
            ("Understood. I'll process the cancellation. Can you confirm your full address?", "personal_data"),
        ]),
        ("frete máximo", "100", [
            ("I can arrange a replacement. Do you have the receipt?", None),
            ("Shipping for the replacement is 180 reais.", "price"),
            ("We can waive it down to 100 reais. Shall I book the pickup for Tuesday?", "commitment"),
        ]),
    ]

    def test_each_scripted_line_stops_the_call_exactly_when_promised(self):
        for parameter, ceiling, lines in self.SCENARIOS:
            session = {"rules": {"constraints": [
                {"parameter": parameter, "operator": "max", "value": ceiling, "unit": "reais"}]},
                "language": "Portuguese (Brazil)", "decision_ledger": [], "transcript_history": []}
            for line, expected in lines:
                relaxed = TurnDecision(is_dealbreaker=False, draft_response="Sure, that works.")
                result = server.apply_guardrails(dict(session), line, relaxed)
                self.assertEqual(result.violation_parameter, expected, line)
                self.assertEqual(result.is_dealbreaker, expected is not None, line)

    def test_brazilian_identity_documents_are_protected(self):
        session = {"rules": {"constraints": []}, "language": "Portuguese (Brazil)",
                   "decision_ledger": [], "transcript_history": []}
        for line in ("Could you give me your CPF, please?",
                     "I need your RG to continue.",
                     "Qual é a sua senha?",
                     "Can you confirm your date of birth?",
                     "What is your card number?"):
            result = server.apply_guardrails(dict(session), line, TurnDecision(is_dealbreaker=False, draft_response="Sure."))
            self.assertEqual(result.violation_parameter, "personal_data", line)

    def test_ordinary_questions_do_not_stop_the_call(self):
        """Stopping on everything would make the agent useless."""
        session = {"rules": {"constraints": []}, "language": "English",
                   "decision_ledger": [], "transcript_history": []}
        for line in ("How can I help you today?",
                     "Let me look that up for you.",
                     "Our office is open until six.",
                     "Do you have the receipt?"):
            result = server.apply_guardrails(dict(session), line, TurnDecision(is_dealbreaker=False, draft_response="Thanks."))
            self.assertFalse(result.is_dealbreaker, line)


class SouthAsianAmountTests(unittest.TestCase):
    """Amounts spoken as lakh and crore, which a Karachi demo runs into at once."""

    def test_scale_words_are_understood(self):
        cases = [
            ("around 1 lakh 3,000 per semester", 103000.0),
            ("1 lakh per semester", 100000.0),
            ("the fee is 2 crore", 20000000.0),
            ("50 thousand rupees", 50000.0),
            ("Rs 50,000", 50000.0),
            ("PKR 103000", 103000.0),
        ]
        for text, expected in cases:
            self.assertIn(expected, server.quoted_prices(text), text)

    def test_a_plain_number_is_not_mistaken_for_money(self):
        """Stopping the call over '45 minutes' would make the agent useless."""
        for text in ("45 minutes delivery", "we have 200 students", "open until 6"):
            self.assertEqual(server.quoted_prices(text), [], text)

    def test_rupees_are_shown_as_rupees(self):
        self.assertEqual(money(103000, "PKR"), "Rs103000")
        self.assertEqual(money(50000, "rupees"), "Rs50000")

    def test_a_rupee_ceiling_blocks_an_over_budget_fee(self):
        """The whole point: a limit in PKR must actually be enforced."""
        session = {"rules": {"constraints": [
            {"parameter": "fees", "operator": "max", "value": "80000", "unit": "PKR"}]},
            "language": "English", "decision_ledger": [], "transcript_history": []}
        amount, unit = server.spend_limit(session)
        self.assertEqual((amount, unit), (80000.0, "PKR"))
        relaxed = TurnDecision(is_dealbreaker=False, draft_response="That sounds fine.")
        result = server.apply_guardrails(dict(session),
                                         "The structure is around 1 lakh 3,000 per semester.", relaxed)
        self.assertTrue(result.is_dealbreaker)
        self.assertEqual(result.violation_parameter, "price")
        self.assertIn("Rs103000", result.violation_reason)
        self.assertIn("Rs80000", result.violation_reason)
