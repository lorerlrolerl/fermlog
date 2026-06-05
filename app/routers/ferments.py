from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.auth import require_user, require_editor, require_admin
from app.database import get_db
from app.lot_code import generate_lot_code
from app.models.ferment import Batch, Ferment
from app.models.lookup import Category, Status
from app.models.user import User
from app.templates import templates

router = APIRouter(prefix="/ferments")


def _form_lookups(db: Session) -> dict:
    return {
        "categories": db.query(Category).order_by(Category.name).all(),
        "statuses": db.query(Status).order_by(Status.name).all(),
    }


def _parse_date(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d")
    except ValueError:
        return None


# ── List ───────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def ferments_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
    category_id: Optional[str] = None,
    status_id: Optional[str] = None,
    q: Optional[str] = None,
    sort: Optional[str] = None,
    dir: Optional[str] = None,
):
    cat_id   = int(category_id) if category_id and category_id.strip() else None
    stat_id  = int(status_id)   if status_id   and status_id.strip()   else None
    sort_by  = sort or "created"
    sort_dir = dir  or "desc"

    query = (
        db.query(Ferment)
        .filter(Ferment.archived_at == None)
        .options(
            joinedload(Ferment.category),
            joinedload(Ferment.status),
            joinedload(Ferment.batches),
        )
    )
    if cat_id:
        query = query.filter(Ferment.category_id == cat_id)
    if stat_id:
        query = query.filter(Ferment.status_id == stat_id)
    if q:
        query = query.filter(Ferment.name.ilike(f"%{q}%"))

    from app.models.lookup import Category as CatModel, Status as StatModel
    from sqlalchemy import func as sqlfunc

    # Columns that need joins
    if sort_by == "name":
        col = Ferment.name
        query = query.order_by(col.asc() if sort_dir == "asc" else col.desc())
    elif sort_by == "category":
        query = query.outerjoin(CatModel, Ferment.category_id == CatModel.id)
        query = query.order_by(CatModel.name.asc() if sort_dir == "asc" else CatModel.name.desc())
    elif sort_by == "status":
        query = query.outerjoin(StatModel, Ferment.status_id == StatModel.id)
        query = query.order_by(StatModel.name.asc() if sort_dir == "asc" else StatModel.name.desc())
    elif sort_by == "batches":
        # Sort in Python after fetching
        query = query.order_by(Ferment.created_at.desc())
    elif sort_by == "age":
        # Sort in Python after computing age
        query = query.order_by(Ferment.created_at.desc())
    else:
        # Default: created
        query = query.order_by(Ferment.created_at.asc() if sort_dir == "asc" else Ferment.created_at.desc())

    ferments = query.all()

    from datetime import datetime as dt
    from app.models.log import BatchLog
    from app.models.lookup import Status as StatModel
    from sqlalchemy import func as sqlfunc

    now = dt.now()
    active_statuses = {"active", "stasis"}

    active_status_row = db.query(StatModel).filter(sqlfunc.lower(StatModel.name) == "active").first()
    active_id = active_status_row.id if active_status_row else None

    # Bulk log queries — one pass each, keyed by ferment id
    batch_to_fid = {b.id: f.id for f in ferments for b in f.batches}
    all_batch_ids = list(batch_to_fid)

    last_log_by_fid: dict = {}
    deactivated_at_by_fid: dict = {}

    if all_batch_ids:
        for batch_id, latest in (
            db.query(BatchLog.batch_id, sqlfunc.max(BatchLog.logged_at).label("l"))
            .filter(BatchLog.batch_id.in_(all_batch_ids))
            .group_by(BatchLog.batch_id)
            .all()
        ):
            fid = batch_to_fid[batch_id]
            if fid not in last_log_by_fid or latest > last_log_by_fid[fid]:
                last_log_by_fid[fid] = latest

        if active_id is not None:
            for batch_id, latest in (
                db.query(BatchLog.batch_id, sqlfunc.max(BatchLog.logged_at).label("l"))
                .filter(
                    BatchLog.batch_id.in_(all_batch_ids),
                    BatchLog.status_id.isnot(None),
                    BatchLog.status_id != active_id,
                )
                .group_by(BatchLog.batch_id)
                .all()
            ):
                fid = batch_to_fid[batch_id]
                if fid not in deactivated_at_by_fid or latest > deactivated_at_by_fid[fid]:
                    deactivated_at_by_fid[fid] = latest

    ferment_ages = {}
    for f in ferments:
        is_active = bool(f.status and f.status.name.lower() in active_statuses)
        last_log_date = last_log_by_fid.get(f.id)

        if is_active:
            end_date = now
        else:
            # End when the status was last logged as non-active; fall back to last log or now
            end_date = deactivated_at_by_fid.get(f.id) or last_log_date or now

        latest_batch = sorted(f.batches, key=lambda b: b.started_at or dt.min)[-1] if f.batches else None
        start_date = (latest_batch.started_at if latest_batch and latest_batch.started_at else f.created_at) or now
        age_days = max((end_date - start_date).days, 0)

        ferment_ages[f.id] = {
            "days": age_days,
            "last_log": last_log_date,
            "is_active": is_active,
        }

    # Python-side sort for computed columns
    if sort_by == "batches":
        ferments = sorted(ferments, key=lambda f: len(f.batches),
                         reverse=(sort_dir == "desc"))
    elif sort_by == "age":
        ferments = sorted(ferments, key=lambda f: ferment_ages.get(f.id, {}).get("days", 0),
                         reverse=(sort_dir == "desc"))

    return templates.TemplateResponse(
        request,
        "ferments/list.html",
        {
            "current_user": current_user,
            "ferments": ferments,
            "ferment_ages": ferment_ages,
            "categories": db.query(Category).order_by(Category.name).all(),
            "statuses": db.query(Status).order_by(Status.name).all(),
            "filters": {"category_id": cat_id, "status_id": stat_id, "q": q or ""},
            "sort": sort_by,
            "dir": sort_dir,
        },
    )


