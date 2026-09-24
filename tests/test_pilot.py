"""Pilot boundaries tested with independent host and participant clients."""
import os
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
os.environ['DELEGATE_DATABASE'] = ':memory:'
import app as server
import pilot_access
from models import ExtractedRules


class PilotTests(unittest.TestCase):
    def setUp(self):
        server.sessiondb.clear()
        server.participant_sessions.clear()
        pilot_access.hits.clear()
        self.env = patch.dict(os.environ, {'DELEGATE_PILOT_PASSCODE': 'test-only-long-passphrase', 'DELEGATE_PUBLIC_MODE': 'true', 'AIML_API_KEY': 'test-key', 'AI_PROVIDER': 'aimlapi'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.host = TestClient(server.app, base_url='https://testserver')
        self.guest = TestClient(server.app, base_url='https://testserver')

    def login(self):
        return self.host.post('/api/access/login', json={'passcode': 'test-only-long-passphrase'})

    def create(self):
        self.login()
        with patch.object(server, 'extract_rules_from_prompt', return_value=ExtractedRules(intent='test', target_business='Hotel')):
            data = self.host.post('/api/session/create', json={'raw_prompt': 'Private task', 'user_language': 'Portuguese (Brazil)'}).json()
        return data['session_id'], data['participant_path'].split('/')[-1]

    def test_controller_requires_login_and_cookie_is_secure(self):
        self.assertEqual(self.guest.get('/app', follow_redirects=False).status_code, 303)
        self.assertEqual(self.guest.post('/api/session/create', json={'raw_prompt': 'test'}).status_code, 401)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        for value in ['HttpOnly', 'Secure', 'SameSite=strict']:
            self.assertIn(value, response.headers['set-cookie'])
        self.assertEqual(self.host.get('/app').status_code, 200)

    def test_forged_and_expired_cookies_are_rejected(self):
        for cookie in ['123.fake.fake', 'malformed', f'1.nonce.{pilot_access.signature("1.nonce")}']:
            self.guest.cookies.set(pilot_access.COOKIE, cookie)
            self.assertEqual(self.guest.get('/api/session/private/status').status_code, 401)

    def test_public_mode_fails_closed_without_strong_passcode(self):
        with patch.dict(os.environ, {'DELEGATE_PILOT_PASSCODE': 'short'}):
            self.assertEqual(self.guest.post('/api/session/create', json={}).status_code, 503)
            self.assertFalse(self.guest.get('/api/health').json()['public_access_ready'])

    def test_participant_needs_only_invitation_and_cannot_control(self):
        sid, token = self.create()
        self.assertEqual(self.guest.get(f'/api/participant/{token}').status_code, 200)
        self.assertEqual(self.guest.get(f'/api/session/{sid}/status').status_code, 401)
        with self.assertRaises(Exception):
            with self.guest.websocket_connect(f'/ws/call/{sid}'):
                pass

    def test_cross_origin_mutation_is_rejected(self):
        self.login()
        self.assertEqual(self.host.post('/api/access/logout', headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.host.get('/app').status_code, 200)

    def test_failed_login_attempts_are_limited(self):
        for _ in range(8):
            self.assertEqual(self.guest.post('/api/access/login', json={'passcode': 'wrong'}).status_code, 401)
        self.assertEqual(self.login().status_code, 429)

    def test_expired_invitation_cannot_read_speak_or_mint_credentials(self):
        sid, token = self.create()
        server.sessiondb[sid]['participant_expires_at'] = time.time() - 1
        for path in ['', '/listen', '/voice']:
            self.assertEqual(self.guest.get(f'/api/participant/{token}{path}').status_code, 410)
        self.assertEqual(self.guest.post(f'/api/participant/{token}/turn', json={'caller_text': 'Hi'}).status_code, 410)

    def test_renew_and_revoke_persist_and_disconnect_old_links(self):
        sid, token = self.create()
        result = self.host.post(f'/api/session/{sid}/invitation/renew').json()
        renewed = result['participant_path'].split('/')[-1]
        self.assertNotEqual(token, renewed)
        self.assertEqual(self.guest.get(f'/api/participant/{token}').status_code, 404)
        self.assertEqual(self.guest.get(f'/api/participant/{renewed}').status_code, 200)
        self.assertEqual(server.store.load_all()[sid]['participant_token'], renewed)
        self.assertEqual(self.host.post(f'/api/session/{sid}/invitation/revoke').status_code, 200)
        self.assertEqual(self.guest.get(f'/api/participant/{renewed}').status_code, 404)
        self.assertIsNone(self.host.get(f'/api/session/{sid}/status').json()['participant_path'])
        self.assertIsNone(server.store.load_all()[sid]['participant_token'])

    def test_delete_requires_completed_call_and_removes_saved_data(self):
        sid, token = self.create()
        self.assertEqual(self.host.post(f'/api/session/{sid}/delete').status_code, 409)
        server.sessiondb[sid]['status'] = 'COMPLETED'
        self.assertEqual(self.host.post(f'/api/session/{sid}/delete').status_code, 200)
        self.assertNotIn(sid, server.store.load_all())
        self.assertEqual(self.host.get(f'/api/session/{sid}/status').status_code, 404)
        self.assertEqual(self.guest.get(f'/api/participant/{token}').status_code, 404)

    def test_sensitive_responses_are_not_cached(self):
        sid, token = self.create()
        response = self.guest.get(f'/api/participant/{token}')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        self.assertEqual(response.headers['x-frame-options'], 'DENY')

    def test_logout_revokes_browser_access(self):
        self.login()
        self.host.post('/api/access/logout')
        self.assertEqual(self.host.get('/api/session/anything/status').status_code, 401)

    def test_ended_room_cannot_mint_listening_tokens(self):
        sid, token = self.create()
        server.sessiondb[sid]['status'] = 'COMPLETED'
        self.assertEqual(self.guest.get(f'/api/participant/{token}/listen').status_code, 409)

    def test_spend_rate_limit_keeps_emergency_controls_available(self):
        sid, _ = self.create()
        pilot_access.hits['pilot-work'] = [time.monotonic()] * 120
        self.assertEqual(self.host.post(f'/api/session/{sid}/user-action', json={'chosen_action': 'Hello'}).status_code, 429)
        self.assertEqual(self.host.post(f'/api/session/{sid}/interrupt').status_code, 200)

    def test_late_saves_cannot_resurrect_a_deleted_conversation(self):
        sid, _ = self.create()
        stale = server.sessiondb[sid]
        stale['status'] = 'COMPLETED'
        self.assertEqual(self.host.post(f'/api/session/{sid}/delete').status_code, 200)
        server.save_session(stale)
        self.assertNotIn(sid, server.store.load_all())
