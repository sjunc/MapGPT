import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.domain.catalog import AUTO_PUBLIC_API_CATEGORY, DatasetMetadata, RegionRef
from app.domain.enums import SpatialLevel


def _auto_clause(auto: bool | None) -> sql.SQL:
    """자동 후보 조건 (d.metadata 기준, 인자 auto_category): None이면 모두, True면 자동 후보만, False면 뺀다."""
    if auto is None:
        return sql.SQL("TRUE")
    if auto:
        return sql.SQL("d.metadata->>'category' = %(auto_category)s")
    return sql.SQL("d.metadata->>'category' IS DISTINCT FROM %(auto_category)s")


def upsert_dataset(conn: psycopg.Connection, metadata: DatasetMetadata) -> None:
    conn.execute(
        """
        INSERT INTO catalog.dataset (dataset_id, metadata, loaded_at)
        VALUES (%s, %s, now())
        ON CONFLICT (dataset_id) DO UPDATE SET metadata = EXCLUDED.metadata, loaded_at = EXCLUDED.loaded_at
        """,
        (metadata.dataset_id, Jsonb(metadata.model_dump(mode="json"))),
    )


def list_datasets(conn: psycopg.Connection, *, include_auto: bool = True) -> list[DatasetMetadata]:
    """카탈로그의 Dataset. include_auto=False면 자동 후보(사람 검토 전 외부 API 출처)를 뺀다."""
    rows = conn.execute(
        sql.SQL("SELECT d.metadata FROM catalog.dataset d WHERE {} ORDER BY d.dataset_id").format(
            _auto_clause(None if include_auto else False)
        ),
        {"auto_category": AUTO_PUBLIC_API_CATEGORY},
    ).fetchall()
    return [DatasetMetadata.model_validate(row[0]) for row in rows]


def find_dataset(conn: psycopg.Connection, dataset_id: str) -> DatasetMetadata | None:
    row = conn.execute("SELECT metadata FROM catalog.dataset WHERE dataset_id = %s", (dataset_id,)).fetchone()
    return DatasetMetadata.model_validate(row[0]) if row else None


def delete_auto_datasets(conn: psycopg.Connection, keep: set[str]) -> list[str]:
    """keep에 없는 자동 후보를 카탈로그에서 지운다 (검색 임베딩도 함께 지워진다). 이미 받은 데이터 테이블은 남긴다."""
    rows = conn.execute(
        "DELETE FROM catalog.dataset d WHERE d.metadata->>'category' = %(auto_category)s "
        "AND NOT (d.dataset_id = ANY(%(keep)s)) RETURNING d.dataset_id",
        {"auto_category": AUTO_PUBLIC_API_CATEGORY, "keep": sorted(keep)},
    ).fetchall()
    return sorted(row[0] for row in rows)


def list_boundary_regions(conn: psycopg.Connection) -> list[RegionRef]:
    """전국 경계에 실제로 존재하는 국가·시도·시군구 목록. 시군구 이름은 '시도 시군구'(예: 부산광역시 중구)다."""
    regions: list[RegionRef] = []
    if conn.execute("SELECT to_regclass('datasets.national_emd_boundary')").fetchone()[0] is None:
        return regions
    if conn.execute("SELECT EXISTS (SELECT 1 FROM datasets.national_emd_boundary)").fetchone()[0] is False:
        return regions
    regions.append(RegionRef(name="대한민국", level=SpatialLevel.COUNTRY, code="KR"))
    rows = conn.execute(
        """
        SELECT DISTINCT sido_nm, sido_cd, NULL::text, NULL::text
        FROM datasets.national_emd_boundary
        UNION ALL
        SELECT DISTINCT sigungu_nm, sigungu_cd, sido_nm, sido_cd
        FROM datasets.national_emd_boundary
        ORDER BY 3 NULLS FIRST, 1
        """
    ).fetchall()
    for name, code, parent_name, _parent_code in rows:
        if parent_name is None:
            regions.append(RegionRef(name=name, level=SpatialLevel.SIDO, code=code))
        else:
            # 중구·동구처럼 같은 이름의 시군구가 여러 시도에 있으므로 시도 이름을 붙여 고유하게 만든다.
            regions.append(RegionRef(name=f"{parent_name} {name}", level=SpatialLevel.SIGUNGU, code=code))
    return regions


def embedding_sources(
    conn: psycopg.Connection, *, include_auto: bool = True
) -> list[tuple[DatasetMetadata, str | None, str | None]]:
    """Dataset과, 저장된 임베딩의 모델·원문 (임베딩이 없으면 None). include_auto=False면 자동 후보를 뺀다."""
    rows = conn.execute(
        sql.SQL(
            """
            SELECT d.metadata, e.model, e.source_text
            FROM catalog.dataset d LEFT JOIN catalog.dataset_embedding e USING (dataset_id)
            WHERE {}
            ORDER BY d.dataset_id
            """
        ).format(_auto_clause(None if include_auto else False)),
        {"auto_category": AUTO_PUBLIC_API_CATEGORY},
    ).fetchall()
    return [(DatasetMetadata.model_validate(metadata), model, source_text) for metadata, model, source_text in rows]


def upsert_embedding(
    conn: psycopg.Connection, dataset_id: str, model: str, source_text: str, embedding: list[float]
) -> None:
    conn.execute(
        """
        INSERT INTO catalog.dataset_embedding (dataset_id, model, source_text, embedding, embedded_at)
        VALUES (%s, %s, %s, %s::vector, now())
        ON CONFLICT (dataset_id) DO UPDATE SET
            model = EXCLUDED.model, source_text = EXCLUDED.source_text,
            embedding = EXCLUDED.embedding, embedded_at = EXCLUDED.embedded_at
        """,
        (dataset_id, model, source_text, _vector(embedding)),
    )


def nearest_datasets(
    conn: psycopg.Connection, embedding: list[float], limit: int, *, auto: bool | None = None, model: str | None = None
) -> list[tuple[DatasetMetadata, float]]:
    """코사인 거리(<=>)가 가까운 순서로 Dataset과 유사도(1 - 거리)를 반환한다.

    auto: None이면 모두, True면 자동 후보만, False면 자동 후보를 뺀다. model을 주면 그 모델의 임베딩만 본다.
    """
    rows = conn.execute(
        sql.SQL(
            """
            SELECT d.metadata, 1 - (e.embedding <=> %(query)s::vector)
            FROM catalog.dataset_embedding e JOIN catalog.dataset d USING (dataset_id)
            WHERE {} AND (%(model)s::text IS NULL OR e.model = %(model)s)
            ORDER BY e.embedding <=> %(query)s::vector
            LIMIT %(limit)s
            """
        ).format(_auto_clause(auto)),
        {"query": _vector(embedding), "limit": limit, "auto_category": AUTO_PUBLIC_API_CATEGORY, "model": model},
    ).fetchall()
    return [(DatasetMetadata.model_validate(metadata), float(similarity)) for metadata, similarity in rows]


def _vector(values: list[float]) -> str:
    return "[" + ",".join(str(value) for value in values) + "]"
