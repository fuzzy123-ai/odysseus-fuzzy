import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tests.helpers.import_state import clear_fake_database_modules

clear_fake_database_modules()

from core.database import Base, Session, ChatMessage
from datetime import datetime

def test_sqlite_foreign_keys_cascade():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    
    TestSessionLocal = sessionmaker(bind=engine)
    db = TestSessionLocal()
    
    session_id = "test-session-123"
    s = Session(
        id=session_id,
        name="Test Session",
        endpoint_url="http://localhost:8000",
        model="gpt-4",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    m = ChatMessage(id="test-msg-123", session_id=session_id, role="user", content="test message")
    
    db.add(s)
    db.add(m)
    db.commit()
    
    assert db.query(Session).count() == 1
    assert db.query(ChatMessage).count() == 1
    
    db.query(Session).filter(Session.id == session_id).delete()
    db.commit()
    
    assert db.query(ChatMessage).count() == 0
    
    db.close()


def test_sqlite_phase1_pragmas_for_file_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'phase1.db'}",
        connect_args={"check_same_thread": False},
    )

    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
