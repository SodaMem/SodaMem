"""`[Evidence metadata: ... label=X ...]` 里的 label 有时就是正文本身。

原始 turn 行没有 predicate_raw, 于是 `reader_evidence` 退回 `row["label"]`,
而检索层给原始 turn 的 label 就是该 turn 内容的截断副本。结果是每条这样的
证据行把自己的前 ~400-500 字符在同一行里发两遍。

实测 (0731 现场捕获两道题的完整 reader prompt): q001 冗余 1,137 字符
= prompt 的 13.2% (3/7 行), q193 4,793 字符 = 8.8% (11/34 行)。

删除条件严格: 只在 label 归一化后确实被正文包含时才删。predicate 型
label ("owns_korg_b1_digital_piano") 是抽取结论、不在正文里, 必须留 ——
它是 reader 判断这条证据讲的是什么的唯一线索。
"""
from __future__ import annotations

from sodamem.answer.reader import _content_with_evidence_metadata


def test_label_that_is_the_content_itself_is_dropped():
    content = (
        "Yes, here are some highly recommended authentic Italian restaurants in "
        "Rome:\n\n1. Trattoria Da Enzo al 29 - a cozy traditional trattoria in "
        "Trastevere.\n2. Roscioli - a delicatessen near the Campo dei Fiori market."
    )
    # Retrieval's label for a raw turn is that turn's own text, truncated —
    # 400-500 characters in the live captures.
    ev = {"evidence_id": "ev_raw:session_49_turn_1", "label": content[:120],
          "source_type": "raw_message"}

    out = _content_with_evidence_metadata(ev, content)

    assert "label=" not in out
    assert "evidence_id=ev_raw:session_49_turn_1" in out
    assert "source_type=raw_message" in out
    assert out.endswith(content)


def test_label_survives_whitespace_differences_between_copy_and_body():
    """截断副本常带被折叠的换行 —— 归一化后仍算重复。"""
    content = "I've had my black Fender Stratocaster   for about 5 years now."
    ev = {"evidence_id": "ev_raw:t1", "label": "I've had my black Fender Stratocaster for about"}

    assert "label=" not in _content_with_evidence_metadata(ev, content)


def test_predicate_label_is_kept_because_it_is_not_in_the_body():
    """抽取出来的谓词是结论, 不是正文的复读 —— 删了 reader 就失去线索。"""
    content = "I'm looking to find a piano technician to service my Korg B1."
    ev = {"evidence_id": "ev_fact:fact_3d75", "label": "owns_korg_b1_digital_piano",
          "source_type": "explicit_text"}

    out = _content_with_evidence_metadata(ev, content)

    assert "label=owns_korg_b1_digital_piano" in out


def test_a_short_label_is_kept_even_when_it_appears_in_the_body():
    """短 label 省不下什么, 而误删会拿走一条真实注解 —— 门槛只对长副本开。"""
    content = "I bought a road bike last spring and ride it to work."
    ev = {"evidence_id": "ev_fact:f1", "label": "road bike"}

    assert "label=road bike" in _content_with_evidence_metadata(ev, content)


def test_rows_with_no_metadata_at_all_are_returned_unchanged():
    assert _content_with_evidence_metadata({}, "plain text") == "plain text"
