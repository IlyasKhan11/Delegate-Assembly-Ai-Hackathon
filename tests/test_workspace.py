"""General business setup and explicit preferences use the same approval engine."""
import os
os.environ['DELEGATE_DATABASE'] = ':memory:'
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as server
from models import ExtractedRules, RuleConstraint, TurnDecision


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.env = patch.dict(os.environ, {'AI_PROVIDER':'aimlapi','AIML_API_KEY':'test-key'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def create(self, **settings):
        rules = ExtractedRules(intent='resolve_delivery_issue', target_business='Model guessed business', constraints=[RuleConstraint(parameter='cost',operator='max',value='50'),RuleConstraint(parameter='date',operator='exact',value='Friday')])
        with patch.object(server,'extract_rules_from_prompt',return_value=rules) as extract:
            result = self.client.post('/api/session/create',json={'raw_prompt':'Ask where my parcel is. Do not order a replacement without asking me.','mode':'assist',**settings})
        self.assertEqual(result.status_code,200)
        return result.json(), extract.call_args.args[0]

    def test_explicit_business_and_zero_budget_override_model_guesses(self):
        data,prompt = self.create(business_name='Acme delivery support',spending_limit=0)
        sid = data['session_id']
        self.assertEqual(data['extracted_rules']['target_business'],'Acme delivery support')
        self.assertEqual(server.price_ceiling(server.sessiondb[sid]),0)
        self.assertIn('Acme delivery support',prompt)
        self.assertIn('Explicit maximum spending limit: 0 USD',prompt)
        self.assertEqual(data['extracted_rules']['constraints'][0]['parameter'],'date')
        with patch.object(server,'evaluate_caller_turn',return_value=TurnDecision(is_dealbreaker=False,draft_response='Sure.')):
            result=self.client.post(f'/api/session/{sid}/evaluate-turn',json={'caller_text':'A replacement delivery costs $5.'})
        self.assertEqual(result.json()['status'],'DECISION_REQUIRED')
        self.assertIn('$0 limit',result.json()['decision']['violation_reason'])

    def test_missing_form_budget_preserves_limit_extracted_from_task(self):
        data,_ = self.create(business_name='Customer services')
        self.assertEqual(server.price_ceiling(server.sessiondb[data['session_id']]),50)

    def test_invalid_budget_is_rejected_before_paid_extraction(self):
        with patch.object(server,'extract_rules_from_prompt') as extract:
            for limit in [-1,1000001,'not a number']:
                response=self.client.post('/api/session/create',json={'raw_prompt':'Ask about my delivery','spending_limit':limit})
                self.assertEqual(response.status_code,422)
            extract.assert_not_called()

    def test_participant_presence_changes_without_exposing_private_preferences(self):
        data,_ = self.create(spending_limit=35)
        sid=data['session_id'];token=data['participant_path'].split('/')[-1]
        self.assertFalse(self.client.get(f'/api/session/{sid}/status').json()['participant_connected'])
        participant=self.client.get(f'/api/participant/{token}').json()
        self.assertTrue(self.client.get(f'/api/session/{sid}/status').json()['participant_connected'])
        self.assertNotIn('rules',participant)
        self.assertNotIn('spending_limit',participant)
        server.sessiondb[sid]['participant_last_seen']=time.time()-20
        self.assertFalse(self.client.get(f'/api/session/{sid}/status').json()['participant_connected'])

    def test_new_workspace_route_is_available(self):
        self.assertEqual(self.client.get('/app').status_code,200)
