import math
import struct
import zlib

import pytest
from grimoire.store import vectors


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def test_unit_scales_to_length_one_and_keeps_direction():
    out = vectors.unit([3.0, 4.0])
    assert out is not None
    assert math.isclose(math.sqrt(sum(c * c for c in out)), 1.0, rel_tol=1e-9)
    assert math.isclose(out[0] / out[1], 3 / 4, rel_tol=1e-9)


@pytest.mark.parametrize("vec", [[], [0.0, 0.0], [float("nan"), 1.0], [float("inf"), 1.0]])
def test_unit_rejects_what_has_no_direction(vec):
    # A zero vector has no direction to normalize, and a non-finite component
    # makes every similarity nan — which compares False against any threshold,
    # so it would look like "not relevant" rather than "broken".
    assert vectors.unit(vec) is None


def test_dot_of_a_unit_vector_with_itself_is_one():
    v = vectors.unit([1.0, 2.0, 3.0])
    assert math.isclose(vectors.dot(v, v), 1.0, rel_tol=1e-9)


def test_dot_of_orthogonal_units_is_zero():
    assert math.isclose(vectors.dot([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-12)


def test_save_then_load_round_trips_as_a_unit_vector():
    vectors.save("m", "the salt marsh", [3.0, 4.0])
    got = vectors.load("m", ["the salt marsh"])
    assert math.isclose(got["the salt marsh"][0], 0.6, rel_tol=1e-5)
    assert math.isclose(got["the salt marsh"][1], 0.8, rel_tol=1e-5)


def test_load_omits_texts_that_were_never_saved():
    vectors.save("m", "known", [1.0, 0.0])
    assert list(vectors.load("m", ["known", "unknown"])) == ["known"]


def test_the_cache_is_keyed_by_the_embedding_space_as_well_as_the_text():
    # The space is endpoint AND model: two endpoints can both serve a model
    # called "embedding" and mean different weights, and their vectors are not
    # comparable even at matching dimensionality.
    vectors.save("https://a/v1\0embed-1", "same text", [1.0, 0.0])
    assert vectors.load("https://b/v1\0embed-1", ["same text"]) == {}
    assert vectors.load("https://a/v1\0embed-2", ["same text"]) == {}
    assert vectors.load("https://a/v1\0embed-1", ["same text"]) != {}


def test_forget_drops_a_cached_vector():
    vectors.save("m", "t", [1.0, 0.0])
    vectors.forget("m", "t")
    assert vectors.load("m", ["t"]) == {}


def test_forgetting_what_was_never_cached_is_not_an_error():
    vectors.forget("m", "never seen")


def test_an_oversized_record_is_a_miss_and_is_never_read(store, monkeypatch):
    # `read_bytes` allocates the whole file before any length or checksum check
    # could reject it, so a mis-synced multi-gigabyte record failed the context
    # build instead of degrading to the miss every other bad file degrades to.
    vectors.save("m", "t", [1.0, 0.0])
    path = next((store / ".cache" / "embeddings").glob("*.vec"))
    path.write_bytes(b"\0" * (vectors.MAX_RECORD + 1))
    monkeypatch.setattr(vectors.Path, "read_bytes",
                        lambda *a, **k: pytest.fail("oversized record must not be read"))
    assert vectors.load("m", ["t"]) == {}


def test_a_zero_vector_is_never_cached():
    # Nothing to normalize, so there is nothing worth storing; the caller must
    # see a miss rather than a stored vector that scores 0 against everything.
    vectors.save("m", "t", [0.0, 0.0])
    assert vectors.load("m", ["t"]) == {}


def test_the_cache_lives_under_the_derived_data_directory(store):
    vectors.save("m", "t", [1.0, 0.0])
    written = list((store / ".cache" / "embeddings").glob("*.vec"))
    assert len(written) == 1


def test_saving_the_same_text_twice_writes_one_entry(store):
    vectors.save("m", "t", [1.0, 0.0])
    vectors.save("m", "t", [1.0, 0.0])
    assert len(list((store / ".cache" / "embeddings").glob("*.vec"))) == 1


@pytest.mark.parametrize("content", [b"", b"abc", b"\x00" * 9])
def test_a_truncated_cache_entry_reads_as_a_miss(store, content):
    # The cache is derived data in a folder the user may sync, truncate, or
    # delete under the app. A file that is not a whole number of components is
    # a partial write; it must cost a re-embed, never a failed scene.
    vectors.save("m", "t", [1.0, 0.0])
    path = next((store / ".cache" / "embeddings").glob("*.vec"))
    path.write_bytes(content)
    assert vectors.load("m", ["t"]) == {}


def test_an_unreadable_cache_directory_reads_as_a_miss(store, monkeypatch):
    vectors.save("m", "t", [1.0, 0.0])
    monkeypatch.setattr(vectors.Path, "read_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert vectors.load("m", ["t"]) == {}


def test_saving_into_an_unwritable_store_is_not_an_error(monkeypatch):
    # Losing a cache write costs one re-embed next turn. Raising here would
    # cost the scene.
    monkeypatch.setattr(vectors.atomic, "write_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    vectors.save("m", "t", [1.0, 0.0])
    assert vectors.load("m", ["t"]) == {}


def test_stored_bytes_are_a_crc_then_little_endian_float32_of_a_unit_vector(store):
    # Spelled out here because a store is synced between machines: a vector
    # written on one and read back byte-swapped on another scores as noise,
    # silently.
    vectors.save("m", "t", [1.0, 1.0, 1.0])
    raw = next((store / ".cache" / "embeddings").glob("*.vec")).read_bytes()
    assert len(raw) == 4 + 3 * 4
    assert struct.unpack("<I", raw[:4])[0] == zlib.crc32(raw[4:])
    got = struct.unpack("<3f", raw[4:])
    assert math.isclose(math.sqrt(sum(c * c for c in got)), 1.0, rel_tol=1e-6)


@pytest.mark.parametrize("vec", [[1e20, 0.0], [0.9, 0.9], [float("inf"), 0.0]])
def test_a_record_corrupted_in_place_reads_as_a_miss(store, vec):
    # The case a length check cannot see: the file is the right size and
    # unpacks cleanly, it is just no longer the vector that was written.
    # `[0.9, 0.9]` is the one that matters most -- norm 1.27, so it projects
    # ABOVE any honest cosine while still landing inside [-1, 1], which is why
    # range-checking the score is not an integrity check.
    vectors.save("m", "t", [1.0, 0.0])
    path = next((store / ".cache" / "embeddings").glob("*.vec"))
    path.write_bytes(path.read_bytes()[:4] + struct.pack("<2f", *vec))
    assert vectors.load("m", ["t"]) == {}


def test_a_record_written_before_the_checksum_reads_as_a_miss(store):
    # No migration: an old record fails the CRC and costs one re-embed.
    vectors.save("m", "t", [1.0, 0.0])
    path = next((store / ".cache" / "embeddings").glob("*.vec"))
    path.write_bytes(struct.pack("<2f", 1.0, 0.0))      # the pre-CRC layout
    assert vectors.load("m", ["t"]) == {}


def test_a_vector_survives_the_round_trip_precisely_enough_to_rank(store):
    # float32 moves a cosine by ~1e-9 -- far below any threshold worth setting.
    v = [0.1234567, -0.7654321, 0.5555555]
    vectors.save("m", "t", v)
    exact, got = vectors.unit(v), vectors.load("m", ["t"])["t"]
    assert math.isclose(vectors.dot(exact, got), 1.0, rel_tol=1e-6)


def test_duplicate_texts_in_one_load_are_looked_up_once(store):
    vectors.save("m", "t", [1.0, 0.0])
    got = vectors.load("m", ["t", "t"])
    assert list(got) == ["t"]
