"""Testes do transporte HTTP sem chamadas reais ao Supabase."""

import base64
import io
import json
import unittest
from datetime import UTC, datetime
from urllib.error import HTTPError
from uuid import uuid4
from unittest.mock import patch

from storage import SyncOperation
from supabase_sync import RemoteOperation, SupabaseSyncClient, SupabaseSyncError


class FakeResponse(io.BytesIO):
    def __init__(self, body: object, status: int = 200):
        super().__init__(json.dumps(body).encode("utf-8"))
        self.status = status


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def operation(entity="subjects", payload=None):
    return SyncOperation(
        id=str(uuid4()),
        entity=entity,
        entity_id=str(uuid4()),
        operation="upsert",
        payload=payload or {"name": "Python"},
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
    )


def remote_row(item, sequence=1):
    return {
        "sequence": sequence,
        "operation_id": item.id,
        "entity": item.entity,
        "entity_id": item.entity_id,
        "operation": item.operation,
        "payload": item.payload,
    }


class SupabaseSyncClientTests(unittest.TestCase):
    def setUp(self):
        self.client = SupabaseSyncClient("https://example.supabase.co", "public-key")
        self.user_id = str(uuid4())

    def test_push_posts_idempotently_then_confirms_the_exact_remote_payload(self):
        first, second = operation(), operation("sessions", {"status": "finished"})
        opener = FakeOpener([
            FakeResponse([remote_row(first), remote_row(second, sequence=2)], 201),
            FakeResponse([remote_row(first), remote_row(second, sequence=2)]),
        ])

        with patch("supabase_sync.build_opener", return_value=opener):
            confirmed = self.client.push((first, second), self.user_id, "access-token")

        self.assertEqual(confirmed, (first.id, second.id))
        post, get = [request for request, _ in opener.requests]
        self.assertEqual(post.get_method(), "POST")
        self.assertIn("on_conflict=user_id%2Coperation_id", post.full_url)
        self.assertEqual(
            post.get_header("Prefer"),
            "resolution=ignore-duplicates,return=representation",
        )
        self.assertEqual(post.get_header("Authorization"), "Bearer access-token")
        posted_rows = json.loads(post.data)
        self.assertEqual([row["operation_id"] for row in posted_rows], [first.id, second.id])
        self.assertEqual({row["user_id"] for row in posted_rows}, {self.user_id})
        self.assertEqual(get.get_method(), "GET")

    def test_push_accepts_duplicate_receipt_only_when_saved_payload_matches(self):
        item = operation()
        opener = FakeOpener([FakeResponse([], 201), FakeResponse([remote_row(item)])])
        with patch("supabase_sync.build_opener", return_value=opener):
            self.assertEqual(self.client.push((item,), self.user_id, "token"), (item.id,))

    def test_push_refuses_to_acknowledge_a_conflicting_duplicate(self):
        item = operation()
        different = remote_row(item)
        different["payload"] = {"name": "SQL"}
        opener = FakeOpener([FakeResponse([], 201), FakeResponse([different])])
        with patch("supabase_sync.build_opener", return_value=opener):
            with self.assertRaisesRegex(SupabaseSyncError, "outros dados"):
                self.client.push((item,), self.user_id, "token")

    def test_push_does_not_acknowledge_when_remote_receipt_is_missing(self):
        item = operation()
        opener = FakeOpener([FakeResponse([], 201), FakeResponse([])])
        with patch("supabase_sync.build_opener", return_value=opener):
            with self.assertRaisesRegex(SupabaseSyncError, "confirmadas no diário"):
                self.client.push((item,), self.user_id, "token")

    def test_push_http_error_does_not_include_session_token(self):
        item = operation()
        secret = "very-private-token"
        opener = FakeOpener([HTTPError("https://example.supabase.co", 403, secret, {}, None)])
        with patch("supabase_sync.build_opener", return_value=opener):
            with self.assertRaises(SupabaseSyncError) as raised:
                self.client.push((item,), self.user_id, secret)
        self.assertNotIn(secret, str(raised.exception))

    def test_fetch_uses_cursor_order_and_validates_the_page(self):
        item = operation()
        opener = FakeOpener([FakeResponse([remote_row(item, 12)])])
        with patch("supabase_sync.build_opener", return_value=opener):
            result = self.client.fetch(10, "token", limit=20)

        request = opener.requests[0][0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("sequence=gt.10", request.full_url)
        self.assertIn("order=sequence.asc", request.full_url)
        self.assertIn("limit=20", request.full_url)
        self.assertEqual(result, (RemoteOperation(12, item.id, item.entity, item.entity_id,
                                                  item.operation, item.payload),))

    def test_fetch_rejects_out_of_order_or_invalid_rows(self):
        first, second = operation(), operation()
        opener = FakeOpener([FakeResponse([remote_row(first, 12), remote_row(second, 11)])])
        with patch("supabase_sync.build_opener", return_value=opener):
            with self.assertRaisesRegex(SupabaseSyncError, "fora da ordem"):
                self.client.fetch(10, "token")

    def test_fetch_http_failure_is_safe_and_does_not_return_partial_data(self):
        opener = FakeOpener([HTTPError("https://example.supabase.co", 401, "token leaked", {}, None)])
        with patch("supabase_sync.build_opener", return_value=opener):
            with self.assertRaisesRegex(SupabaseSyncError, "HTTP 401") as raised:
                self.client.fetch(0, "access-secret")
        self.assertNotIn("access-secret", str(raised.exception))
        self.assertNotIn("token leaked", str(raised.exception))

    def test_invalid_inputs_fail_before_any_request(self):
        opener = FakeOpener([])
        with patch("supabase_sync.build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                self.client.fetch(-1, "token")
            with self.assertRaises(ValueError):
                self.client.fetch(0, "token", True)
            with self.assertRaises(ValueError):
                self.client.push((operation(),), "not-a-uuid", "token")
            with self.assertRaises(ValueError):
                self.client.push((), self.user_id, "token")
        self.assertEqual(opener.requests, [])

    def test_client_requires_https_and_refuses_privileged_service_keys(self):
        with self.assertRaises(ValueError):
            SupabaseSyncClient("http://example.com", "public-key")
        with self.assertRaises(ValueError):
            SupabaseSyncClient("https://example.supabase.co", "sb_secret_private")
        payload = base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("=")
        with self.assertRaises(ValueError):
            SupabaseSyncClient("https://example.supabase.co", f"header.{payload}.signature")
        with self.assertRaises(ValueError):
            self.client.fetch(0, f"header.{payload}.signature")

    def test_redirects_are_not_followed(self):
        item = operation()
        opener = FakeOpener([HTTPError("https://example.supabase.co", 302, "redirect", {}, None)])
        with patch("supabase_sync.build_opener", return_value=opener):
            with self.assertRaisesRegex(SupabaseSyncError, "HTTP 302"):
                self.client.push((item,), self.user_id, "token")
        self.assertEqual(len(opener.requests), 1)


if __name__ == "__main__":
    unittest.main()
