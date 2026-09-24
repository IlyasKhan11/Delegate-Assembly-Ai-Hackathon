"""Regression tests for approval gates shared by REST and WebSocket transports.

All external AI calls are mocked; no keys or paid requests are needed.
"""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import os
os.environ["DELEGATE_DATABASE"] = ":memory:"
import app as server
from models import ExtractedRules, RuleConstraint, TurnDecision, StagedResponse, FinalSummary

RULES = ExtractedRules(intent="early_check_in", target_business="Grandview Harbour Hotel", opening_phrase="Is early check-in available?", constraints=[RuleConstraint(parameter="price", operator="max", value="20", unit="USD")])


class CallFlowTests(unittest.TestCase):
    def setUp(self):
        server.sessiondb.clear()
        self.client = TestClient(server.app)
        self.key = patch.dict('os.environ', {'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key', 'ASSEMBLYAI_API_KEY': 'test-audio-key'})
        self.key.start()
        self.addCleanup(self.key.stop)

    def create(self, mode="assist"):
        with patch.object(server, 'extract_rules_from_prompt', return_value=RULES):
            response = self.client.post('/api/session/create', json={'raw_prompt': 'Early check-in, max $20', 'user_language': 'Spanish', 'mode': mode})
        self.assertEqual(response.status_code, 200)
        return response.json()['session_id']

    def evaluate(self, session, text, **changes):
        decision = TurnDecision(is_dealbreaker=False, draft_response="Let me ask about availability.", translated_response="Déjeme preguntar por disponibilidad.", **changes)
        with patch.object(server, 'evaluate_caller_turn', return_value=decision):
            return self.client.post(f'/api/session/{session}/evaluate-turn', json={'caller_text': text})

    def stage(self, session, action="Negotiate down to $20"):
        draft = StagedResponse(english_response="Would twenty dollars work?", translated_response="¿Le funcionarían veinte dólares?", action_summary="Counter-offer of $20")
        with patch.object(server, 'generate_staged_draft', return_value=draft):
            return self.client.post(f'/api/session/{session}/user-action', json={'chosen_action': action})

    def test_frontend_and_assets_are_served(self):
        for path in ['/', '/demo', '/assets/app.js', '/assets/styles.css', '/assets/demo.js', '/assets/favicon.svg']:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_the_browser_is_never_left_holding_a_stale_frontend(self):
        """A cached app.js looks exactly like an update that did not happen."""
        for path in ['/', '/demo', '/assets/app.js', '/assets/participant.js', '/assets/voice_playback.js']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.headers.get('cache-control'), 'no-cache', path)

    def test_an_unchanged_asset_still_answers_cheaply(self):
        """no-cache means revalidate, not re-download."""
        first = self.client.get('/assets/app.js')
        again = self.client.get('/assets/app.js', headers={'If-None-Match': first.headers['etag']})
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b'')

    def test_missing_credentials_does_not_break_frontend(self):
        with patch.dict('os.environ', {'AIML_API_KEY': ''}):
            self.assertFalse(self.client.get('/api/health').json()['ai_configured'])
            self.assertEqual(self.client.get('/').status_code, 200)
            self.assertEqual(self.client.post('/api/session/create', json={'raw_prompt': 'Hi'}).status_code, 503)

    def test_empty_prompt_and_invalid_mode(self):
        self.assertEqual(self.client.post('/api/session/create', json={'raw_prompt':'  '}).status_code, 400)
        self.assertEqual(self.client.post('/api/session/create', json={'raw_prompt':'Hi', 'mode':'invalid'}).status_code, 422)

    def test_assist_stages_every_routine_reply(self):
        sid = self.create()
        result = self.evaluate(sid, 'How can I help?').json()
        self.assertEqual(result['status'], 'AWAITING_APPROVAL')
        session = server.sessiondb[sid]
        self.assertEqual(len(session['transcript_history']), 1)
        self.assertTrue(session['pending_draft'])
        result = self.client.post(f'/api/session/{sid}/approve').json()
        self.assertEqual(result['session_status'], 'IN_PROGRESS')
        self.assertEqual(len(session['transcript_history']), 2)
        self.assertEqual(self.client.post(f'/api/session/{sid}/approve').status_code, 409)

    def test_delegate_routine_reply_is_spoken(self):
        sid = self.create('delegate')
        self.assertEqual(self.evaluate(sid, 'Good afternoon').json()['status'], 'IN_PROGRESS')
        self.assertEqual(len(server.sessiondb[sid]['transcript_history']), 2)

    def test_price_rule_overrides_incorrect_model_completion(self):
        for quote in ['$30', 'thirty dollars', '30 dollars']:
            sid = self.create('delegate')
            result = self.evaluate(sid, f'You are all set. It costs {quote}.', call_completed=True).json()
            self.assertEqual(result['status'], 'DECISION_REQUIRED')
            self.assertFalse(result['decision']['call_completed'])
            self.assertEqual(result['decision']['violation_parameter'], 'price')

    def test_booking_requires_approval_in_both_modes(self):
        for mode in ['assist', 'delegate']:
            sid = self.create(mode)
            result = self.evaluate(sid, 'Shall I book the check-in?', call_completed=True).json()
            self.assertEqual(result['status'], 'DECISION_REQUIRED')
            self.assertEqual(result['decision']['violation_parameter'], 'commitment')

    def test_sensitive_information_is_gated(self):
        sid = self.create('delegate')
        result = self.evaluate(sid, 'What is your credit card number?').json()
        self.assertEqual(result['status'], 'DECISION_REQUIRED')
        self.assertEqual(result['decision']['violation_parameter'], 'personal_data')

    def test_unapproved_draft_does_not_enter_ledger(self):
        sid = self.create()
        self.stage(sid, 'Approve $30 — this time only')
        self.assertEqual(server.sessiondb[sid]['decision_ledger'], [])
        self.assertNotIn('approved_prices', server.sessiondb[sid])
        self.client.post(f'/api/session/{sid}/reject')
        self.assertIsNone(server.sessiondb[sid]['pending_draft'])
        self.assertEqual(self.client.post(f'/api/session/{sid}/approve').status_code, 409)

    def test_approved_exception_preserves_original_ceiling(self):
        sid = self.create('delegate')
        self.stage(sid, 'Approve $30 — this time only')
        self.client.post(f'/api/session/{sid}/approve')
        session = server.sessiondb[sid]
        self.assertEqual(server.price_ceiling(session), 20)
        self.assertEqual(session['approved_prices'], [30])
        self.assertTrue(session['decision_ledger'][0]['approved'])
        self.assertEqual(self.evaluate(sid, 'It costs $30.').json()['status'], 'IN_PROGRESS')
        self.assertEqual(self.evaluate(sid, 'Now it costs $40.').json()['status'], 'DECISION_REQUIRED')

    def test_pending_turn_and_completed_session_are_protected(self):
        sid = self.create()
        self.evaluate(sid, 'Good afternoon')
        self.assertEqual(self.evaluate(sid, 'Hello again').status_code, 409)
        server.sessiondb[sid]['status'] = 'COMPLETED'
        self.assertEqual(self.stage(sid).status_code, 409)
        self.assertEqual(self.client.post(f'/api/session/{sid}/approve').status_code, 409)

    def test_interrupt_clears_pending_draft(self):
        sid = self.create()
        self.stage(sid)
        result = self.client.post(f'/api/session/{sid}/interrupt')
        self.assertEqual(result.json()['status'], 'DECISION_REQUIRED')
        self.assertIsNone(server.sessiondb[sid]['pending_draft'])
        self.assertEqual(self.client.post(f'/api/session/{sid}/approve').status_code, 409)

    def test_assist_completion_waits_until_approval(self):
        sid = self.create()
        result = self.evaluate(sid, 'Goodbye!', call_completed=True)
        self.assertEqual(result.json()['status'], 'AWAITING_APPROVAL')
        result = self.client.post(f'/api/session/{sid}/approve')
        self.assertEqual(result.json()['session_status'], 'COMPLETED')

    def test_model_failure_does_not_duplicate_caller_transcript(self):
        sid = self.create()
        with patch.object(server, 'evaluate_caller_turn', side_effect=RuntimeError('provider failure')):
            response = self.client.post(f'/api/session/{sid}/evaluate-turn', json={'caller_text': 'Hello'})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(server.sessiondb[sid]['transcript_history'], [])

    def test_summary_failure_can_be_retried(self):
        sid = self.create()
        with patch.object(server, 'generate_call_summary', side_effect=RuntimeError('provider failure')):
            response = self.client.post(f'/api/session/{sid}/complete')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(server.sessiondb[sid]['status'], 'COMPLETED')
        summary = FinalSummary(outcome_headline='Nothing confirmed.', outcome_subtext='Call ended.')
        with patch.object(server, 'generate_call_summary', return_value=summary) as generate:
            self.assertEqual(self.client.post(f'/api/session/{sid}/complete').status_code, 200)
            self.assertEqual(self.client.post(f'/api/session/{sid}/complete').status_code, 200)
            self.assertEqual(generate.call_count, 1)

    def test_websocket_uses_the_same_assist_gate(self):
        sid = self.create()
        decision = TurnDecision(is_dealbreaker=False, draft_response='Hello!')
        with patch.object(server, 'evaluate_caller_turn', return_value=decision):
            with self.client.websocket_connect(f'/ws/call/{sid}') as ws:
                ws.send_json({'event':'TRANSCRIPTION_CHUNK', 'text':'Good morning'})
                self.assertEqual(ws.receive_json()['event'], 'TRANSCRIPT_STREAM')
                self.assertEqual(ws.receive_json()['event'], 'STAGING_UPDATE')
                self.assertEqual(ws.receive_json()['status'], 'AWAITING_APPROVAL')
                ws.send_json({'event':'APPROVE_AND_SPEAK'})
                self.assertEqual(ws.receive_json()['text'], 'Hello!')
                self.assertEqual(ws.receive_json()['status'], 'IN_PROGRESS')
                ws.send_json({'event':'APPROVE_AND_SPEAK'})
                self.assertEqual(ws.receive_json()['status_code'], 409)

    def test_audio_uses_the_same_price_gate(self):
        sid = self.create('delegate')
        with patch.object(server, 'transcribe_audio', return_value='It costs thirty dollars.'), patch.object(server, 'evaluate_caller_turn', return_value=TurnDecision(is_dealbreaker=False)):
            response = self.client.post(f'/api/session/{sid}/audio-turn', files={'file':('test.webm',b'test','audio/webm')})
        self.assertEqual(response.json()['status'], 'DECISION_REQUIRED')
        self.assertEqual(response.json()['transcribed_text'], 'It costs thirty dollars.')

if __name__ == '__main__':
    unittest.main()
