from __future__ import annotations

import argparse

import pytest

from scripts.document_chunking_ablation_run import parse_config


def test_parse_config() -> None:
    assert parse_config("c800_o100:800:100") == (
        "c800_o100",
        800,
        100,
    )


def test_parse_config_rejects_invalid_overlap() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_config("bad:800:800")
