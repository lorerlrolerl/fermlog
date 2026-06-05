"""Ferments and batches — CRUD, lot codes, stage tracking."""
from tests.conftest import make_ferment, make_batch


def test_ferments_list_empty(auth_client):
    resp = auth_client.get("/ferments")
    assert resp.status_code == 200


def test_create_ferment_with_batch(auth_client, db):
    from app.models.ferment import Ferment, Batch
    from app.models.lookup import Category, Status
    db.add_all([Category(name="LAB"), Status(name="active", color="#4ec9b0")])
    db.commit()

    resp = auth_client.post("/ferments/new", data={
        "name": "House Sauerkraut",
        "batch_stage": "1",
        "batch_started_at": "2025-05-01",
    })
    assert resp.status_code in (302, 303)
    f = db.query(Ferment).filter_by(name="House Sauerkraut").first()
    assert f is not None
    assert len(f.batches) == 1


def test_create_ferment_name_required(auth_client):
    resp = auth_client.post("/ferments/new", data={"name": "", "batch_stage": "1"})
    assert resp.status_code == 422


def test_lot_code_auto_generated(auth_client, db):
    from app.models.ferment import Batch
    from app.models.lookup import Category, Status
    db.add_all([Category(name="LAB"), Status(name="active", color="#4ec9b0")])
    db.commit()
    auth_client.post("/ferments/new", data={
        "name": "Red Kraut", "batch_stage": "1", "batch_started_at": "2025-05-16",
    })
    batch = db.query(Batch).first()
    assert batch is not None
    assert batch.lot_code is not None
    assert len(batch.lot_code) > 5


def test_lot_code_custom(auth_client, db):
    from app.models.ferment import Batch
    auth_client.post("/ferments/new", data={
        "name": "Kombucha", "batch_stage": "1", "batch_lot_code": "MY-CUSTOM-001",
    })
    assert db.query(Batch).filter_by(lot_code="MY-CUSTOM-001").first() is not None


def test_lot_code_uniqueness_enforced(auth_client):
    auth_client.post("/ferments/new", data={
        "name": "First", "batch_stage": "1", "batch_lot_code": "DUPE-001",
    })
    resp = auth_client.post("/ferments/new", data={
        "name": "Second", "batch_stage": "1", "batch_lot_code": "DUPE-001",
    })
    assert resp.status_code == 422


def test_ferment_detail_page(auth_client, db):
    f = make_ferment(db, "Detail Test")
    make_batch(db, f.id, lot_code="DT-001")
    db.commit()
    resp = auth_client.get(f"/ferments/{f.id}")
    assert resp.status_code == 200
    assert b"Detail Test" in resp.content


def test_ferment_404(auth_client):
    resp = auth_client.get("/ferments/99999")
    assert resp.status_code == 404


def test_ferment_edit(auth_client, db):
    f = make_ferment(db, "Old Name")
    db.commit()
    resp = auth_client.post(f"/ferments/{f.id}/edit", data={"name": "New Name"})
    assert resp.status_code in (302, 303)
    db.expire(f)
    assert f.name == "New Name"


def test_new_batch_increments_number(auth_client, db):
    from app.models.ferment import Batch
    f = make_ferment(db, "Batch Counter")
    make_batch(db, f.id, lot_code="BC-001", batch_number=1)
    db.commit()

    resp = auth_client.post(f"/ferments/{f.id}/batches/new", data={
        "stage": "2", "lot_code": "BC-002", "started_at": "2025-05-16",
    })
    assert resp.status_code in (302, 303)
    assert db.query(Batch).filter_by(ferment_id=f.id).count() == 2


def test_child_batch_inherits_parent_batch_number(auth_client, db):
    """S2 child of B1 must keep batch_number=1, not get batch_number=2."""
    from app.models.ferment import Batch
    f = make_ferment(db, "Stage Inherit")
    root = make_batch(db, f.id, lot_code="SI-001", batch_number=1)
    db.commit()

    resp = auth_client.post(f"/ferments/{f.id}/batches/new", data={
        "stage": "2",
        "parent_batch_id": str(root.id),
        "lot_code": "SI-002",
        "started_at": "2025-05-20",
    })
    assert resp.status_code in (302, 303)
    child = db.query(Batch).filter_by(ferment_id=f.id, stage=2).first()
    assert child is not None
    assert child.batch_number == 1, "child stage must inherit parent's batch_number"


def test_independent_second_brew_gets_new_batch_number(auth_client, db):
    """A new S1 batch with no parent is a fresh brew run — gets batch_number=2."""
    from app.models.ferment import Batch
    f = make_ferment(db, "Second Brew")
    make_batch(db, f.id, lot_code="SB-001", batch_number=1)
    db.commit()

    resp = auth_client.post(f"/ferments/{f.id}/batches/new", data={
        "stage": "1",
        "lot_code": "SB-002",
        "started_at": "2025-06-01",
    })
    assert resp.status_code in (302, 303)
    new_batch = db.query(Batch).filter_by(ferment_id=f.id, lot_code="SB-002").first()
    assert new_batch is not None
    assert new_batch.batch_number == 2, "independent new brew must get batch_number=2"


def test_batch_detail_page(auth_client, db):
    f = make_ferment(db, "Batch Detail")
    b = make_batch(db, f.id, lot_code="BD-001")
    db.commit()
    resp = auth_client.get(f"/ferments/{f.id}/batches/{b.id}")
    assert resp.status_code == 200
    assert b"BD-001" in resp.content


def test_add_ingredient_to_batch(auth_client, db):
    from app.models.ferment import BatchIngredient
    from tests.conftest import make_ingredient
    f = make_ferment(db, "SKR")
    b = make_batch(db, f.id, lot_code="SKR-001")
    ing = make_ingredient(db, "White Cabbage")
    db.commit()

    resp = auth_client.post(
        f"/ferments/{f.id}/batches/{b.id}/ingredients/add",
        data={"ingredient_id": str(ing.id), "quantity": "500"},
    )
    assert resp.status_code in (302, 303)
    bi = db.query(BatchIngredient).filter_by(batch_id=b.id, ingredient_id=ing.id).first()
    assert bi is not None
    assert bi.quantity == 500


def test_remove_ingredient_from_batch(auth_client, db):
    from app.models.ferment import BatchIngredient
    from tests.conftest import make_ingredient
    f = make_ferment(db, "SKR2")
    b = make_batch(db, f.id, lot_code="SKR2-001")
    ing = make_ingredient(db, "Red Cabbage")
    db.add(BatchIngredient(batch_id=b.id, ingredient_id=ing.id, quantity=300))
    db.commit()

    resp = auth_client.post(f"/ferments/{f.id}/batches/{b.id}/ingredients/{ing.id}/remove")
    assert resp.status_code in (302, 303)
    assert db.query(BatchIngredient).filter_by(batch_id=b.id, ingredient_id=ing.id).first() is None