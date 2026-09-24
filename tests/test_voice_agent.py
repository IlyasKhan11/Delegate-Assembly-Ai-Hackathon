"""Live voice call regressions.

These prove the thing that matters about routing CallBridge through the
AssemblyAI Voice Agent API: the human approval gate still stands, and no
credential escapes to the browser. No AssemblyAI request is made here.
"""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["DELEGATE_DATABASE"] = ":memory:"
import app as server
import voice_agent
from models import ExtractedRules, RuleConstraint, TurnDecision, StagedResponse

RULES = ExtractedRules(
    intent="early_check_in", target_business="Grandview Harbour Hotel",
    opening_phrase="Hello, I am calling about early check-in for my client.",
    constraints=[RuleConstraint(parameter="price", operator="max", value="20", unit="USD")],
)


def collect(generator):
    """Drains an async generator into the list of text pieces it streamed."""
    async def drain():
        events = []
        async for chunk in generator:
            events.append(chunk)
        return events
    return asyncio.run(drain())


def spoken_words(events):
    """Reassembles the sentence a caller would actually hear."""
    text = ""
    for event in events:
        payload = event[len("data: "):].strip()
        if payload == "[DONE]":
            continue
        text += json.loads(payload)["choices"][0]["delta"].get("content", "")
    return text


class VoiceConfigTests(unittest.TestCase):
    def test_missing_settings_are_explained_not_silently_ignored(self):
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': '', 'PUBLIC_BASE_URL': ''}):
            self.assertIn("ASSEMBLYAI_API_KEY", voice_agent.unavailable_reason())
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'k', 'PUBLIC_BASE_URL': ''}):
            self.assertIn("PUBLIC_BASE_URL", voice_agent.unavailable_reason())
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'k', 'PUBLIC_BASE_URL': 'http://localhost:8000'}):
            self.assertIn("https", voice_agent.unavailable_reason())
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'k', 'PUBLIC_BASE_URL': 'https://demo.example.com'}):
            self.assertEqual(voice_agent.unavailable_reason(), "")

    def test_public_settings_never_carry_a_credential(self):
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'secret-voice-key', 'PUBLIC_BASE_URL': 'https://demo.example.com'}):
            settings = voice_agent.public_voice_settings()
        self.assertTrue(settings["voice_call_configured"])
        self.assertNotIn("secret-voice-key", json.dumps(settings))
        self.assertNotIn(voice_agent.bridge_token(), json.dumps(settings))

    def test_agent_is_pointed_at_this_server_and_the_chosen_voice(self):
        with patch.dict('os.environ', {'ASSEMBLYAI_API_KEY': 'k', 'PUBLIC_BASE_URL': 'https://demo.example.com', 'VOICE_AGENT_VOICE': 'michael'}):
            payload = voice_agent.agent_payload({"session_id": "abc123", "rules": RULES.model_dump()})
        self.assertEqual(payload["llm"][0]["base_url"], "https://demo.example.com/v1/voice/abc123")
        self.assertEqual(payload["llm"][0]["api_key"], voice_agent.bridge_token())
        self.assertEqual(payload["voice"]["voice_id"], "michael")
        self.assertEqual(payload["greeting"], RULES.opening_phrase)

    def test_an_unknown_voice_falls_back_instead_of_failing_mid_call(self):
        with patch.dict('os.environ', {'VOICE_AGENT_VOICE': 'jarvis'}):
            self.assertEqual(voice_agent.configured_voice(), voice_agent.DEFAULT_VOICE)

    def test_a_continued_sentence_does_not_run_into_the_previous_one(self):
        events = list(voice_agent.speech_events("Hold on please.", "id", first=True))
        events += list(voice_agent.speech_events("Twenty dollars works.", "id"))
        self.assertEqual(spoken_words(events), "Hold on please. Twenty dollars works.")


