import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.auth import require_user, require_editor
from app.database import get_db
from app.lot_code import generate_lot_code, next_batch_number
from app.models.ferment import Batch, BatchAdditive, BatchIngredient, Container, Ferment
from app.models.ingredient import Ingredient
from app.models.additive import Additive
from app.models.lookup import Category, Status, VesselMaterial, VesselType, SmellDescriptor, VisualDescriptor
from app.models.log import BatchLog
from app.models.user import User
from app.templates import templates

router = APIRouter(prefix="/ferments/{ferment_id}/batches")


def _get_ferment_or_404(ferment_id: int, db: Session):
    return db.query(Ferment).filter(Ferment.id == ferment_id).first()


def _sync_ferment_status(db: Session, ferment_id: int) -> None:
    """Set ferment.status_id to the status of the latest batch (by started_at)."""
    latest = (
        db.query(Batch)
        .filter(Batch.ferment_id == ferment_id, Batch.status_id.isnot(None))
        .order_by(Batch.started_at.desc().nullslast(), Batch.batch_number.desc())
        .first()
    )
    if latest:
        ferment = _get_ferment_or_404(ferment_id, db)
        if ferment:
            ferment.status_id = latest.status_id


def _get_batch_or_404(batch_id: int, ferment_id: int, db: Session):
    return (
        db.query(Batch)
        .filter(Batch.id == batch_id, Batch.ferment_id == ferment_id)
        .options(
            joinedload(Batch.status),
            joinedload(Batch.ingredients).joinedload(BatchIngredient.ingredient),
            joinedload(Batch.additives).joinedload(BatchAdditive.additive),
            joinedload(Batch.containers).joinedload(Container.vessel_type),
            joinedload(Batch.containers).joinedload(Container.vessel_material),
            joinedload(Batch.containers).joinedload(Container.status),
            joinedload(Batch.parent_batch),
            joinedload(Batch.created_by_user),
        )
        .first()
    )


# ── New batch — MUST be before /{batch_id} ────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def batch_new(
    ferment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    ferment = _get_ferment_or_404(ferment_id, db)
    if not ferment:
        return RedirectResponse("/ferments", status_code=status.HTTP_302_FOUND)

    existing_batches = db.query(Batch).filter(Batch.ferment_id == ferment_id).all()
    next_num   = next_batch_number(existing_batches)
    next_stage = max((b.stage for b in existing_batches), default=0) + 1
    cat_name   = ferment.category.name if ferment.category else None
    suggested_lot = generate_lot_code(ferment.name, cat_name, datetime.now(), next_stage, next_num)
    parent_options = sorted(existing_batches, key=lambda b: b.batch_number)

    return templates.TemplateResponse(
        request,
        "batches/new.html",
        {
            "current_user": current_user,
            "ferment": ferment,
            "suggested_lot": suggested_lot,
            "next_num": next_num,
            "next_stage": next_stage,
            "parent_options": parent_options,
            "statuses": db.query(Status).order_by(Status.name).all(),
            "today": datetime.now().strftime("%Y-%m-%d"),
            "errors": {},
        },
    )


