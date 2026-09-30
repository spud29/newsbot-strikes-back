"""Regression: generate_embedding must use /api/embed and parse embeddings[0].

The old /api/embeddings endpoint (key "prompt", response "embedding") returns
{"embedding": []} + 500 on Ollama 0.32.15, killing every entry at the dedup
step. The fix targets /api/embed (key "input", response "embeddings").
"""
import unittest
from unittest import mock

import ollama_client as oc


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise __import__("requests").exceptions.HTTPError(f"{self.status_code}")
        return None

    def json(self):
        return self._payload


class EmbeddingEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = oc.OllamaClient()
        self.client.base_url = "http://localhost:11434"
        self.client.embedding_model = "qwen3-embedding:0.6b"
        self.client.embedding_num_ctx = 512

    @mock.patch("ollama_client.requests.post")
    def test_uses_new_embed_endpoint_and_parses_embeddings(self, mock_post):
        vector = [0.1] * 1024
        mock_post.return_value = FakeResponse(
            {"model": "qwen3-embedding:0.6b", "embeddings": [vector]}
        )
        result = self.client.generate_embedding("test content")

        self.assertEqual(result, vector)
        url = mock_post.call_args[0][0]
        self.assertIn("/api/embed", url)
        self.assertNotIn("/api/embeddings", url)
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["input"], "test content")
        self.assertNotIn("prompt", body)
        self.assertEqual(body["options"]["num_ctx"], 512)

    @mock.patch("ollama_client.requests.post")
    def test_raises_on_empty_embeddings(self, mock_post):
        mock_post.return_value = FakeResponse({"model": "x", "embeddings": []})
        with self.assertRaises(ValueError):
            self.client.generate_embedding("test content")


if __name__ == "__main__":
    unittest.main()
