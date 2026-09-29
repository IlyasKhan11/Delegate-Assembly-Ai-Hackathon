"""Regressions for the free neural voice (edge-tts).

The voice must never become a way to put new words in the agent's mouth, and a
failure must leave the page free to fall back rather than going silent. No
network request is made here: synthesis itself is mocked.
"""
import asyncio
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["DELEGATE_DATABASE"] = ":memory:"
import app as server
import tts
from models import ExtractedRules, StagedResponse, TurnDecision

RULES = ExtractedRules(intent="early_check_in", target_business="Grandview Harbour Hotel",
                       opening_phrase="Hello, I am calling about early check-in.")
FAKE_MP3 = b"ID3fake-audio-bytes"


class SpeechEndpointTests(unittest.TestCase):
    def setUp(self):
        server.sessiondb.clear()
        self.client = TestClient(server.app)
        self.env = patch.dict('os.environ', {'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key'})
        self.env.start()
        self.addCleanup(self.env.stop)
        with patch.object(server, 'extract_rules_from_prompt', return_value=RULES):
            created = self.client.post('/api/session/create', json={
                'raw_prompt': 'Early check-in, max $20', 'user_language': 'English', 'mode': 'assist'}).json()
        self.session = created['session_id']
        self.participant = created['participant_path'].rsplit('/', 1)[-1]
        server.append_turn(server.sessiondb[self.session], 'agent', 'Hello, I am calling about early check-in.')
        server.append_turn(server.sessiondb[self.session], 'caller', 'It costs thirty dollars.')

    def speak(self, text, as_participant=False):
        route = (f'/api/participant/{self.participant}/speech' if as_participant
                 else f'/api/session/{self.session}/speech')
        with patch.object(server.tts, 'synthesize', return_value=FAKE_MP3):
            return self.client.post(route, json={'text': text})

    def test_a_line_from_the_conversation_is_read_aloud(self):
        response = self.speak('Hello, I am calling about early check-in.')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'audio/mpeg')
        self.assertEqual(response.content, FAKE_MP3)

    def test_words_never_said_are_refused(self):
        """The voice cannot be used to make the agent say something new."""
        response = self.speak('Please charge my credit card four thousand dollars.')
        self.assertEqual(response.status_code, 403)
        response = self.speak('Please charge my credit card four thousand dollars.', as_participant=True)
        self.assertEqual(response.status_code, 403)

    def test_the_controller_may_hear_a_draft_before_approving_it(self):
        draft = StagedResponse(english_response="Would twenty dollars work?",
                               translated_response="Would twenty dollars work?",
                               action_summary="Counter-offer of $20")
        with patch.object(server, 'generate_staged_draft', return_value=draft):
            self.client.post(f'/api/session/{self.session}/user-action',
                             json={'chosen_action': 'Negotiate down to $20'})
        self.assertEqual(self.speak('Would twenty dollars work?').status_code, 200)

    def test_the_receptionist_cannot_hear_a_draft_before_it_is_approved(self):
        """An unapproved reply must not reach the other side by any route."""
        draft = StagedResponse(english_response="Would twenty dollars work?",
                               translated_response="Would twenty dollars work?",
                               action_summary="Counter-offer of $20")
        with patch.object(server, 'generate_staged_draft', return_value=draft):
            staged = self.client.post(f'/api/session/{self.session}/user-action',
                                      json={'chosen_action': 'Negotiate down to $20'}).json()
        self.assertEqual(self.speak('Would twenty dollars work?', as_participant=True).status_code, 403)
        self.client.post(f'/api/session/{self.session}/approve', json={'draft_id': staged['draft_id']})
        self.assertEqual(self.speak('Would twenty dollars work?', as_participant=True).status_code, 200)

    def test_the_receptionist_is_not_read_their_own_words_back(self):
        self.assertEqual(self.speak('It costs thirty dollars.').status_code, 200)
        self.assertEqual(self.speak('It costs thirty dollars.', as_participant=True).status_code, 403)

    def test_a_synthesis_failure_reports_cleanly_so_the_page_can_fall_back(self):
        with patch.object(server.tts, 'synthesize', side_effect=RuntimeError('edge-tts is down')):
            response = self.client.post(f'/api/session/{self.session}/speech',
                                        json={'text': 'Hello, I am calling about early check-in.'})
        self.assertEqual(response.status_code, 502)

    def test_the_voice_is_advertised_without_needing_any_key(self):
        health = self.client.get('/api/health').json()
        self.assertTrue(health['tts_configured'])
        self.assertEqual(health['tts_voice'], tts.configured_voice())


