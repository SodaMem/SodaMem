"""Drop-in structural ``answer_one``: planner → code branch → optional reader."""
from __future__ import annotations

import time
from typing import Any

from sodamem_struct.aggregate import try_aggregate
from sodamem_struct.classify import route_question
from sodamem_struct.slots import try_slot


def _confidence_floor() -> float:
    import os

    try:
        return float(os.environ.get("SODAMEM_STRUCT_MIN_CONF", "0.55"))
    except ValueError:
        return 0.55


def answer_one_struct(item: dict, api_key: str) -> dict:
    """Same return shape as ``run_s500.answer_one``, with structural overrides."""
    # Local import keeps worker import light and mirrors run_s500 symbols.
    import run_s500 as rs
    from sodamem import SodaMem
    from sodamem.answer import (
        PlannerConfig,
        ReaderConfig,
        assemble_reader_context,
        reader_answer,
        run_planner_loop,
    )
    from sodamem.llm.factory import create_provider
    from sodamem.tools import MemoryTool

    t0 = time.time()
    provider = create_provider(
        provider="openai",
        model=rs.MODEL,
        api_key=api_key,
        base_url=rs.BASE_URL,
    )
    provider._thinking = False

    question = item["question"]
    current_date = item["question_date"]
    route = route_question(question)
    struct_meta: dict[str, Any] = {
        "struct_route": route.kind,
        "struct_template": route.template,
        "struct_override": False,
        "struct_reason": "",
    }

    with SodaMem.open(rs.store_root() / item["user_id"]) as mem:
        chroma_available = mem.store.chroma_available
        tool = MemoryTool(mem, user_id=item["user_id"])
        planner_config = PlannerConfig(
            **rs._supported(
                PlannerConfig,
                max_steps=rs.MAX_STEPS,
                planner_max_tokens=rs.PLANNER_MAX_TOKENS,
                temperature=rs.TEMPERATURE,
                fallback_top_k=rs.FALLBACK_TOP_K,
                abstention_gate=rs.ABSTENTION_GATE,
                count_roster=rs.COUNT_ROSTER,
                time_window=rs.TIME_WINDOW,
                capture_planner_input=rs.CAPTURE_INPUT,
                claim_evidence_autofill=rs.CLAIM_AUTOFILL,
                stall_stop=rs.STALL_STOP,
                truncation_retry=rs.TRUNC_RETRY,
                prompt_cache_layout=rs.CACHE_LAYOUT,
                short_evidence_ids=rs.SHORT_IDS,
                capability_autocall=rs.CAPABILITY_AUTOCALL,
                context_offload=rs.CONTEXT_OFFLOAD,
                stall_dup_threshold=rs.STALL_DUP_THRESHOLD,
                stall_zero_rows_threshold=rs.STALL_ZERO_THRESHOLD,
            )
        )
        reader_config = ReaderConfig(
            **rs._supported(
                ReaderConfig,
                max_tokens=rs.READER_MAX_TOKENS,
                temperature=rs.TEMPERATURE,
                role_timeline=rs.ROLE_TIMELINE,
            )
        )
        loop_result = run_planner_loop(
            question,
            current_date=current_date,
            tools=tool,
            provider=provider,
            config=planner_config,
        )

        override_text = None
        floor = _confidence_floor()

        if route.kind in {"set_count", "set_sum"}:
            agg = try_aggregate(
                question,
                loop_result.evidence,
                current_date=current_date,
                route=route,
            )
            struct_meta.update(
                {
                    "struct_reason": agg.reason,
                    "struct_confidence": agg.confidence,
                    "struct_included_n": len(agg.included),
                    "struct_excluded_n": len(agg.excluded),
                    "struct_value": agg.value,
                }
            )
            if agg.ok and agg.confidence >= floor and agg.answer:
                override_text = agg.answer
                struct_meta["struct_override"] = True
                struct_meta["struct_included"] = agg.included[:20]
                struct_meta["struct_excluded"] = agg.excluded[:20]

        elif route.kind.startswith("slot_"):
            slot = try_slot(
                question,
                loop_result.evidence,
                current_date=current_date,
                route=route,
            )
            struct_meta.update(
                {
                    "struct_reason": slot.reason,
                    "struct_confidence": slot.confidence,
                    "struct_slot_kind": slot.kind,
                }
            )
            # new_speed / role_duration not reliable enough for hard override yet.
            # redeem_points kept (when evidence is found).
            allow_slot = route.kind == "slot_redeem_points"
            if allow_slot and slot.ok and slot.confidence >= floor and slot.answer:
                override_text = slot.answer
                struct_meta["struct_override"] = True
                struct_meta["struct_slot_evidence"] = slot.evidence[:8]
            elif slot.ok:
                struct_meta["struct_reason"] = f"slot_no_override:{slot.reason}"

        if override_text is not None:
            # Skip reader LLM — number/slot already decided in code.
            class _Text:
                text = override_text

            result = _Text()
        else:
            context = assemble_reader_context(
                loop_result.evidence,
                loop_result.selected_evidence_ids,
                question,
                current_date=current_date,
                provider=provider,
                config=reader_config,
                insufficient=loop_result.insufficient,
                missing_information=loop_result.missing_information,
                planner_claims=loop_result.planner_claims,
                planner_conflicts=loop_result.planner_conflicts,
            )
            result = reader_answer(
                question,
                context,
                current_date=current_date,
                provider=provider,
                config=reader_config,
                **rs._supported(
                    reader_answer,
                    answer_bias=rs.ANSWER_BIAS,
                    membership_bias=rs.MEMBERSHIP_BIAS,
                    personalization_bias=rs.PERSONALIZATION_BIAS,
                ),
            )

    usage = provider.usage_summary()
    tools_used = sorted(
        {
            str(obs.get("tool"))
            for row in loop_result.planner_trace
            for obs in (row.get("observations") or [])
            if obs.get("tool")
        }
    )
    out = {
        "hypothesis": result.text,
        "termination": loop_result.termination,
        "planner_steps": len(loop_result.planner_trace),
        "trace": rs.compact_trace(loop_result.planner_trace),
        "_raw_trace": rs.prune_raw_trace(loop_result.planner_trace),
        "selected_evidence": len(loop_result.selected_evidence_ids),
        "chroma_available": chroma_available,
        "unsupported_flags": list(rs._DROPPED_FLAGS),
        "tools_used": tools_used,
        "role_timeline": rs.ROLE_TIMELINE,
        "answer_bias": rs.ANSWER_BIAS,
        "membership_bias": rs.MEMBERSHIP_BIAS,
        "abstention_gate": rs.ABSTENTION_GATE,
        "count_roster": rs.COUNT_ROSTER,
        "time_window": rs.TIME_WINDOW,
        "personalization_bias": rs.PERSONALIZATION_BIAS,
        "capture_planner_input": rs.CAPTURE_INPUT,
        "claim_evidence_autofill": rs.CLAIM_AUTOFILL,
        "stall_stop": rs.STALL_STOP,
        "truncation_retry": rs.TRUNC_RETRY,
        "prompt_cache_layout": rs.CACHE_LAYOUT,
        "short_evidence_ids": rs.SHORT_IDS,
        "capability_autocall": rs.CAPABILITY_AUTOCALL,
        "context_offload": bool(getattr(planner_config, "context_offload", False)),
        "context_offload_requested": rs.CONTEXT_OFFLOAD,
        "stall_dup_threshold": rs.STALL_DUP_THRESHOLD,
        "stall_zero_rows_threshold": rs.STALL_ZERO_THRESHOLD,
        "usage_totals": {
            k: usage.get(k)
            for k in (
                "calls",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cached_input_tokens",
            )
        },
        "served_models": usage.get("served_models") or [],
        "elapsed_s": round(time.time() - t0, 1),
        **struct_meta,
    }
    return out
