"""Redacted activity wrapper for streamed LLM responses."""

from __future__ import annotations

from typing import Any, Callable, Optional


async def stream_llm_with_activity(
    url: str,
    model: str,
    messages: list[dict],
    *,
    temperature: float,
    max_tokens: int,
    headers: Optional[dict],
    timeout: int,
    prompt_type: Optional[str],
    tools: Optional[list[dict]],
    session_id: Optional[str],
    owner: Optional[str],
    surface: Optional[str],
    correlation_id: Optional[str],
    task_id: Optional[str],
    doc_id: Optional[str],
    stream_impl_func: Callable[..., Any],
    delta_chars_func: Callable[[str], int],
    usage_func: Callable[[str], tuple[int | None, int | None]],
    error_class_func: Callable[[str], Optional[str]],
    record_activity_func: Callable[..., None],
    detect_provider_func: Callable[[str], str],
    time_func: Callable[[], float],
):
    """Yield stream chunks while recording redacted activity metadata."""
    start = time_func()
    output_chars = 0
    input_tokens = None
    output_tokens = None
    status = "success"
    error_class = None
    try:
        async for chunk in stream_impl_func(
            url,
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            headers=headers,
            timeout=timeout,
            prompt_type=prompt_type,
            tools=tools,
            session_id=session_id,
        ):
            output_chars += delta_chars_func(chunk)
            usage_in, usage_out = usage_func(chunk)
            if usage_in is not None:
                input_tokens = usage_in
            if usage_out is not None:
                output_tokens = usage_out
            chunk_error = error_class_func(chunk)
            if chunk_error:
                status = "error"
                error_class = chunk_error
            yield chunk
    except Exception as exc:
        status = "error"
        error_class = type(exc).__name__
        raise
    finally:
        record_activity_func(
            owner=owner,
            surface=surface,
            correlation_id=correlation_id,
            session_id=session_id,
            task_id=task_id,
            doc_id=doc_id,
            prompt_type=prompt_type or "stream_llm",
            provider=detect_provider_func(url),
            endpoint_url=url,
            model=model,
            messages=messages,
            output_chars=output_chars,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=int((time_func() - start) * 1000),
            status=status,
            error_class=error_class,
            cache_hit=False,
            side_effects=("stream",),
        )
