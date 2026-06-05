from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.auth import require_user, require_editor
from app.database import get_db
from app.models.schedule import Schedule, ScheduleEvent
from app.models.ferment import Ferment, Batch, Container
from app.models.tool import Tool
from app.models.user import User
from app.templates import templates

router = APIRouter(prefix="/schedules")

TARGET_TYPES = ["ferment", "batch", "container", "tool"]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def _target_name(db: Session, target_type: str, target_id: int) -> str:
    """Resolve a human-readable name for any target type."""
    try:
        if target_type == "ferment":
            obj = db.query(Ferment).filter_by(id=target_id).first()
            return obj.name if obj else f"Ferment #{target_id}"
        elif target_type == "batch":
            obj = db.query(Batch).filter_by(id=target_id).first()
            return obj.lot_code or f"Batch #{target_id}" if obj else f"Batch #{target_id}"
        elif target_type == "container":
            obj = db.query(Container).filter_by(id=target_id).first()
            return obj.name if obj else f"Container #{target_id}"
        elif target_type == "tool":
            obj = db.query(Tool).filter_by(id=target_id).first()
            return obj.name if obj else f"Tool #{target_id}"
    except Exception:
        pass
    return f"{target_type} #{target_id}"


# ── List all schedules ─────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def schedules_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
    target_type: Optional[str] = None,
    show_inactive: bool = False,
):
    query = db.query(Schedule)
    if not show_inactive:
        query = query.filter(Schedule.is_active == True)
    if target_type:
        query = query.filter(Schedule.target_type == target_type)

    # Default: overdue/due first, then by next_due_at
    schedules = query.order_by(Schedule.next_due_at.asc().nullslast()).all()
    now = _now()

    enriched = []
    for s in schedules:
        enriched.append({
            "schedule": s,
            "target_name": _target_name(db, s.target_type, s.target_id),
            "is_overdue": s.next_due_at and s.next_due_at < now,
            "days_until": (s.next_due_at - now).days if s.next_due_at else None,
        })

    # Python sort for enriched fields
    sort_by  = getattr(request, 'query_params', {}).get('sort', 'due') if hasattr(request, 'query_params') else 'due'
    sort_dir = getattr(request, 'query_params', {}).get('dir', 'asc')  if hasattr(request, 'query_params') else 'asc'

    sort_by  = request.query_params.get('sort', 'due')
    sort_dir = request.query_params.get('dir',  'asc')

    sort_key_map = {
        "name":   lambda x: x["schedule"].name.lower(),
        "type":   lambda x: x["schedule"].target_type.lower(),
        "target": lambda x: x["target_name"].lower(),
        "due":    lambda x: x["schedule"].next_due_at or datetime(9999,1,1),
    }
    key_fn = sort_key_map.get(sort_by, sort_key_map["due"])
    enriched = sorted(enriched, key=key_fn, reverse=(sort_dir == "desc"))

    return templates.TemplateResponse(request, "schedules/list.html", {
        "current_user": current_user,
        "enriched": enriched,
        "now": now,
        "target_types": TARGET_TYPES,
        "filters": {"target_type": target_type or "", "show_inactive": show_inactive},
        "sort": sort_by,
        "dir": sort_dir,
    })


# ── New schedule ───────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def schedules_new(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    # Pre-fill from query params when adding from a detail page
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
):
    ferments    = db.query(Ferment).filter(Ferment.archived_at == None).order_by(Ferment.name).all()
    batches     = db.query(Batch).order_by(Batch.id.desc()).all()
    containers  = db.query(Container).order_by(Container.name).all()
    tools       = db.query(Tool).order_by(Tool.name).all()

    return templates.TemplateResponse(request, "schedules/new.html", {
        "current_user": current_user,
        "ferments": ferments, "batches": batches,
        "containers": containers, "tools": tools,
        "target_types": TARGET_TYPES,
        "prefill_type": target_type or "",
        "prefill_id": target_id or "",
        "today": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "errors": {},
    })


@router.post("/new")
def schedules_create(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    target_type: str = Form(...),
    target_id: int = Form(...),
    interval_days: Optional[float] = Form(None),
    next_due_at: Optional[str] = Form(None),
):
    errors = {}
    if not name.strip():
        errors["name"] = "Name is required."
    if target_type not in TARGET_TYPES:
        errors["target_type"] = "Invalid target type."
    if errors:
        return RedirectResponse("/schedules/new", status_code=status.HTTP_303_SEE_OTHER)

    due = _parse_dt(next_due_at) or _now()

    s = Schedule(
        name=name.strip(),
        description=description or None,
        target_type=target_type,
        target_id=target_id,
        interval_days=interval_days or None,
        next_due_at=due,
        is_active=True,
    )
    db.add(s)
    db.commit()

    # Redirect back to origin if came from a detail page
    return RedirectResponse("/schedules", status_code=status.HTTP_303_SEE_OTHER)


# ── Complete a schedule event ──────────────────────────────────────────────

