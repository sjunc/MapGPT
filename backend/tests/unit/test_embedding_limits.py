import json
import math

import httpx2
import pytest
import tiktoken
from openai import OpenAI

from app.llm.embeddings import EMBEDDING_DIMENSIONS, OpenAIEmbedder


def test_long_korean_document_is_preserved_and_combined_in_input_order():
    encoding = tiktoken.get_encoding("cl100k_base")
    texts = ["짧은 설명", "서울특별시 범죄 발생 지역별 통계 " * 900, "마지막 <|endoftext|> 컬럼"]
    received = []
    chunk_vectors = []

    def respond(request):
        inputs = json.loads(request.content)["input"]
        assert len(inputs) <= 2048
        assert sum(len(tokens) for tokens in inputs) <= 300_000
        data = []
        for index, tokens in enumerate(inputs):
            assert 0 < len(tokens) <= 8191
            assert all(isinstance(token, int) for token in tokens)
            received.append(tokens)
            vector = [0.0] * EMBEDDING_DIMENSIONS
            vector[len(received) % 2] = 1.0
            chunk_vectors.append(vector)
            data.append({"index": index, "embedding": vector, "object": "embedding"})
        return httpx2.Response(200, json={"data": data[::-1], "model": "text-embedding-3-small", "object": "list"})

    client = OpenAI(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(respond)))
    vectors = OpenAIEmbedder(api_key="test", model="text-embedding-3-small", client=client).embed(texts)

    assert len(received) > len(texts)
    assert [token for part in received for token in part] == [
        token for text in texts for token in encoding.encode(text, disallowed_special=())
    ]
    assert len(vectors) == len(texts)
    assert vectors[0] == chunk_vectors[0]
    assert vectors[-1] == chunk_vectors[-1]
    weighted = [sum(v[i] * len(t) for v, t in zip(chunk_vectors[1:-1], received[1:-1], strict=True)) for i in range(2)]
    norm = math.hypot(*weighted)
    assert vectors[1][:2] == pytest.approx([value / norm for value in weighted])
    assert vectors[1][2:] == [0.0] * (EMBEDDING_DIMENSIONS - 2)


def test_many_documents_are_batched_below_request_token_limit():
    requests = []

    def respond(request):
        inputs = json.loads(request.content)["input"]
        assert sum(len(tokens) for tokens in inputs) <= 300_000
        requests.append(inputs)
        return httpx2.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.1] * EMBEDDING_DIMENSIONS, "object": "embedding"}
                    for i in range(len(inputs))
                ],
                "model": "text-embedding-3-small",
                "object": "list",
            },
        )

    client = OpenAI(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(respond)))
    vectors = OpenAIEmbedder(api_key="test", model="text-embedding-3-small", client=client).embed([" a" * 8000] * 40)
    assert len(requests) > 1
    assert len(vectors) == 40
    assert all(vector == [0.1] * EMBEDDING_DIMENSIONS for vector in vectors)
