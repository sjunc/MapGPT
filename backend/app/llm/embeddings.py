"""Dataset 메타데이터·검색어 임베딩 (OpenAI Embeddings API)."""

import math
from typing import Protocol

import openai
import tiktoken
from openai import OpenAI

# catalog.dataset_embedding.embedding 컬럼(vector(1536))과 같아야 한다.
EMBEDDING_DIMENSIONS = 1536
MAX_INPUT_TOKENS = 8191
# 32 * 8191 < 요청당 300,000토큰. 입력 개수 제한(2048)도 넘지 않는다.
EMBEDDING_BATCH_SIZE = 32


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, *, api_key: str, model: str, client: OpenAI | None = None):
        self.model = model
        self._client = client or OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # text-embedding-3-small/large의 인코딩. 토큰 배열을 보내 한글 경계도 손실 없이 나눈다.
        encoding = tiktoken.get_encoding("cl100k_base")
        chunks: list[list[int]] = []
        counts = []
        for text in texts:
            tokens = encoding.encode(text, disallowed_special=())
            if not tokens:
                raise EmbeddingError("빈 텍스트는 임베딩할 수 없습니다")
            parts = [tokens[start : start + MAX_INPUT_TOKENS] for start in range(0, len(tokens), MAX_INPUT_TOKENS)]
            counts.append(len(parts))
            chunks.extend(parts)

        vectors = []
        for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
            vectors.extend(self._embed_batch(chunks[start : start + EMBEDDING_BATCH_SIZE]))

        combined = []
        offset = 0
        for count in counts:
            if count == 1:
                combined.append(vectors[offset])
            else:
                # 긴 설명도 Dataset당 벡터 하나로 유지한다. 짧은 마지막 조각은 길이만큼만 반영한다.
                weighted = [0.0] * EMBEDDING_DIMENSIONS
                for index in range(offset, offset + count):
                    for dimension, value in enumerate(vectors[index]):
                        weighted[dimension] += value * len(chunks[index])
                norm = math.sqrt(sum(value * value for value in weighted))
                if norm == 0:
                    raise EmbeddingError("임베딩 조각의 합이 영벡터여서 검색 벡터를 만들 수 없습니다")
                combined.append([value / norm for value in weighted])
            offset += count
        return combined

    def _embed_batch(self, tokens: list[list[int]]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(
                model=self.model, input=tokens, dimensions=EMBEDDING_DIMENSIONS, encoding_format="float"
            )
        except openai.APIError as error:
            raise EmbeddingError(f"OpenAI 임베딩 호출 실패: {error}") from error
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(tokens) or any(len(vector) != EMBEDDING_DIMENSIONS for vector in vectors):
            raise EmbeddingError("임베딩 응답의 개수나 차원이 요청과 다릅니다")
        return vectors
