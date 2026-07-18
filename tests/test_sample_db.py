import sqlite3

from app import db as db_module


def create_marker_db(path, value: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
    connection.execute("INSERT INTO marker (value) VALUES (?)", (value,))
    connection.commit()
    connection.close()


def read_marker(path) -> str:
    connection = sqlite3.connect(path)
    value = connection.execute("SELECT value FROM marker").fetchone()[0]
    connection.close()
    return value


def test_copies_sample_when_default_db_is_missing(tmp_path, monkeypatch):
    default_db = tmp_path / "data" / "youtube_scripts.db"
    sample_db = tmp_path / "sample_data" / "youtube_scripts.db"
    sample_db.parent.mkdir()
    create_marker_db(sample_db, "sample")

    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", str(default_db))
    monkeypatch.setattr(db_module, "DB_PATH", str(default_db))
    monkeypatch.setattr(db_module, "SAMPLE_DB_PATH", str(sample_db))

    assert db_module.copy_sample_db_if_missing() is True
    assert read_marker(default_db) == "sample"


def test_does_not_overwrite_existing_default_db(tmp_path, monkeypatch):
    default_db = tmp_path / "data" / "youtube_scripts.db"
    sample_db = tmp_path / "sample_data" / "youtube_scripts.db"
    default_db.parent.mkdir()
    sample_db.parent.mkdir()
    create_marker_db(default_db, "existing")
    create_marker_db(sample_db, "sample")

    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", str(default_db))
    monkeypatch.setattr(db_module, "DB_PATH", str(default_db))
    monkeypatch.setattr(db_module, "SAMPLE_DB_PATH", str(sample_db))

    assert db_module.copy_sample_db_if_missing() is False
    assert read_marker(default_db) == "existing"


def test_does_not_seed_custom_db_path(tmp_path, monkeypatch):
    default_db = tmp_path / "data" / "youtube_scripts.db"
    custom_db = tmp_path / "data" / "custom.db"
    sample_db = tmp_path / "sample_data" / "youtube_scripts.db"
    sample_db.parent.mkdir()
    create_marker_db(sample_db, "sample")

    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", str(default_db))
    monkeypatch.setattr(db_module, "DB_PATH", str(custom_db))
    monkeypatch.setattr(db_module, "SAMPLE_DB_PATH", str(sample_db))

    assert db_module.copy_sample_db_if_missing() is False
    assert not custom_db.exists()
