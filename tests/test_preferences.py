"""Conversation settings reach generation without changing literal translations."""
import os
os.environ['DELEGATE_DATABASE'] = ':memory:'
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as server
import engine
from models import ExtractedRules, UserDecisionAction


class PreferenceTests(unittest.TestCase):
    def test_preferences_saved_for_resume_and_not_disclosed_to_participant(self):
        preferences={'tone':'friendly','reply_length':'detailed','additional_instructions':'Only suggest afternoons. PRIVATE NOTE'}
        client=TestClient(server.app)
        with patch.dict(os.environ,{'AI_PROVIDER':'aimlapi','AIML_API_KEY':'test-key'}), patch.object(server,'extract_rules_from_prompt',return_value=ExtractedRules(intent='appointment',target_business='Office')) as extract:
            response=client.post('/api/session/create',json={'raw_prompt':'Ask about availability','preferences':preferences})
        self.assertEqual(response.status_code,200)
        sid=response.json()['session_id'];token=response.json()['participant_path'].split('/')[-1]
        self.assertIn('friendly; detailed',extract.call_args.args[0])
        self.assertIn('Only suggest afternoons.',extract.call_args.args[0])
        self.assertEqual(client.get(f'/api/session/{sid}/status').json()['preferences'],preferences)
        self.assertNotIn('PRIVATE NOTE',str(client.get(f'/api/participant/{token}').json()))

    def test_invalid_preferences_fail_before_model_spend(self):
        client=TestClient(server.app)
        with patch.object(server,'extract_rules_from_prompt') as extract:
            for preferences in [{'tone':'anything'},{'reply_length':'unbounded'},{'additional_instructions':'x'*1201}]:
                self.assertEqual(client.post('/api/session/create',json={'raw_prompt':'Hello','preferences':preferences}).status_code,422)
            extract.assert_not_called()

    def test_style_applies_to_generated_replies_but_not_literal_user_wording(self):
        session={'rules':{},'language':'French','preferences':{'tone':'friendly','reply_length':'detailed','additional_instructions':'STYLE_PRIVATE_INSTRUCTION'}}
        payload={'english_response':'Hello','translated_response':'Bonjour','action_summary':'Greeting'}
        completion=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(payload)))])
        with patch.object(engine.client.chat.completions,'create',return_value=completion) as model:
            engine.generate_staged_draft(session,UserDecisionAction(chosen_action='Greet them'))
            generated=model.call_args.kwargs['messages'][0]['content']
            self.assertIn('friendly; detailed',generated)
            self.assertIn('STYLE_PRIVATE_INSTRUCTION',generated)
            self.assertIn('never override approval',generated)
            engine.generate_staged_draft(session,UserDecisionAction(chosen_action='Custom response',response_mode='verbatim',custom_instruction='Bonjour'))
            literal=model.call_args.kwargs['messages'][0]['content']
            self.assertNotIn('STYLE_PRIVATE_INSTRUCTION',literal)
            self.assertNotIn('friendly; detailed',literal)
            self.assertIn('faithful translator',literal)
