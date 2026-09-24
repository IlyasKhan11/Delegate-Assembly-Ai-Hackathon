"""Provider and cost regressions; these tests never call a paid API."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ai_config
import engine
from models import UserDecisionAction


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {
            'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-aiml-key',
            'AIML_MODEL': 'openai/gpt-4o-mini', 'GROQ_API_KEY': '',
            'ASSEMBLYAI_API_KEY': '', 'LLM_MAX_OUTPUT_TOKENS': '700',
            'LLM_MAX_INPUT_CHARS': '24000',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_english_user_written_reply_is_preserved_without_a_paid_request(self):
        with patch.object(engine.client.chat.completions, 'create') as create:
            result = engine.generate_staged_draft({'language':'English'}, UserDecisionAction(chosen_action='Custom response', custom_instruction='hi', response_mode='verbatim'))
            self.assertEqual(result.english_response, 'hi')
            create.assert_not_called()

    def test_translator_is_not_given_negotiation_context_for_a_literal_reply(self):
        completion = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps({'english_response':'Hi', 'translated_response':'Salut', 'action_summary':'User-written greeting'})))])
        with patch.object(engine.client.chat.completions, 'create', return_value=completion) as create:
            engine.generate_staged_draft({'language':'French', 'rules':{'intent':'negotiate_hotel'}}, UserDecisionAction(chosen_action='Custom response', custom_instruction='hi', response_mode='verbatim'))
            messages = create.call_args.kwargs['messages']
            self.assertEqual(messages[1]['content'], 'hi')
            self.assertNotIn('negotiate_hotel', messages[0]['content'])
            self.assertIn('faithful translator', messages[0]['content'])

    def test_key_and_model_belong_to_selected_provider(self):
        self.assertEqual(ai_config.provider_url(), 'https://api.aimlapi.com/v1')
        self.assertEqual(ai_config.provider_key(), 'test-aiml-key')
        self.assertEqual(ai_config.get_model(), 'openai/gpt-4o-mini')
        with patch.dict('os.environ', {'AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-groq-key'}):
            self.assertEqual(ai_config.provider_key(), 'test-groq-key')
            self.assertEqual(ai_config.provider_url(), 'https://api.groq.com/openai/v1')

    def test_health_never_contains_credentials(self):
        settings = ai_config.public_ai_settings()
        self.assertTrue(settings['ai_configured'])
        self.assertFalse(settings['audio_configured'])
        self.assertNotIn('test-aiml-key', json.dumps(settings))

    def test_placeholders_do_not_count_as_configured(self):
        with patch.dict('os.environ', {'AIML_API_KEY': 'your_aiml_api_key_here'}):
            self.assertFalse(ai_config.public_ai_settings()['ai_configured'])

    def test_no_automatic_retries(self):
        self.assertEqual(engine.client.max_retries, 0)

    def test_every_ai_operation_has_an_output_cap(self):
        session = {'rules': {}, 'language': 'Spanish', 'transcript_history': [], 'decision_ledger': []}
        operations = [
            (lambda: engine.extract_rules_from_prompt('Check in at noon'), {'intent': 'check_in', 'target_business': 'Hotel'}),
            (lambda: engine.evaluate_caller_turn('Hello', session), {'is_dealbreaker': False}),
            (lambda: engine.generate_staged_draft(session, UserDecisionAction(chosen_action='Decline')), {'english_response': 'No thanks.', 'translated_response': 'No, gracias.', 'action_summary': 'Declined'}),
            (lambda: engine.generate_call_summary(session), {'outcome_headline': 'Nothing booked.', 'outcome_subtext': 'No agreement.'}),
        ]
        for operation, result in operations:
            completion = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(result)))])
            with patch.object(engine.client.chat.completions, 'create', return_value=completion) as create:
                operation()
                self.assertEqual(create.call_count, 1)
                self.assertEqual(create.call_args.kwargs['max_tokens'], 700)
                self.assertEqual(create.call_args.kwargs['model'], 'openai/gpt-4o-mini')

    def test_oversized_input_is_rejected_before_spending(self):
        with patch.object(engine.client.chat.completions, 'create') as create:
            with self.assertRaisesRegex(ValueError, 'input budget'):
                engine.budgeted_completion(messages=[{'role': 'user', 'content': 'a' * 24001}])
            create.assert_not_called()

    def test_truncation_does_not_trigger_a_paid_retry(self):
        completion = SimpleNamespace(choices=[SimpleNamespace(finish_reason='length')])
        with patch.object(engine.client.chat.completions, 'create', return_value=completion) as create:
            with self.assertRaisesRegex(ValueError, 'token cap'):
                engine.budgeted_completion(messages=[{'role': 'user', 'content': 'Hello'}])
            self.assertEqual(create.call_count, 1)

    def test_microphone_does_not_spend_aiml_text_credits(self):
        with patch.object(engine, 'aai_key', ''), patch.object(engine, 'groq_key', ''), patch.object(engine.client.audio.transcriptions, 'create') as create:
            with self.assertRaisesRegex(ValueError, 'Microphone input needs'):
                engine.transcribe_audio(b'audio', 'audio.webm')
            create.assert_not_called()

    def test_groq_audio_uses_its_own_client_and_credential(self):
        with patch.object(engine, 'aai_key', ''), patch.object(engine, 'groq_key', 'speech-only-key'), patch.object(engine, 'OpenAI') as factory:
            speech = factory.return_value.__enter__.return_value
            speech.audio.transcriptions.create.return_value.text = ' Hello '
            self.assertEqual(engine.transcribe_audio(b'audio'), 'Hello')
            self.assertEqual(factory.call_args.kwargs['api_key'], 'speech-only-key')
            self.assertEqual(factory.call_args.kwargs['base_url'], 'https://api.groq.com/openai/v1')
            self.assertEqual(factory.call_args.kwargs['max_retries'], 0)

if __name__ == '__main__':
    unittest.main()
