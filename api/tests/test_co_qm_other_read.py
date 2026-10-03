"""其他报检单（qm_other_inspect）、其他检验单（qm_other_check）：可读取、列表、查找，不接审批流。"""

from __future__ import annotations

from typing import get_args

import pytest
from u8co_api.co_models import VoucherType
from u8co_api.co_models_wf import WfType

_KINDS = ("qm_other_inspect", "qm_other_check")


@pytest.mark.parametrize("kind", _KINDS)
def test_readable_without_workflow(kind: str) -> None:
    assert kind in get_args(VoucherType)
    assert kind not in get_args(WfType)
