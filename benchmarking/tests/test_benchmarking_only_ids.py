"""--only 子集加载 (0731 稳定集评法)。

空文件必须报错而不是"没有过滤条件 = 跑全部": --only 的整个用途是把一次
跑限制在一个子集上, 所以一个读不出 id 的文件是操作失误, 而"静默跑全部
500 道"会在计费之后才被发现。

(本文件原先还覆盖 reader-votes 臂的两个纯函数。该臂 0806 随实现一并删除
—— 每次计分跑都是关闭的; 证伪记录见 benchmarking/README.md。)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# A base `pip install sodamem` has no [llm] extra, and CI's
# gate-i1-base-deps job collects this whole tree under exactly that
# install. Skipping is the contract; exploding at import is what the
# gate exists to catch.
pytest.importorskip("openai", reason="the benchmark harness imports the OpenAI SDK at module level; it lives behind the [llm] extra")

from run_s500 import load_only_ids  # noqa: E402


def test_only_ids_json_list(tmp_path):
    p = tmp_path / "ids.json"
    p.write_text('["q193", "q053"]')
    assert load_only_ids(str(p)) == {"q193", "q053"}


def test_only_ids_lines(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("q193\nq053\n\n")
    assert load_only_ids(str(p)) == {"q193", "q053"}


def test_only_ids_empty_file_refuses_to_run_everything(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("\n")
    with pytest.raises(SystemExit):
        load_only_ids(str(p))
