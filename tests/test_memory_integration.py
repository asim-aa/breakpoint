"""Exercises the REAL sentence-transformer model — separate from
test_memory.py so the fast, network-free suite isn't affected. Needs
network access on first run (to download the model, ~90MB, cached after
that) and takes several seconds even when cached, since loading torch and
the model is not instant. Skips cleanly if the model can't be loaded
(e.g. no network on a fresh machine) rather than failing the whole run."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import memory


@pytest.fixture(scope="module")
def real_embed():
    try:
        memory.embed("warm up")
    except Exception as e:
        pytest.skip(f"Could not load the embedding model (likely no network): {e}")
    return memory.embed


def test_real_embedding_has_expected_dimensionality(real_embed):
    vector = real_embed("none input: TypeError")
    assert len(vector) == 384  # all-MiniLM-L6-v2's output size


def test_real_embedding_clusters_similar_bugs_above_threshold(real_embed):
    v1 = real_embed("none input: TypeError: 'NoneType' object is not iterable")
    v2 = real_embed("empty list input: TypeError: 'NoneType' object is not iterable")
    similarity = memory._cosine(v1, v2)
    assert similarity >= memory.SIMILARITY_THRESHOLD


def test_real_embedding_separates_dissimilar_bugs_below_threshold(real_embed):
    v1 = real_embed("none input: TypeError: 'NoneType' object is not iterable")
    v2 = real_embed("reversed word order preserving whitespace: AssertionError mismatch")
    similarity = memory._cosine(v1, v2)
    assert similarity < memory.SIMILARITY_THRESHOLD


def test_record_bug_end_to_end_with_real_model(tmp_path, real_embed):
    db_path = str(tmp_path / "test_breakpoint.db")

    id1 = memory.record_bug(
        spec_id=1,
        test_code="def test_none_input():\n    assert f(None) == []",
        error="TypeError: 'NoneType' object is not iterable",
        db_path=db_path,
    )
    id2 = memory.record_bug(
        spec_id=2,
        test_code="def test_empty_input():\n    assert f(None) == []",
        error="TypeError: 'NoneType' object is not iterable",
        db_path=db_path,
    )
    id3 = memory.record_bug(
        spec_id=3,
        test_code="def test_reversed_words():\n    assert reverse('a b') == 'b a'",
        error="AssertionError: got 'a  b'",
        db_path=db_path,
    )

    assert id1 == id2  # same underlying bug, different spec -> same cluster
    assert id3 != id1  # genuinely different bug -> new cluster

    patterns = memory.list_patterns(db_path=db_path)
    assert len(patterns) == 2