class SynthesisTests(unittest.TestCase):
    def setUp(self):
        tts._cache.clear()
        tts._in_flight.clear()

    def test_the_same_line_is_only_generated_once(self):
        """Replays and the approve-after-preview path must not re-synthesize."""
        async def scenario():
            with patch.object(tts, '_render', return_value=FAKE_MP3) as render:
                first = await tts.synthesize('Would twenty dollars work?')
                second = await tts.synthesize('Would twenty dollars work?')
                return first, second, render.call_count
        first, second, calls = asyncio.run(scenario())
        self.assertEqual(first, second)
        self.assertEqual(calls, 1)

    def test_preparing_and_playing_at_once_share_one_generation(self):
        """The page prepares a draft, then plays it; that is one request, not two."""
        async def scenario():
            with patch.object(tts, '_render', return_value=FAKE_MP3) as render:
                await asyncio.gather(tts.synthesize('Hold on please.'), tts.synthesize('Hold on please.'))
                return render.call_count
        self.assertEqual(asyncio.run(scenario()), 1)

    def test_a_failure_does_not_poison_later_attempts(self):
        async def scenario():
            with patch.object(tts, '_render', side_effect=RuntimeError('network blip')):
                try:
                    await tts.synthesize('Hello there.')
                except RuntimeError:
                    pass
            with patch.object(tts, '_render', return_value=FAKE_MP3):
                return await tts.synthesize('Hello there.')
        self.assertEqual(asyncio.run(scenario()), FAKE_MP3)

    def test_a_stalled_speech_service_fails_with_a_clear_reason(self):
        """A host that silently blocks outbound traffic must not hang the request."""
        async def never_answers(text, voice):
            await asyncio.sleep(60)

        async def scenario():
            with patch.object(tts, 'SYNTHESIS_TIMEOUT_SECONDS', 0.05), \
                 patch.object(tts, '_render', side_effect=never_answers):
                await tts.synthesize('Hello there.')
        with self.assertRaisesRegex(TimeoutError, 'speech.platform.bing.com'):
            asyncio.run(scenario())

    def test_empty_text_is_rejected_before_any_work(self):
        async def scenario():
            with patch.object(tts, '_render') as render:
                with self.assertRaises(ValueError):
                    await tts.synthesize('   ')
                return render.call_count
        self.assertEqual(asyncio.run(scenario()), 0)

    def test_the_cache_stays_bounded_during_a_long_call(self):
        async def scenario():
            with patch.object(tts, '_render', return_value=FAKE_MP3):
                for index in range(tts.CACHE_LIMIT + 20):
                    await tts.synthesize(f'Line number {index}.')
        asyncio.run(scenario())
        self.assertLessEqual(len(tts._cache), tts.CACHE_LIMIT)

    def test_the_voice_can_be_changed_with_one_setting(self):
        with patch.dict('os.environ', {'TTS_VOICE': 'en-GB-SoniaNeural'}):
            self.assertEqual(tts.configured_voice(), 'en-GB-SoniaNeural')
        with patch.dict('os.environ', {'TTS_VOICE': ''}):
            self.assertEqual(tts.configured_voice(), tts.DEFAULT_VOICE)


if __name__ == '__main__':
    unittest.main()


