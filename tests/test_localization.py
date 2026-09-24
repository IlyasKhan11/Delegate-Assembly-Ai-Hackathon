"""Localized user choices retain English policy actions without extra AI calls."""
import os
os.environ['DELEGATE_DATABASE'] = ':memory:'
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import app as server
import engine
from localization import MESSAGES
from models import TurnDecision, IncomingUserRequest, UserDecisionAction
from pydantic import ValidationError


class LocalizationTests(unittest.TestCase):
    def test_shared_catalog_has_every_key_in_all_five_languages(self):
        self.assertEqual(set(MESSAGES), {'Portuguese (Brazil)', 'Spanish', 'French', 'German', 'English'})
        for language, messages in MESSAGES.items():
            self.assertEqual(set(messages), set(MESSAGES['English']), language)
            self.assertTrue(all(messages.values()))

    def test_guardrail_labels_translate_without_changing_authorization_actions(self):
        for language in MESSAGES:
            session = {'language': language, 'rules': {'constraints':[{'parameter':'price','operator':'max','value':'20'}]}}
            decision = server.apply_guardrails(session, 'The fee is $30.', TurnDecision(is_dealbreaker=False, translated_user_options=['incorrect old option']))
            self.assertEqual(decision.suggested_user_options, ['Negotiate down to $20','Approve $30 — this time only','Decline the offer'])
            self.assertEqual(len(decision.translated_user_options),3)
            self.assertIn('$30',decision.translated_violation_reason)
            self.assertIn('$20',decision.translated_violation_reason)
            if language != 'English':
                self.assertNotEqual(decision.suggested_user_options[0],decision.translated_user_options[0])
            booking = server.apply_guardrails(session, 'Shall I confirm the booking?', TurnDecision(is_dealbreaker=False))
            self.assertEqual(booking.suggested_user_options[0], 'Approve this booking')
            self.assertEqual(booking.translated_user_options[0], MESSAGES[language]['confirm'])

    def test_model_generates_dynamic_translations_in_the_same_bounded_request(self):
        data={'is_dealbreaker':True,'violation_reason':'Friday is unavailable.','translated_violation_reason':'Vendredi est indisponible.','suggested_user_options':['Ask about Saturday'],'translated_user_options':['Demander pour samedi']}
        completion=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(data)))])
        with patch.object(engine.client.chat.completions,'create',return_value=completion) as model:
            decision=engine.evaluate_caller_turn('Friday is unavailable.',{'language':'French','rules':{}})
        self.assertEqual(model.call_count,1)
        prompt=model.call_args.kwargs['messages'][0]['content']
        self.assertIn('translated_user_options in French',prompt)
        self.assertIn('same order',prompt)
        self.assertEqual(decision.translated_user_options,['Demander pour samedi'])
        self.assertEqual(model.call_args.kwargs['max_tokens'],700)

    def test_only_requested_languages_are_accepted_with_legacy_portuguese_compatibility(self):
        for language in MESSAGES:
            self.assertEqual(IncomingUserRequest(raw_prompt='Hello', user_language=language).user_language,language)
        for alias in ['Portuguese', 'Brazilian Portuguese', 'pt-BR']:
            self.assertEqual(IncomingUserRequest(raw_prompt='Hello', user_language=alias).user_language,'Portuguese (Brazil)')
        for unsupported in ['Italian', 'Dutch', {'language':'French'}]:
            with self.assertRaises(ValidationError):
                IncomingUserRequest(raw_prompt='Hello', user_language=unsupported)

    def test_custom_reply_translation_explicitly_requests_brazilian_portuguese(self):
        data={'english_response':'Hello','translated_response':'Olá','action_summary':'Greeting'}
        completion=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(data)))])
        with patch.object(engine.client.chat.completions,'create',return_value=completion) as model:
            engine.generate_staged_draft({'language':'Portuguese (Brazil)'},UserDecisionAction(chosen_action='Custom response',custom_instruction='Olá',response_mode='verbatim'))
        self.assertIn('Brazilian Portuguese (pt-BR)',model.call_args.kwargs['messages'][0]['content'])
