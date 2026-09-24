"""Persistence, retry and concurrent human control tests; no provider requests."""
import os
os.environ['DELEGATE_DATABASE'] = ':memory:'
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from fastapi import HTTPException
import app as server
from call_store import CallStore
from models import IncomingUserRequest, IncomingCallerTurn, UserDecisionAction, DraftApproval, ExtractedRules, StagedResponse, TurnDecision, FinalSummary


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        server.sessiondb.clear()
        rules = ExtractedRules(intent='hotel', target_business='Hotel')
        with patch.dict(os.environ, {'AI_PROVIDER':'aimlapi', 'AIML_API_KEY':'test-key'}), patch.object(server, 'extract_rules_from_prompt', return_value=rules):
            self.sid = server.create_session(IncomingUserRequest(raw_prompt='Check room availability', mode='assist'))['session_id']
        self.session = server.sessiondb[self.sid]
        self.draft = StagedResponse(english_response='Is a room available?', translated_response='Is a room available?', action_summary='Ask about availability')

    def test_draft_retry_calls_provider_once_and_keeps_same_identity(self):
        action = UserDecisionAction(chosen_action='Ask about rooms', request_id='retry-1')
        with patch.object(server, 'generate_staged_draft', return_value=self.draft) as model:
            first = server.handle_user_action(self.sid, action)
            second = server.handle_user_action(self.sid, action)
        self.assertEqual(first, second)
        self.assertEqual(model.call_count, 1)
        with self.assertRaises(HTTPException) as error:
            server.handle_user_action(self.sid, UserDecisionAction(chosen_action='Different request', request_id='retry-1'))
        self.assertEqual(error.exception.status_code, 409)

    def test_old_draft_cannot_approve_new_words_and_approval_retry_does_not_repeat(self):
        with patch.object(server, 'generate_staged_draft', return_value=self.draft):
            old = server.handle_user_action(self.sid, UserDecisionAction(chosen_action='First'))
            new = server.handle_user_action(self.sid, UserDecisionAction(chosen_action='Second'))
        with self.assertRaises(HTTPException):
            server.approve_and_speak(self.sid, DraftApproval(draft_id=old['draft_id']))
        approval = DraftApproval(draft_id=new['draft_id'])
        self.assertEqual(server.approve_and_speak(self.sid, approval), server.approve_and_speak(self.sid, approval))
        self.assertEqual(len(self.session['transcript_history']), 1)

    def test_pause_returns_while_ai_is_running_and_discards_late_draft(self):
        started, release = Event(), Event()
        def slow_model(*args):
            started.set()
            if not release.wait(5):
                raise TimeoutError('test did not release provider')
            return self.draft
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(server, 'generate_staged_draft', side_effect=slow_model):
            pending = pool.submit(server.handle_user_action, self.sid, UserDecisionAction(chosen_action='Ask about rooms'))
            try:
                self.assertTrue(started.wait(2))
                paused = pool.submit(server.interrupt_call, self.sid).result(timeout=2)
                self.assertEqual(paused['status'], 'DECISION_REQUIRED')
            finally:
                release.set()
            with self.assertRaises(HTTPException) as error:
                pending.result(timeout=2)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(self.session['pending_draft'])
        self.assertEqual(self.session['transcript_history'], [])

    def test_caller_retry_does_not_duplicate_transcript(self):
        turn = IncomingCallerTurn(caller_text='Good morning', request_id='human-1')
        decision = TurnDecision(is_dealbreaker=False, draft_response='Hello')
        with patch.object(server, 'evaluate_caller_turn', return_value=decision) as model:
            self.assertEqual(server.evaluate_turn(self.sid, turn), server.evaluate_turn(self.sid, turn))
        self.assertEqual(model.call_count, 1)
        self.assertEqual(len(self.session['transcript_history']), 1)

    def test_completed_summary_is_persisted_and_not_regenerated(self):
        summary = FinalSummary(outcome_headline='No agreement', outcome_subtext='Ended before agreement', confirmed_items=[], unresolved_items=['Room availability'])
        with patch.object(server, 'generate_call_summary', return_value=summary) as model:
            self.assertEqual(server.complete_call(self.sid), server.complete_call(self.sid))
        self.assertEqual(model.call_count, 1)
        self.assertEqual(server.store.load_all()[self.sid]['summary'], summary.model_dump())

    def test_local_store_restores_private_call_and_participant_token_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/calls.sqlite3'
            first = CallStore(path)
            first.save(self.session)
            first.close()
            second = CallStore(path)
            try:
                restored = second.load_all()[self.sid]
                self.assertEqual(restored, self.session)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            finally:
                second.close()

    def test_ending_marks_call_closed_before_waiting_for_inflight_model(self):
        started, release, ended = Event(), Event(), Event()
        original_save = server.save_session
        def save(session):
            original_save(session)
            if session['status'] == 'COMPLETED':
                ended.set()
        def slow_model(*args):
            started.set()
            release.wait(5)
            return self.draft
        summary = FinalSummary(outcome_headline='Ended', outcome_subtext='Nothing agreed')
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(server, 'generate_staged_draft', side_effect=slow_model), patch.object(server, 'generate_call_summary', return_value=summary), patch.object(server, 'save_session', side_effect=save):
            pending = pool.submit(server.handle_user_action, self.sid, UserDecisionAction(chosen_action='Ask about rooms'))
            try:
                self.assertTrue(started.wait(2))
                finishing = pool.submit(server.complete_call, self.sid)
                self.assertTrue(ended.wait(2))
                self.assertEqual(self.session['status'], 'COMPLETED')
            finally:
                release.set()
            with self.assertRaises(HTTPException):
                pending.result(timeout=2)
            self.assertEqual(finishing.result(timeout=2)['status'], 'COMPLETED')
        self.assertEqual(self.session['transcript_history'], [])

    def test_a_blocked_offer_cannot_speak_model_supplied_acceptance(self):
        decision = TurnDecision(is_dealbreaker=True, violation_reason='Needs review', immediate_stalling_phrase='I accept the price and confirm the booking.')
        with patch.object(server, 'evaluate_caller_turn', return_value=decision):
            server.evaluate_turn(self.sid, IncomingCallerTurn(caller_text='Shall I confirm the booking?'))
        self.assertEqual(self.session['transcript_history'][-1]['text'], 'Let me check with my client before agreeing to anything.')