class VoiceChoiceTests(unittest.TestCase):
    """Picking a voice must follow the call, not just the browser that chose it."""

    def setUp(self):
        server.sessiondb.clear()
        self.client = TestClient(server.app)
        self.env = patch.dict('os.environ', {'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key', 'TTS_VOICE': ''})
        self.env.start()
        self.addCleanup(self.env.stop)

    def create(self, voice=None):
        payload = {'raw_prompt': 'Early check-in', 'user_language': 'English'}
        if voice:
            payload['voice'] = voice
        with patch.object(server, 'extract_rules_from_prompt', return_value=RULES):
            created = self.client.post('/api/session/create', json=payload).json()
        server.append_turn(server.sessiondb[created['session_id']], 'agent', 'Hello there.')
        return created

    def test_a_call_defaults_to_the_voice_the_team_settled_on(self):
        created = self.create()
        self.assertEqual(created['tts_voice'], 'en-US-AvaNeural')
        self.assertEqual(tts.SELECTABLE_VOICES['female'], 'en-US-AvaNeural')

    def test_a_male_voice_can_be_chosen_when_the_call_is_created(self):
        created = self.create(voice='male')
        self.assertEqual(created['tts_voice'], 'en-US-AndrewNeural')
        self.assertEqual(server.sessiondb[created['session_id']]['voice_choice'], 'male')

    def test_the_voice_can_be_switched_during_a_call(self):
        session = self.create()['session_id']
        result = self.client.post(f'/api/session/{session}/voice-choice', json={'voice': 'male'}).json()
        self.assertEqual(result['tts_voice'], 'en-US-AndrewNeural')
        status = self.client.get(f'/api/session/{session}/status').json()
        self.assertEqual(status['tts_voice_choice'], 'male')

    def test_an_invented_voice_is_refused(self):
        session = self.create()['session_id']
        response = self.client.post(f'/api/session/{session}/voice-choice', json={'voice': 'robot'})
        self.assertEqual(response.status_code, 422)

    def test_the_chosen_voice_is_what_actually_gets_synthesized(self):
        session = self.create(voice='male')['session_id']
        with patch.object(server.tts, 'synthesize', return_value=FAKE_MP3) as synthesize:
            self.client.post(f'/api/session/{session}/speech', json={'text': 'Hello there.'})
        self.assertEqual(synthesize.call_args.args[1], 'en-US-AndrewNeural')

    def test_the_receptionist_hears_the_voice_the_controller_chose(self):
        created = self.create(voice='male')
        token = created['participant_path'].rsplit('/', 1)[-1]
        self.assertEqual(self.client.get(f'/api/participant/{token}').json()['voice_choice'], 'male')
        with patch.object(server.tts, 'synthesize', return_value=FAKE_MP3) as synthesize:
            self.client.post(f'/api/participant/{token}/speech', json={'text': 'Hello there.'})
        self.assertEqual(synthesize.call_args.args[1], 'en-US-AndrewNeural')

    def test_a_live_call_uses_a_matching_assemblyai_voice(self):
        """The two tiers should not sound like different people."""
        import voice_agent
        self.assertEqual(voice_agent.voice_for({'voice_choice': 'male'}), 'michael')
        self.assertEqual(voice_agent.voice_for({'voice_choice': 'female'}), 'eve')
        for choice in ('female', 'male'):
            self.assertIn(voice_agent.voice_for({'voice_choice': choice}), voice_agent.AVAILABLE_VOICES)


class DemoVoiceTests(unittest.TestCase):
    """The guided demo is what gets presented, so it must not sound robotic."""

    def setUp(self):
        self.client = TestClient(server.app)
        server._demo_speech_hits.clear()

    def speak(self, text, voice=None):
        payload = {'text': text}
        if voice:
            payload['voice'] = voice
        with patch.object(server.tts, 'synthesize', return_value=FAKE_MP3) as synthesize:
            response = self.client.post('/api/demo/speech', json=payload)
        return response, synthesize

    def test_the_demo_speaks_in_the_natural_voice_without_a_call_or_a_key(self):
        response, _ = self.speak('My guest can go up to twenty dollars for the early check-in.')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'audio/mpeg')
        self.assertEqual(response.content, FAKE_MP3)

    def test_the_demo_follows_the_chosen_voice(self):
        _, synthesize = self.speak('Would that work on your side?', voice='male')
        self.assertEqual(synthesize.call_args.args[1], 'en-US-AndrewNeural')
        _, synthesize = self.speak('Would that work on your side?', voice='female')
        self.assertEqual(synthesize.call_args.args[1], 'en-US-AvaNeural')

    def test_a_typed_demo_reply_can_be_spoken(self):
        """The demo lets the presenter write their own words; those must speak too."""
        response, _ = self.speak('Actually, please hold that room until Friday morning.')
        self.assertEqual(response.status_code, 200)

    def test_the_open_demo_route_cannot_become_a_speech_service(self):
        long_text = 'a' * (server.DEMO_SPEECH_LIMIT + 1)
        self.assertEqual(self.speak(long_text)[0].status_code, 413)
        self.assertEqual(self.speak('   ')[0].status_code, 400)

    def test_the_demo_route_is_rate_limited(self):
        with patch.object(server.tts, 'synthesize', return_value=FAKE_MP3):
            statuses = [self.client.post('/api/demo/speech', json={'text': f'Line {index}.'}).status_code
                        for index in range(server.DEMO_SPEECH_PER_MINUTE + 3)]
        self.assertEqual(statuses[0], 200)
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(statuses.count(200), server.DEMO_SPEECH_PER_MINUTE)

    def test_a_failure_lets_the_page_fall_back_rather_than_going_silent(self):
        with patch.object(server.tts, 'synthesize', side_effect=RuntimeError('edge-tts is down')):
            response = self.client.post('/api/demo/speech', json={'text': 'Hello there.'})
        self.assertEqual(response.status_code, 502)