@router.post("/new")
def batch_create(
    ferment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    stage: int = Form(1),
    parent_batch_id: Optional[int] = Form(None),
    status_id: Optional[int] = Form(None),
    started_at: Optional[str] = Form(None),
    target_ready_at: Optional[str] = Form(None),
    target_ph: Optional[float] = Form(None),
    lot_code: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    ferment = _get_ferment_or_404(ferment_id, db)
    if not ferment:
        return RedirectResponse("/ferments", status_code=status.HTTP_302_FOUND)

    errors = {}
    if lot_code and lot_code.strip():
        existing = db.query(Batch).filter(Batch.lot_code == lot_code.strip()).first()
        if existing:
            errors["lot_code"] = f"Lot code '{lot_code.strip()}' already in use."

    existing_batches = db.query(Batch).filter(Batch.ferment_id == ferment_id).all()

    if errors:
        return templates.TemplateResponse(
            request,
            "batches/new.html",
            {
                "current_user": current_user,
                "ferment": ferment,
                "suggested_lot": lot_code or "",
                "next_num": next_batch_number(existing_batches),
                "next_stage": stage,
                "parent_options": sorted(existing_batches, key=lambda b: b.batch_number),
                "statuses": db.query(Status).order_by(Status.name).all(),
                "today": datetime.now().strftime("%Y-%m-%d"),
                "errors": errors,
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    def parse_date(val):
        if not val:
            return None
        try:
            return datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            return None

    if parent_batch_id:
        parent_b = next((b for b in existing_batches if b.id == parent_batch_id), None)
        batch_num = parent_b.batch_number if parent_b else next_batch_number(existing_batches)
    else:
        batch_num = next_batch_number(existing_batches)
    cat_name  = ferment.category.name if ferment.category else None
    started   = parse_date(started_at) or datetime.now(timezone.utc).replace(tzinfo=None)
    final_lot = (
        lot_code.strip() if lot_code and lot_code.strip()
        else generate_lot_code(ferment.name, cat_name, started, stage, batch_num)
    )

    batch = Batch(
        ferment_id=ferment_id,
        batch_number=batch_num,
        lot_code=final_lot,
        stage=stage,
        parent_batch_id=parent_batch_id or None,
        status_id=status_id,
        started_at=started,
        target_ready_at=parse_date(target_ready_at),
        target_ph=target_ph,
        notes=notes or None,
        created_by_id=current_user.id,
    )
    db.add(batch)
    db.commit()

    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Batch detail ───────────────────────────────────────────────────────────

@router.get("/{batch_id}", response_class=HTMLResponse)
def batch_detail(
    ferment_id: int,
    batch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    ferment = _get_ferment_or_404(ferment_id, db)
    if not ferment:
        return RedirectResponse("/ferments", status_code=status.HTTP_302_FOUND)

    batch = _get_batch_or_404(batch_id, ferment_id, db)
    if not batch:
        return RedirectResponse(f"/ferments/{ferment_id}", status_code=status.HTTP_302_FOUND)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    used_ingredient_ids = {bi.ingredient_id for bi in batch.ingredients}
    used_additive_ids   = {ba.additive_id for ba in batch.additives}

    available_ingredients = (
        db.query(Ingredient)
        .filter(Ingredient.is_active == True, ~Ingredient.id.in_(used_ingredient_ids))
        .options(joinedload(Ingredient.cut_size))
        .order_by(Ingredient.name)
        .all()
    )
    from sqlalchemy.orm import joinedload as jload
    available_additives = (
        db.query(Additive)
        .options(jload(Additive.type_obj))
        .filter(~Additive.id.in_(used_additive_ids))
        .order_by(Additive.name)
        .all()
    )

    logs = (
        db.query(BatchLog)
        .filter(BatchLog.batch_id == batch_id)
        .options(
            joinedload(BatchLog.logged_by),
            joinedload(BatchLog.status),
            joinedload(BatchLog.smell_descriptors),
            joinedload(BatchLog.visual_descriptors),
        )
        .order_by(BatchLog.logged_at.desc())
        .all()
    )

    # Age ends when the batch was last logged as non-active; still-active batches use now
    active_status = db.query(Status).filter(Status.name.ilike("active")).first()
    active_id = active_status.id if active_status else None
    age_end = now
    if batch.status_id != active_id:
        for log in logs:  # already ordered desc
            if log.status_id is not None and log.status_id != active_id:
                age_end = log.logged_at
                break

    active_tab = request.query_params.get("tab", "overview")

    ingredients_json = json.dumps([
        {
            "id": bi.ingredient_id,
            "name": bi.ingredient.name,
            "quantity": float(bi.quantity) if bi.quantity else 0,
            "kind": "ingredient",
        }
        for bi in batch.ingredients
    ])

    additives_json = json.dumps([
        {
            "id": ba.additive_id,
            "name": ba.additive.name,
            "quantity": float(ba.quantity) if ba.quantity else 0,
            "kind": "additive",
        }
        for ba in batch.additives
    ])

    return templates.TemplateResponse(
        request,
        "batches/detail.html",
        {
            "current_user": current_user,
            "ferment": ferment,
            "batch": batch,
            "now": now,
            "age_end": age_end,
            "available_ingredients": available_ingredients,
            "available_additives": available_additives,
            "vessel_types": db.query(VesselType).order_by(VesselType.name).all(),
            "vessel_materials": db.query(VesselMaterial).order_by(VesselMaterial.name).all(),
            "statuses": db.query(Status).order_by(Status.name).all(),
            "smell_descriptors": db.query(SmellDescriptor).order_by(SmellDescriptor.name).all(),
            "visual_descriptors": db.query(VisualDescriptor).order_by(VisualDescriptor.name).all(),
            "logs": logs,
            "active_tab": active_tab,
            "ingredients_json": ingredients_json,
            "additives_json": additives_json,
        },
    )


# ── Add / remove ingredient ────────────────────────────────────────────────

@router.post("/{batch_id}/ingredients/add")
def batch_add_ingredient(
    ferment_id: int,
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    ingredient_id: int = Form(...),
    quantity: Optional[float] = Form(None),
    unit: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    existing = db.query(BatchIngredient).filter(
        BatchIngredient.batch_id == batch_id,
        BatchIngredient.ingredient_id == ingredient_id,
    ).first()
    if not existing:
        db.add(BatchIngredient(
            batch_id=batch_id,
            ingredient_id=ingredient_id,
            quantity=quantity,
            unit=unit.strip() if unit else None,
            notes=notes or None,
        ))
        db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{batch_id}/ingredients/{ingredient_id}/remove")
def batch_remove_ingredient(
    ferment_id: int,
    batch_id: int,
    ingredient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    db.query(BatchIngredient).filter(
        BatchIngredient.batch_id == batch_id,
        BatchIngredient.ingredient_id == ingredient_id,
    ).delete()
    db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Add / remove additive ──────────────────────────────────────────────────

@router.post("/{batch_id}/additives/add")
def batch_add_additive(
    ferment_id: int,
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    additive_id: int = Form(...),
    quantity: Optional[float] = Form(None),
    unit: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    existing = db.query(BatchAdditive).filter(
        BatchAdditive.batch_id == batch_id,
        BatchAdditive.additive_id == additive_id,
    ).first()
    if not existing:
        db.add(BatchAdditive(
            batch_id=batch_id,
            additive_id=additive_id,
            quantity=quantity,
            unit=unit.strip() if unit else None,
            notes=notes or None,
        ))
        db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{batch_id}/additives/{additive_id}/remove")
def batch_remove_additive(
    ferment_id: int,
    batch_id: int,
    additive_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    db.query(BatchAdditive).filter(
        BatchAdditive.batch_id == batch_id,
        BatchAdditive.additive_id == additive_id,
    ).delete()
    db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Add / remove container ─────────────────────────────────────────────────

@router.post("/{batch_id}/containers/add")
def batch_add_container(
    ferment_id: int,
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    name: str = Form(...),
    vessel_type_id: Optional[int] = Form(None),
    vessel_material_id: Optional[int] = Form(None),
    capacity: Optional[float] = Form(None),
    capacity_unit: Optional[str] = Form(None),
    fill_weight: Optional[float] = Form(None),
    fill_unit: Optional[str] = Form(None),
    status_id: Optional[int] = Form(None),
    notes: Optional[str] = Form(None),
):
    db.add(Container(
        batch_id=batch_id,
        name=name.strip(),
        vessel_type_id=vessel_type_id or None,
        vessel_material_id=vessel_material_id or None,
        capacity=capacity,
        capacity_unit=capacity_unit.strip() if capacity_unit else None,
        fill_weight=fill_weight,
        fill_unit=fill_unit.strip() if fill_unit else None,
        status_id=status_id or None,
        notes=notes or None,
    ))
    db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{batch_id}/containers/{container_id}/remove")
def batch_remove_container(
    ferment_id: int,
    batch_id: int,
    container_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    db.query(Container).filter(
        Container.id == container_id,
        Container.batch_id == batch_id,
    ).delete()
    db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ── Edit batch metadata ────────────────────────────────────────────────────

@router.post("/{batch_id}/edit")
def batch_edit(
    ferment_id: int,
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    lot_code: Optional[str] = Form(None),
    status_id: Optional[int] = Form(None),
    stage: int = Form(1),
    started_at: Optional[str] = Form(None),
    target_ready_at: Optional[str] = Form(None),
    target_ph: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
):
    batch = db.query(Batch).filter(
        Batch.id == batch_id, Batch.ferment_id == ferment_id
    ).first()
    if not batch:
        return RedirectResponse(f"/ferments/{ferment_id}", status_code=status.HTTP_302_FOUND)

    def parse_date(val):
        if not val:
            return None
        try:
            return datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            return None

    # Check lot code uniqueness if changed
    new_lot = lot_code.strip() if lot_code and lot_code.strip() else None
    if new_lot and new_lot != batch.lot_code:
        existing = db.query(Batch).filter(
            Batch.lot_code == new_lot, Batch.id != batch_id
        ).first()
        if existing:
            new_lot = batch.lot_code  # revert silently — could show error in future

    batch.lot_code = new_lot
    batch.status_id = status_id
    batch.stage = stage
    batch.started_at = parse_date(started_at)
    batch.target_ready_at = parse_date(target_ready_at)
    batch.target_ph = target_ph
    batch.notes = notes or None
    db.flush()
    _sync_ferment_status(db, ferment_id)
    db.commit()

    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )