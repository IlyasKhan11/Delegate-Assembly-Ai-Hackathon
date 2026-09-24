"""Live speech-to-text: the other side just talks.

The key properties: the AssemblyAI key never reaches the browser, and a spoken
sentence goes through exactly the same guardrails and approval gate as a typed
one. Nothing here calls AssemblyAI.
"""
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["DELEGATE_DATABASE"] = ":memory:"
import app as server
import live_transcription
from models import ExtractedRules, RuleConstraint, TurnDecision

RULES = ExtractedRules(intent="dispute", target_business="operadora",
                       opening_phrase="Olá, estou ligando sobre uma cobrança.",
                       constraints=[RuleConstraint(parameter="taxa", operator="max", value="50", unit="reais")])


class ListeningConfigTests(unittest.TestCase):
    def test_it_needs_only_the_assemblyai_key_and_no_tunnel(self):
        """Unlike the Voice Agent API this works without a public address."""
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'k', 'PUBLIC_BASE_URL': ''}):
            self.assertEqual(live_transcription.unavailable_reason(), "")
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': ''}):
            self.assertIn("ASSEMBLYAI_API_KEY", live_transcription.unavailable_reason())

    def test_settings_never_carry_the_key(self):
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'secret-listening-key'}):
            settings = live_transcription.public_listening_settings()
        self.assertTrue(settings["live_listening_configured"])
        self.assertNotIn("secret-listening-key", json.dumps(settings))

    def test_the_socket_url_asks_for_the_right_audio_format(self):
        url = live_transcription.websocket_url()
        self.assertTrue(url.startswith("wss://streaming.assemblyai.com/v3/ws"))
        self.assertIn(f"sample_rate={live_transcription.SAMPLE_RATE}", url)
        self.assertIn("format_turns=true", url)
        self.assertNotIn("token=", url, "the token is added by the browser, never baked in")


class ListeningEndpointTests(unittest.TestCase):
    def setUp(self):
        server.sessiondb.clear()
        self.client = TestClient(server.app)
        self.env = patch.dict('os.environ', {
            'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key',
            'ASSEMBLYAI_API_KEY': 'secret-listening-key'})
        self.env.start()
        self.addCleanup(self.env.stop)
        with patch.object(server, 'extract_rules_from_prompt', return_value=RULES):
            created = self.client.post('/api/session/create', json={
                'raw_prompt': 'Contestar cobrança', 'user_language': 'Portuguese (Brazil)',
                'mode': 'assist'}).json()
        self.session = created['session_id']
        self.participant = created['participant_path'].rsplit('/', 1)[-1]

    def test_both_sides_get_a_short_lived_token_not_the_key(self):
        for route in (f'/api/session/{self.session}/listen',
                      f'/api/participant/{self.participant}/listen'):
            with patch.object(server.live_transcription, 'listening_token', return_value='temp-listen-123'):
                result = self.client.get(route).json()
            self.assertTrue(result['listening'], route)
            self.assertEqual(result['token'], 'temp-listen-123', route)
            self.assertNotIn('secret-listening-key', json.dumps(result), route)

    def test_an_unknown_receptionist_link_gets_nothing(self):
        self.assertEqual(self.client.get('/api/participant/not-a-real-token/listen').status_code, 404)

    def test_missing_configuration_is_explained_rather_than_failing_oddly(self):
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': ''}):
            response = self.client.get(f'/api/session/{self.session}/listen')
        self.assertEqual(response.status_code, 503)
        self.assertIn('ASSEMBLYAI_API_KEY', response.json()['detail'])

    def test_a_spoken_sentence_is_judged_exactly_like_a_typed_one(self):
        """Speech must not become a way around the spend guardrail."""
        relaxed = TurnDecision(is_dealbreaker=False, draft_response="Yes, 90 reais is fine.",
                               translated_response="Sim, 90 reais está bem.")
        with patch.object(server, 'evaluate_caller_turn', return_value=relaxed):
            spoken = self.client.post(f'/api/participant/{self.participant}/turn',
                                      json={'caller_text': 'There is a 90 reais fee.'})
        self.assertEqual(spoken.status_code, 200)
        session = server.sessiondb[self.session]
        self.assertEqual(session['status'], 'DECISION_REQUIRED')
        self.assertNotIn('Yes, 90 reais is fine.',
                         [turn['text'] for turn in session['transcript_history'] if turn['speaker'] == 'agent'])

    def test_health_advertises_live_listening(self):
        health = self.client.get('/api/health').json()
        self.assertTrue(health['live_listening_configured'])
        self.assertEqual(health['listen_sample_rate'], live_transcription.SAMPLE_RATE)
        self.assertNotIn('secret-listening-key', json.dumps(health))


if __name__ == '__main__':
    unittest.main()
