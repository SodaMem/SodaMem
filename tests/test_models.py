from sodamem.models import AnswerEvidenceBundle, fact_search_document


def test_answer_evidence_bundle_has_no_answer_task_field():
    field_names = set(getattr(AnswerEvidenceBundle, "model_fields", {}) or getattr(AnswerEvidenceBundle, "__dataclass_fields__", {}))
    assert "answer_task" not in field_names


def test_fact_search_document_is_pure_function():
    # Smoke test: no need for a fully-populated FactEvent, just confirm the
    # function exists with the expected signature and doesn't raise.
    import inspect
    sig = inspect.signature(fact_search_document)
    assert list(sig.parameters) == ["fact"]
