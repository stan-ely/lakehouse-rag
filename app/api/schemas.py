from pydantic import BaseModel, ConfigDict, Field

from app.generation.answer import Answer


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)


class CitationOut(BaseModel):
    index: int
    title: str
    source_uri: str
    doc_id: str
    chunk_id: str
    doc_version: str


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class QueryResponse(BaseModel):
    request_id: str
    answer: str
    refused: bool
    refusal_reason: str | None
    grounded: bool
    citations: list[CitationOut]
    model: str | None
    provider: str | None
    usage: UsageOut
    cost_usd: float | None
    timings_ms: dict[str, float]
    route: str | None = None
    route_method: str | None = None
    sql: str | None = None
    sql_error: str | None = None

    @classmethod
    def from_answer(cls, request_id: str, answer: Answer) -> "QueryResponse":
        return cls(
            request_id=request_id,
            answer=answer.text,
            refused=answer.refused,
            refusal_reason=answer.refusal_reason,
            grounded=answer.grounded,
            citations=[
                CitationOut(
                    index=c.index,
                    title=c.title,
                    source_uri=c.source_uri,
                    doc_id=c.doc_id,
                    chunk_id=c.chunk_id,
                    doc_version=c.doc_version,
                )
                for c in answer.citations
            ],
            model=answer.model,
            provider=answer.provider,
            usage=UsageOut(
                input_tokens=answer.usage.input_tokens,
                output_tokens=answer.usage.output_tokens,
                cache_read_tokens=answer.usage.cache_read_tokens,
                cache_write_tokens=answer.usage.cache_write_tokens,
            ),
            cost_usd=None if answer.cost_usd is None else float(answer.cost_usd),
            timings_ms=answer.timings_ms,
            route=answer.route,
            route_method=answer.route_method,
            sql=answer.sql,
            sql_error=answer.sql_error,
        )


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]
