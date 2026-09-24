"""Two independent clients exercise the live room without spending AI credits."""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import os
os.environ["DELEGATE_DATABASE"] = ":memory:"
import app as server
from models import ExtractedRules, RuleConstraint, TurnDecision, StagedResponse


class HumanRoomTests(unittest.TestCase):
    def setUp(self):
        server.sessiondb.clear()
        server.participant_sessions.clear()
        self.presenter = TestClient(server.app)
        self.receptionist = TestClient(server.app)
        self.env = patch.dict('os.environ', {'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key'})
        self.env.start()
        self.addCleanup(self.env.stop)
        rules = ExtractedRules(intent='hotel', target_business='Grandview Harbour Hotel', constraints=[RuleConstraint(parameter='price', operator='max', value='20', unit='USD')], special_notes='PRIVATE INSTRUCTION')
        with patch.object(server, 'extract_rules_from_prompt', return_value=rules):
            data = self.presenter.post('/api/session/create', json={'raw_prompt':'Private goal: early check-in, max $20', 'mode':'assist', 'user_language':'Spanish'}).json()
        self.sid = data['session_id']
        self.token = data['participant_path'].split('/')[-1]

    def stage(self, chosen='Introduce the call', text='Hello, is early check-in available?'):
        draft = StagedResponse(english_response=text, translated_response='PRIVATE TRANSLATION', action_summary='User selected a response.')
        with patch.object(server, 'generate_staged_draft', return_value=draft):
            result = self.presenter.post(f'/api/session/{self.sid}/user-action', json={'chosen_action':chosen})
        self.assertEqual(result.status_code, 200)

    def human_turn(self, text, complete=False):
        # Deliberately permissive model: deterministic guards must still catch risk.
        decision = TurnDecision(is_dealbreaker=False, call_completed=complete, draft_response='Let me check.', translated_response='Déjeme comprobar.')
        with patch.object(server, 'evaluate_caller_turn', return_value=decision):
            return self.receptionist.post(f'/api/participant/{self.token}/turn', json={'caller_text':text})

    def test_participant_cannot_see_private_rules_or_unapproved_words(self):
        self.stage(text='SECRET UNAPPROVED REPLY')
        data = self.receptionist.get(f'/api/participant/{self.token}').json()
        self.assertEqual(data['status'], 'waiting')
        self.assertEqual(data['transcript'], [])
        for secret in ['PRIVATE', 'SECRET', '$20', self.sid, 'rules', 'pending_draft', 'decision_ledger']:
            self.assertNotIn(secret, str(data))
        self.presenter.post(f'/api/session/{self.sid}/approve')
        data = self.receptionist.get(f'/api/participant/{self.token}').json()
        self.assertEqual(data['status'], 'ready')
        self.assertEqual(data['transcript'][0]['text'], 'SECRET UNAPPROVED REPLY')
        self.assertNotIn('translation', data['transcript'][0])

    def test_two_people_can_negotiate_and_approve_booking_separately(self):
        self.stage()
        self.presenter.post(f'/api/session/{self.sid}/approve')
        offer = self.human_turn('Early check-in costs thirty dollars.')
        self.assertEqual(offer.status_code, 200)
        self.assertEqual(offer.json()['status'], 'waiting')
        control = self.presenter.get(f'/api/session/{self.sid}/status').json()
        self.assertEqual(control['last_decision']['violation_parameter'], 'price')
        self.assertEqual(control['status'], 'DECISION_REQUIRED')
        self.assertNotIn('last_decision', offer.json())
        self.assertEqual(self.human_turn('Hello again').status_code, 409)
        self.stage('Negotiate down to $20', 'Could you do twenty dollars?')
        self.presenter.post(f'/api/session/{self.sid}/approve')
        self.human_turn('Twenty dollars is fine. Shall I hold the booking?')
        control = self.presenter.get(f'/api/session/{self.sid}/status').json()
        self.assertEqual(control['last_decision']['violation_parameter'], 'commitment')
        self.assertIn('Approve this booking', control['last_decision']['suggested_user_options'])
        self.stage('Approve this booking', 'Please confirm the booking for twenty dollars.')
        self.presenter.post(f'/api/session/{self.sid}/approve')
        self.assertTrue(server.sessiondb[self.sid]['commitment_authorized'])
        self.human_turn('Your booking is confirmed. Goodbye.', complete=True)
        self.assertEqual(self.receptionist.get(f'/api/participant/{self.token}').json()['status'], 'waiting')
        self.presenter.post(f'/api/session/{self.sid}/approve')
        self.assertEqual(self.receptionist.get(f'/api/participant/{self.token}').json()['status'], 'ended')
        self.assertEqual(self.human_turn('Another reply').status_code, 409)

    def test_room_link_does_not_grant_controller_api_access(self):
        self.assertNotEqual(self.sid, self.token)
        self.assertEqual(self.receptionist.post(f'/api/session/{self.token}/approve').status_code, 404)
        self.assertEqual(self.receptionist.get('/api/participant/not-a-room').status_code, 404)

    def test_interruption_is_idempotent(self):
        self.stage()
        for _ in range(3):
            self.assertEqual(self.presenter.post(f'/api/session/{self.sid}/interrupt').status_code, 200)
        entries = [row for row in server.sessiondb[self.sid]['decision_ledger'] if row['action_chosen']=='Interrupted']
        self.assertEqual(len(entries), 1)
        self.assertEqual(self.presenter.post(f'/api/session/{self.sid}/approve').status_code, 409)

    def test_empty_verbatim_reply_is_rejected_before_calling_model(self):
        with patch.object(server, 'generate_staged_draft') as generate:
            result = self.presenter.post(f'/api/session/{self.sid}/user-action', json={'chosen_action':'Custom response','response_mode':'verbatim','custom_instruction':'  '})
            self.assertEqual(result.status_code, 400)
            generate.assert_not_called()

    def test_receptionist_and_guide_assets_are_served(self):
        for path in [f'/receptionist/{self.token}', '/assets/participant.js', '/assets/speech.js', '/assets/PRESENTER_GUIDE.md']:
            self.assertEqual(self.presenter.get(path).status_code, 200, path)

if __name__ == '__main__':
    unittest.main()
