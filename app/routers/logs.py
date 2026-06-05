from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_editor
from app.database import get_db
from app.models.ferment import Batch, Ferment
from app.models.log import BatchLog
from app.models.lookup import SmellDescriptor, Status, VisualDescriptor
from app.models.user import User
from app.templates import templates

router = APIRouter(prefix="/ferments/{ferment_id}/batches")


def _sync_ferment_status(db: Session, ferment_id: int) -> None:
    """Set ferment.status_id to the status of the latest batch (by started_at)."""
    latest = (
        db.query(Batch)
        .filter(Batch.ferment_id == ferment_id, Batch.status_id.isnot(None))
        .order_by(Batch.started_at.desc().nullslast(), Batch.batch_number.desc())
        .first()
    )
    if latest:
        ferment = db.query(Ferment).filter_by(id=ferment_id).first()
        if ferment:
            ferment.status_id = latest.status_id


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(val.strip(), fmt)
        except ValueError:
            continue
    return None


@router.post("/{batch_id}/logs/add")
def batch_add_log(
    ferment_id: int,
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    logged_at: Optional[str] = Form(None),
    ph: Optional[float] = Form(None),
    temperature: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    smell_notes: Optional[str] = Form(None),
    visual_notes: Optional[str] = Form(None),
    status_id: Optional[int] = Form(None),
    smell_ids: Optional[List[int]] = Form(None),
    visual_ids: Optional[List[int]] = Form(None),
):
    dt = _parse_dt(logged_at) or datetime.utcnow()
    entry = BatchLog(
        batch_id=batch_id,
        logged_at=dt,
        logged_by_id=current_user.id,
        ph=ph,
        temperature=temperature,
        notes=notes or None,
        smell_notes=smell_notes or None,
        visual_notes=visual_notes or None,
        status_id=status_id or None,
    )
    if smell_ids:
        entry.smell_descriptors = (
            db.query(SmellDescriptor).filter(SmellDescriptor.id.in_(smell_ids)).all()
        )
    if visual_ids:
        entry.visual_descriptors = (
            db.query(VisualDescriptor).filter(VisualDescriptor.id.in_(visual_ids)).all()
        )
    db.add(entry)
    if status_id:
        batch = db.query(Batch).filter_by(id=batch_id).first()
        if batch:
            batch.status_id = status_id
    db.flush()
    _sync_ferment_status(db, ferment_id)
    db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}?tab=logs",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{batch_id}/logs/{log_id}/edit", response_class=HTMLResponse)
def batch_edit_log_form(
    ferment_id: int,
    batch_id: int,
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    from app.models.ferment import Ferment
    ferment = db.query(Ferment).filter_by(id=ferment_id).first()
    batch = db.query(Batch).filter_by(id=batch_id, ferment_id=ferment_id).first()
    entry = db.query(BatchLog).filter_by(id=log_id, batch_id=batch_id).first()
    if not ferment or not batch or not entry:
        return RedirectResponse(
            f"/ferments/{ferment_id}/batches/{batch_id}",
            status_code=status.HTTP_302_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "batches/log_edit.html",
        {
            "current_user": current_user,
            "ferment": ferment,
            "batch": batch,
            "entry": entry,
            "statuses": db.query(Status).order_by(Status.name).all(),
            "smell_descriptors": db.query(SmellDescriptor).order_by(SmellDescriptor.name).all(),
            "visual_descriptors": db.query(VisualDescriptor).order_by(VisualDescriptor.name).all(),
        },
    )


@router.post("/{batch_id}/logs/{log_id}/edit")
def batch_edit_log(
    ferment_id: int,
    batch_id: int,
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    logged_at: Optional[str] = Form(None),
    ph: Optional[float] = Form(None),
    temperature: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    smell_notes: Optional[str] = Form(None),
    visual_notes: Optional[str] = Form(None),
    status_id: Optional[int] = Form(None),
    smell_ids: Optional[List[int]] = Form(None),
    visual_ids: Optional[List[int]] = Form(None),
):
    entry = db.query(BatchLog).filter_by(id=log_id, batch_id=batch_id).first()
    if not entry:
        return RedirectResponse(
            f"/ferments/{ferment_id}/batches/{batch_id}",
            status_code=status.HTTP_302_FOUND,
        )
    dt = _parse_dt(logged_at)
    if dt:
        entry.logged_at = dt
    entry.ph = ph
    entry.temperature = temperature
    entry.notes = notes or None
    entry.smell_notes = smell_notes or None
    entry.visual_notes = visual_notes or None
    entry.status_id = status_id or None
    entry.smell_descriptors = (
        db.query(SmellDescriptor).filter(SmellDescriptor.id.in_(smell_ids)).all()
        if smell_ids else []
    )
    entry.visual_descriptors = (
        db.query(VisualDescriptor).filter(VisualDescriptor.id.in_(visual_ids)).all()
        if visual_ids else []
    )
    if status_id:
        batch = db.query(Batch).filter_by(id=batch_id).first()
        if batch:
            batch.status_id = status_id
    db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}?tab=logs",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{batch_id}/logs/{log_id}/delete")
def batch_delete_log(
    ferment_id: int,
    batch_id: int,
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    entry = db.query(BatchLog).filter_by(id=log_id, batch_id=batch_id).first()
    if entry:
        db.delete(entry)
        db.commit()
    return RedirectResponse(
        f"/ferments/{ferment_id}/batches/{batch_id}?tab=logs",
        status_code=status.HTTP_303_SEE_OTHER,
    )

