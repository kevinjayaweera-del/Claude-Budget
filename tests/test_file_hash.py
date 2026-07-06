import hashlib

from server.file_hash import hash_file


def test_hash_file_matches_known_sha256(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"hello budget tracker")

    expected = hashlib.sha256(b"hello budget tracker").hexdigest()
    assert hash_file(file_path) == expected


def test_hash_file_differs_for_different_content(tmp_path):
    file_a = tmp_path / "a.txt"
    file_b = tmp_path / "b.txt"
    file_a.write_bytes(b"content a")
    file_b.write_bytes(b"content b")

    assert hash_file(file_a) != hash_file(file_b)
