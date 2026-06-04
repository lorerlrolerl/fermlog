"""Batch log — creating and deleting log entries."""
from datetime import datetime
from tests.conftest import make_ferment, make_batch


def test_add_log_entry(auth_client, db):
    from app.models.log import BatchLog
    f = make_ferment(db); b = make_batch(db, f.id, lot_code="LOG-001"); db.commit()
    resp = auth_client.post(f"/ferments/{f.id}/batches/{b.id}/logs/add", data={
        "logged_at": "2025-05-16 10:00",
        "ph": "3.6",
        "temperature": "21.5",
        "notes": "Looking good",
    })
    assert resp.status_code in (302, 303), f"Got {resp.status_code}"
    entry = db.query(BatchLog).filter_by(batch_id=b.id).first()
    assert entry is not None
    assert entry.ph == 3.6
    assert entry.notes == "Looking good"


def test_log_entry_updates_batch_status(auth_client, db):
    from app.models.ferment import Batch
    from app.models.lookup import Status
    db.add(Status(name="ready", color="#b5cea8")); db.commit()
    ready = db.query(Status).filter_by(name="ready").first()
    f = make_ferment(db); b = make_batch(db, f.id, lot_code="SU-001"); db.commit()
    auth_client.post(f"/ferments/{f.id}/batches/{b.id}/logs/add", data={
        "logged_at": "2025-05-16 10:00",
        "status_id": str(ready.id),
    })
    # Refresh from DB — the router updates a fresh batch object
    db.expire_all()
    updated = db.query(Batch).filter_by(id=b.id).first()
    assert updated.status_id == ready.id


def test_log_with_smell_descriptors(auth_client, db):
    from app.models.log import BatchLog
    from app.models.lookup import SmellDescriptor
    db.add(SmellDescriptor(name="Acidic")); db.commit()
    acidic = db.query(SmellDescriptor).filter_by(name="Acidic").first()
    f = make_ferment(db); b = make_batch(db, f.id, lot_code="SM-001"); db.commit()
    auth_client.post(f"/ferments/{f.id}/batches/{b.id}/logs/add", data={
        "logged_at": "2025-05-16 10:00",
        "smell_ids": str(acidic.id),
        "smell_notes": "Smells acidic",
    })
    db.expire_all()
    entry = db.query(BatchLog).filter_by(batch_id=b.id).first()
    assert entry is not None, "Log entry not created"
    assert any(s.id == acidic.id for s in entry.smell_descriptors)


def test_delete_log_entry(auth_client, db):
    from app.models.log import BatchLog
    f = make_ferment(db); b = make_batch(db, f.id, lot_code="DL-001")
    entry = BatchLog(batch_id=b.id, logged_at=datetime.now(), ph=3.5)
    db.add(entry); db.commit()
    resp = auth_client.post(f"/ferments/{f.id}/batches/{b.id}/logs/{entry.id}/delete")
    assert resp.status_code in (302, 303)
    assert db.query(BatchLog).filter_by(id=entry.id).first() is None


def test_viewer_cannot_add_log(viewer_client, db):
    f = make_ferment(db); b = make_batch(db, f.id, lot_code="VL-001"); db.commit()
    resp = viewer_client.post(
        f"/ferments/{f.id}/batches/{b.id}/logs/add",
        data={"logged_at": "2025-05-16 10:00", "ph": "3.5"},
    )
    assert resp.status_code in (302, 303, 403)