# ── New ────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def ferments_new(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    today = datetime.now().strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request,
        "ferments/new.html",
        {"current_user": current_user, "errors": {}, "today": today, **_form_lookups(db)},
    )


@router.post("/new")
def ferments_create(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    # Ferment fields
    name: str = Form(...),
    category_id: Optional[int] = Form(None),
    status_id: Optional[int] = Form(None),
    is_ongoing: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    # Batch fields
    batch_stage: int = Form(1),
    batch_started_at: Optional[str] = Form(None),
    batch_target_ready_at: Optional[str] = Form(None),
    batch_target_ph: Optional[float] = Form(None),
    batch_notes: Optional[str] = Form(None),
    batch_lot_code: Optional[str] = Form(None),
):
    errors = {}
    if not name.strip():
        errors["name"] = "Name is required."

    # Check lot code uniqueness if provided
    if batch_lot_code and batch_lot_code.strip():
        existing = db.query(Batch).filter(Batch.lot_code == batch_lot_code.strip()).first()
        if existing:
            errors["batch_lot_code"] = f"Lot code '{batch_lot_code}' is already in use."

    if errors:
        today = datetime.now().strftime("%Y-%m-%d")
        return templates.TemplateResponse(
            request,
            "ferments/new.html",
            {"current_user": current_user, "errors": errors, "today": today, **_form_lookups(db)},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    started = _parse_date(batch_started_at) or datetime.now(timezone.utc).replace(tzinfo=None)

    # Get category name for lot code generation
    category_name = None
    if category_id:
        cat = db.query(Category).filter(Category.id == category_id).first()
        category_name = cat.name if cat else None

    ferment = Ferment(
        name=name.strip(),
        description=description or None,
        category_id=category_id,
        status_id=status_id,
        is_ongoing=bool(is_ongoing),
        created_by_id=current_user.id,
    )
    db.add(ferment)
    db.flush()  # get ferment.id

    batch_num = 1  # first batch
    lot_code = (
        batch_lot_code.strip()
        if batch_lot_code and batch_lot_code.strip()
        else generate_lot_code(name.strip(), category_name, started, batch_stage, batch_num)
    )

    batch = Batch(
        ferment_id=ferment.id,
        batch_number=batch_num,
        lot_code=lot_code,
        stage=batch_stage,
        status_id=status_id,
        started_at=started,
        target_ready_at=_parse_date(batch_target_ready_at),
        target_ph=batch_target_ph,
        notes=batch_notes or None,
        created_by_id=current_user.id,
    )
    db.add(batch)
    db.commit()

    return RedirectResponse(f"/ferments/{ferment.id}", status_code=status.HTTP_303_SEE_OTHER)


# ── Detail ─────────────────────────────────────────────────────────────────

@router.get("/{ferment_id}", response_class=HTMLResponse)
def ferments_detail(
    ferment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    ferment = (
        db.query(Ferment)
        .filter(Ferment.id == ferment_id)
        .options(
            joinedload(Ferment.category),
            joinedload(Ferment.status),
            joinedload(Ferment.created_by_user),
            joinedload(Ferment.batches).joinedload(Batch.status),
            joinedload(Ferment.batches).joinedload(Batch.ingredients),
            joinedload(Ferment.batches).joinedload(Batch.additives),
            joinedload(Ferment.batches).joinedload(Batch.containers),
        )
        .first()
    )

    if not ferment:
        return templates.TemplateResponse(
            request, "404.html", {"current_user": current_user}, status_code=404
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    batches = sorted(ferment.batches, key=lambda b: b.started_at or datetime.min)

    # Compute age_end per batch: date of last non-active status log, or now if still active
    from app.models.log import BatchLog
    from sqlalchemy import func as sqlfunc
    active_status = db.query(Status).filter(Status.name.ilike("active")).first()
    active_id = active_status.id if active_status else None

    batch_age_ends = {b.id: now for b in batches}
    inactive_ids = [b.id for b in batches if b.status_id != active_id]
    if inactive_ids and active_id is not None:
        for batch_id, latest in (
            db.query(BatchLog.batch_id, sqlfunc.max(BatchLog.logged_at).label("l"))
            .filter(
                BatchLog.batch_id.in_(inactive_ids),
                BatchLog.status_id.isnot(None),
                BatchLog.status_id != active_id,
            )
            .group_by(BatchLog.batch_id)
            .all()
        ):
            batch_age_ends[batch_id] = latest

    return templates.TemplateResponse(
        request,
        "ferments/detail.html",
        {
            "current_user": current_user,
            "ferment": ferment,
            "batches": batches,
            "now": now,
            "batch_age_ends": batch_age_ends,
        },
    )


# ── Edit ferment ───────────────────────────────────────────────────────────

@router.get("/{ferment_id}/edit", response_class=HTMLResponse)
def ferments_edit(
    ferment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    ferment = db.query(Ferment).filter(Ferment.id == ferment_id).first()
    if not ferment:
        return RedirectResponse("/ferments", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        request,
        "ferments/edit.html",
        {"current_user": current_user, "ferment": ferment, "errors": {}, **_form_lookups(db)},
    )


@router.post("/{ferment_id}/edit")
def ferments_update(
    ferment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    name: str = Form(...),
    category_id: Optional[int] = Form(None),
    status_id: Optional[int] = Form(None),
    is_ongoing: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
):
    ferment = db.query(Ferment).filter(Ferment.id == ferment_id).first()
    if not ferment:
        return RedirectResponse("/ferments", status_code=status.HTTP_302_FOUND)

    errors = {}
    if not name.strip():
        errors["name"] = "Name is required."

    if errors:
        return templates.TemplateResponse(
            request,
            "ferments/edit.html",
            {"current_user": current_user, "ferment": ferment, "errors": errors, **_form_lookups(db)},
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    ferment.name = name.strip()
    ferment.description = description or None
    ferment.category_id = category_id
    ferment.status_id = status_id
    ferment.is_ongoing = bool(is_ongoing)
    db.commit()

    return RedirectResponse(f"/ferments/{ferment_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{ferment_id}/delete", response_class=HTMLResponse)
def ferment_delete_confirm(
    ferment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    ferment = db.query(Ferment).filter_by(id=ferment_id).first()
    if not ferment:
        return RedirectResponse("/ferments", status_code=status.HTTP_302_FOUND)
    confirm_key = f"{ferment.id}-{ferment.name}"
    return templates.TemplateResponse(request, "ferments/delete_confirm.html", {
        "current_user": current_user,
        "ferment": ferment,
        "confirm_key": confirm_key,
        "error": None,
    })


@router.post("/{ferment_id}/delete")
def ferment_delete(
    ferment_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    confirm_name: str = Form(...),
):
    ferment = db.query(Ferment).filter_by(id=ferment_id).first()
    if not ferment:
        return RedirectResponse("/ferments", status_code=status.HTTP_302_FOUND)

    confirm_key = f"{ferment.id}-{ferment.name}"
    if confirm_name.strip() != confirm_key:
        return templates.TemplateResponse(request, "ferments/delete_confirm.html", {
            "current_user": current_user,
            "ferment": ferment,
            "confirm_key": confirm_key,
            "error": f"Does not match. Type exactly: {confirm_key}",
        }, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    from app.models.ferment import Batch, BatchIngredient, BatchAdditive, Container
    from app.models.log import BatchLog, log_smells, log_visuals
    from app.models.schedule import Schedule

    # Delete all related records manually to avoid FK constraint errors
    for batch in ferment.batches:
        # Delete log entries and their descriptors
        for log in db.query(BatchLog).filter_by(batch_id=batch.id).all():
            db.execute(log_smells.delete().where(log_smells.c.log_id == log.id))
            db.execute(log_visuals.delete().where(log_visuals.c.log_id == log.id))
            db.delete(log)
        # Delete batch ingredients and additives
        db.query(BatchIngredient).filter_by(batch_id=batch.id).delete()
        db.query(BatchAdditive).filter_by(batch_id=batch.id).delete()
        # Delete containers
        db.query(Container).filter_by(batch_id=batch.id).delete()
        db.delete(batch)

    # Delete related schedules
    db.query(Schedule).filter_by(target_type="ferment", target_id=ferment.id).delete()

    db.delete(ferment)
    db.commit()
    return RedirectResponse("/ferments", status_code=status.HTTP_303_SEE_OTHER)