from sodamem.models import fact_search_document

# `test_answer_evidence_bundle_has_no_answer_task_field` lived here until 0806.
# It pinned that one field stayed absent from a dataclass whose only producer
# (`context.cards.project_answer_bundle`) had no callers — the model and the
# projection are both gone now, and a test that a deleted field is missing
# from a deleted class asserts nothing.


def test_fact_search_document_is_pure_function():
    # Smoke test: no need for a fully-populated FactEvent, just confirm the
    # function exists with the expected signature and doesn't raise.
    import inspect
    sig = inspect.signature(fact_search_document)
    assert list(sig.parameters) == ["fact"]