@router.post("/{schedule_id}/complete")
def schedule_complete(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    completed_at: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    schedule = db.query(Schedule).filter_by(id=schedule_id).first()
    if not schedule:
        return RedirectResponse("/schedules", status_code=status.HTTP_302_FOUND)

    now = _now()
    actual = _parse_dt(completed_at) or now
    was_late = bool(schedule.next_due_at and actual > schedule.next_due_at)

    event = ScheduleEvent(
        schedule_id=schedule_id,
        due_at=schedule.next_due_at or actual,
        completed_at=actual,
        completed_by_id=current_user.id,
        was_late=was_late,
        notes=notes or None,
    )
    db.add(event)

    # Recalculate next_due_at from actual completion date
    if schedule.interval_days:
        schedule.next_due_at = actual + timedelta(days=schedule.interval_days)
    else:
        # One-off — deactivate after completion
        schedule.is_active = False
        schedule.next_due_at = None

    db.commit()
    return RedirectResponse("/schedules", status_code=status.HTTP_303_SEE_OTHER)


# ── Edit schedule ──────────────────────────────────────────────────────────

@router.get("/{schedule_id}/edit", response_class=HTMLResponse)
def schedules_edit(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    schedule = db.query(Schedule).filter_by(id=schedule_id).first()
    if not schedule:
        return RedirectResponse("/schedules", status_code=status.HTTP_302_FOUND)

    ferments   = db.query(Ferment).filter(Ferment.archived_at == None).order_by(Ferment.name).all()
    batches    = db.query(Batch).order_by(Batch.id.desc()).all()
    containers = db.query(Container).order_by(Container.name).all()
    tools      = db.query(Tool).order_by(Tool.name).all()

    return templates.TemplateResponse(request, "schedules/edit.html", {
        "current_user": current_user,
        "schedule": schedule,
        "target_name": _target_name(db, schedule.target_type, schedule.target_id),
        "ferments": ferments, "batches": batches,
        "containers": containers, "tools": tools,
        "target_types": TARGET_TYPES,
        "errors": {},
    })


@router.post("/{schedule_id}/edit")
def schedules_update(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    target_type: str = Form(...),
    target_id: int = Form(...),
    interval_days: Optional[float] = Form(None),
    next_due_at: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
):
    schedule = db.query(Schedule).filter_by(id=schedule_id).first()
    if not schedule:
        return RedirectResponse("/schedules", status_code=status.HTTP_302_FOUND)

    schedule.name = name.strip()
    schedule.description = description or None
    schedule.target_type = target_type
    schedule.target_id = target_id
    schedule.interval_days = interval_days or None
    schedule.next_due_at = _parse_dt(next_due_at)
    schedule.is_active = bool(is_active)
    db.commit()
    return RedirectResponse("/schedules", status_code=status.HTTP_303_SEE_OTHER)


# ── Detail / history ───────────────────────────────────────────────────────

@router.get("/{schedule_id}", response_class=HTMLResponse)
def schedule_detail(
    schedule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_user),
):
    schedule = db.query(Schedule).filter_by(id=schedule_id).first()
    if not schedule:
        return RedirectResponse("/schedules", status_code=status.HTTP_302_FOUND)

    events = (
        db.query(ScheduleEvent)
        .filter_by(schedule_id=schedule_id)
        .options(joinedload(ScheduleEvent.completed_by_user))
        .order_by(ScheduleEvent.completed_at.desc())
        .all()
    )

    return templates.TemplateResponse(request, "schedules/detail.html", {
        "current_user": current_user,
        "schedule": schedule,
        "target_name": _target_name(db, schedule.target_type, schedule.target_id),
        "events": events,
        "now": _now(),
        "today_str": datetime.now().strftime("%Y-%m-%dT%H:%M"),
    })


# ── Edit / delete a history event ─────────────────────────────────────────

@router.get("/{schedule_id}/events/{event_id}/edit", response_class=HTMLResponse)
def schedule_event_edit_form(
    schedule_id: int,
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    schedule = db.query(Schedule).filter_by(id=schedule_id).first()
    event    = db.query(ScheduleEvent).filter_by(id=event_id, schedule_id=schedule_id).first()
    if not schedule or not event:
        return RedirectResponse(f"/schedules/{schedule_id}", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "schedules/event_edit.html", {
        "current_user": current_user,
        "schedule": schedule,
        "event": event,
        "target_name": _target_name(db, schedule.target_type, schedule.target_id),
    })


@router.post("/{schedule_id}/events/{event_id}/edit")
def schedule_event_edit(
    schedule_id: int,
    event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
    completed_at: Optional[str] = Form(None),
    due_at: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
):
    event = db.query(ScheduleEvent).filter_by(id=event_id, schedule_id=schedule_id).first()
    if not event:
        return RedirectResponse(f"/schedules/{schedule_id}", status_code=status.HTTP_302_FOUND)
    event.completed_at = _parse_dt(completed_at)
    event.due_at       = _parse_dt(due_at) or event.due_at
    event.notes        = notes or None
    event.was_late     = bool(event.completed_at and event.due_at and event.completed_at > event.due_at)
    db.commit()
    return RedirectResponse(f"/schedules/{schedule_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{schedule_id}/events/{event_id}/delete")
def schedule_event_delete(
    schedule_id: int,
    event_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    event = db.query(ScheduleEvent).filter_by(id=event_id, schedule_id=schedule_id).first()
    if event:
        db.delete(event)
        db.commit()
    return RedirectResponse(f"/schedules/{schedule_id}", status_code=status.HTTP_303_SEE_OTHER)


# ── Delete ─────────────────────────────────────────────────────────────────

@router.post("/{schedule_id}/delete")
def schedules_delete(
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    schedule = db.query(Schedule).filter_by(id=schedule_id).first()
    if schedule:
        db.delete(schedule)
        db.commit()
    return RedirectResponse("/schedules", status_code=status.HTTP_303_SEE_OTHER)