class VoiceBridgeTests(unittest.TestCase):
    """The endpoint AssemblyAI calls to find out what the agent should say."""

    def setUp(self):
        server.sessiondb.clear()
        self.client = TestClient(server.app)
        self.env = patch.dict('os.environ', {
            'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key',
            'ASSEMBLYAI_API_KEY': 'test-voice-key', 'PUBLIC_BASE_URL': 'https://demo.example.com',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.session = self.create_session()

    def create_session(self, mode="assist"):
        with patch.object(server, 'extract_rules_from_prompt', return_value=RULES):
            response = self.client.post('/api/session/create', json={
                'raw_prompt': 'Early check-in, max $20', 'user_language': 'Spanish', 'mode': mode})
        session_id = response.json()['session_id']
        server.sessiondb[session_id]['voice_live'] = True
        server.sessiondb[session_id]['voice_agent_id'] = 'agent-123'
        server.sessiondb[session_id]['voice_outbox'] = []
        return session_id

    def headers(self):
        return {"Authorization": f"Bearer {voice_agent.bridge_token()}"}

    def test_the_bridge_refuses_anyone_without_the_shared_secret(self):
        response = self.client.post(f'/v1/voice/{self.session}/chat/completions',
                                    json={'messages': [{'role': 'user', 'content': 'Hello?'}]},
                                    headers={"Authorization": "Bearer wrong-token"})
        self.assertEqual(response.status_code, 401)

    def test_a_routine_turn_is_answered_straight_away(self):
        decision = TurnDecision(is_dealbreaker=False, draft_response="Could I ask about availability?",
                                translated_response="¿Podría preguntar por disponibilidad?")
        session = self.create_session(mode="delegate")
        with patch.object(server, 'evaluate_caller_turn', return_value=decision):
            response = self.client.post(f'/v1/voice/{session}/chat/completions',
                                        json={'messages': [{'role': 'user', 'content': 'Front desk, how can I help?'}]},
                                        headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Could I ask about availability?")

    def test_a_turn_needing_approval_stalls_and_speaks_nothing_else(self):
        """The receptionist hears a holding phrase, never the unapproved reply."""
        decision = TurnDecision(is_dealbreaker=True, violation_parameter="price",
                                violation_reason="The hotel is asking for $30, above your $20 limit.",
                                immediate_stalling_phrase="Hold on a moment while I check with my client.",
                                draft_response="Yes, thirty dollars is fine.")
        with patch.object(server, 'evaluate_caller_turn', return_value=decision), \
             patch.object(server.voice_agent, 'approval_timeout', return_value=0.3):
            events = collect(server.voice_reply_stream(self.session, "Early check-in is $30.", "id", "callbridge"))
        heard = spoken_words(events)
        # The guardrail replaces the model's stalling phrase with its own
        # non-committal line, and that is the line the caller must hear.
        stall = server.sessiondb[self.session]["last_decision"]["immediate_stalling_phrase"]
        self.assertIn(stall, heard)
        self.assertNotIn("committing", stall.lower())
        self.assertNotIn("thirty dollars is fine", heard)
        self.assertIn(voice_agent.FALLBACK_AFTER_TIMEOUT, heard)
        self.assertEqual(events[-1], "data: [DONE]\n\n")

    def test_the_approved_reply_reaches_the_caller_on_the_held_line(self):
        server.sessiondb[self.session]['voice_outbox'] = ["Would twenty dollars work instead?"]
        with patch.object(server, 'voice_turn_plan', return_value={"speak_now": None, "stall": "One moment please."}):
            events = collect(server.voice_reply_stream(self.session, "It is $30.", "id", "callbridge"))
        self.assertEqual(spoken_words(events), "One moment please. Would twenty dollars work instead?")

    def test_approving_a_draft_releases_it_to_the_waiting_voice_call(self):
        """The approve button is what puts words in the caller's ear."""
        draft = StagedResponse(english_response="Would twenty dollars work?",
                               translated_response="¿Le funcionarían veinte dólares?",
                               action_summary="Counter-offer of $20")
        with patch.object(server, 'generate_staged_draft', return_value=draft):
            staged = self.client.post(f'/api/session/{self.session}/user-action',
                                      json={'chosen_action': 'Negotiate down to $20'})
        self.assertEqual(server.sessiondb[self.session]['voice_outbox'], [],
                         "drafting alone must not queue speech")
        self.client.post(f'/api/session/{self.session}/approve',
                         json={'draft_id': staged.json()['draft_id']})
        self.assertEqual(server.sessiondb[self.session]['voice_outbox'], ["Would twenty dollars work?"])

    def test_a_rejected_draft_is_never_queued_for_speech(self):
        draft = StagedResponse(english_response="Thirty dollars is fine.",
                               translated_response="Treinta dólares está bien.", action_summary="Accepted $30")
        with patch.object(server, 'generate_staged_draft', return_value=draft):
            self.client.post(f'/api/session/{self.session}/user-action', json={'chosen_action': 'Accept $30'})
        self.client.post(f'/api/session/{self.session}/reject')
        self.assertEqual(server.sessiondb[self.session]['voice_outbox'], [])

    def test_a_second_turn_while_a_decision_is_open_does_not_start_another(self):
        server.sessiondb[self.session]['status'] = 'DECISION_REQUIRED'
        with patch.object(server, 'evaluate_caller_turn') as evaluate:
            plan = server.voice_turn_plan(self.session, "Are you still there?")
            evaluate.assert_not_called()
        self.assertIsNone(plan["speak_now"])
        self.assertIn(plan["stall"], voice_agent.HOLDING_PHRASES)

    def test_an_ended_call_says_goodbye_rather_than_erroring_into_silence(self):
        server.sessiondb[self.session]['status'] = 'COMPLETED'
        response = self.client.post(f'/v1/voice/{self.session}/chat/completions',
                                    json={'messages': [{'role': 'user', 'content': 'Anything else?'}]},
                                    headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn("goodbye", response.json()["choices"][0]["message"]["content"].lower())

    def test_the_stalling_phrase_is_written_into_the_transcript(self):
        """The controller's transcript must match what the receptionist heard."""
        decision = TurnDecision(is_dealbreaker=False, draft_response="Yes, that is fine.",
                                translated_response="Sí, está bien.")
        with patch.object(server, 'evaluate_caller_turn', return_value=decision):
            plan = server.voice_turn_plan(self.session, "Can I confirm the booking?")
        spoken = [turn["text"] for turn in server.sessiondb[self.session]["transcript_history"]
                  if turn["speaker"] == "agent"]
        self.assertIn(plan["stall"], spoken)
        self.assertNotIn("Yes, that is fine.", spoken)


class VoiceCredentialTests(unittest.TestCase):
    def setUp(self):
        server.sessiondb.clear()
        self.client = TestClient(server.app)
        self.env = patch.dict('os.environ', {
            'AI_PROVIDER': 'aimlapi', 'AIML_API_KEY': 'test-key',
            'ASSEMBLYAI_API_KEY': 'secret-voice-key', 'PUBLIC_BASE_URL': 'https://demo.example.com',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        with patch.object(server, 'extract_rules_from_prompt', return_value=RULES):
            created = self.client.post('/api/session/create', json={
                'raw_prompt': 'Early check-in, max $20', 'user_language': 'English'}).json()
        self.session = created['session_id']
        self.participant = created['participant_path'].rsplit('/', 1)[-1]

    def test_starting_a_call_records_the_opening_line_it_will_speak(self):
        with patch.object(server.voice_agent, 'create_agent', return_value='agent-xyz'):
            result = self.client.post(f'/api/session/{self.session}/voice/start').json()
        self.assertTrue(result['voice_live'])
        spoken = [turn['text'] for turn in server.sessiondb[self.session]['transcript_history']]
        self.assertIn(RULES.opening_phrase, spoken)

    def test_the_receptionist_page_gets_a_one_time_token_not_the_api_key(self):
        with patch.object(server.voice_agent, 'create_agent', return_value='agent-xyz'):
            self.client.post(f'/api/session/{self.session}/voice/start')
        with patch.object(server.voice_agent, 'session_token', return_value='temp-token-123'):
            credentials = self.client.get(f'/api/participant/{self.participant}/voice').json()
        self.assertEqual(credentials['token'], 'temp-token-123')
        self.assertNotIn('secret-voice-key', json.dumps(credentials))
        self.assertNotIn(voice_agent.bridge_token(), json.dumps(credentials))

    def test_the_receptionist_is_told_plainly_when_no_call_is_running(self):
        credentials = self.client.get(f'/api/participant/{self.participant}/voice').json()
        self.assertFalse(credentials['voice_live'])
        self.assertIn('not started', credentials['hint'])

    def test_starting_without_a_public_address_explains_why(self):
        with patch.dict('os.environ', {'PUBLIC_BASE_URL': ''}):
            response = self.client.post(f'/api/session/{self.session}/voice/start')
        self.assertEqual(response.status_code, 503)
        self.assertIn('PUBLIC_BASE_URL', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
