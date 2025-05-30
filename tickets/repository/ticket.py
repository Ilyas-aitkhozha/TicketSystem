from typing import List, Optional
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload
from tickets import models
from tickets.models import UserTeam, ProjectUser
from tickets.schemas.ticket import TicketCreate, TicketOut, TicketStatusUpdate, TicketAssigneeUpdate, TicketFeedbackUpdate
from tickets.enums import ProjectRole, TicketType, TicketStatus, WorkerRole

#--------------------------------------- CREATE
def _resolve_assignee(
    db: Session,
    assignee_name: Optional[str],
    project_id: int,
) -> Optional[int]:
    if not assignee_name:
        return None

    user = (
        db.query(models.User)
          .join(ProjectUser, ProjectUser.user_id == models.User.id)
          .filter(
              ProjectUser.project_id == project_id,
              models.User.name.ilike(f"%{assignee_name}%"),
          )
          .first()
    )
    if not user:
        raise HTTPException(404, "Assignee not found in this project")
    return user.id

def create_ticket(
    db: Session,
    ticket_in: TicketCreate,
    user_id: int,
    project_id: int,
    team_id: int | None = None
) -> TicketOut:
    team_id = team_id or ticket_in.team_id
    if team_id is None:
        user: models.User = db.get(models.User, user_id)
        if not user.teams:
            raise HTTPException(400, "Team ID must be provided")
        team_id = user.teams[0].id

    team = db.get(models.Team, team_id)
    if not team:
        raise HTTPException(404, "Team not found")
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    author_link = (
        db.query(ProjectUser)
        .filter_by(user_id=user_id, project_id=project_id)
        .first()
    )
    if not author_link:
        raise HTTPException(403, "You are not a member of this project")

    assigned_user_id = _resolve_assignee(
        db,
        getattr(ticket_in, "assigned_to_name", None),  # поле-строка из схемы
        project_id,
    )

    assigned_worker_team_id: Optional[int] = None
    if getattr(ticket_in, "worker_team_id", None) is not None:
        if project.worker_team_id != ticket_in.worker_team_id:
            raise HTTPException(400, "Worker team not assigned to this project")
        assigned_worker_team_id = ticket_in.worker_team_id
    elif ticket_in.type == TicketType.worker:
        if not project.worker_team_id:
            raise HTTPException(400, "No worker team assigned to project")
        assigned_worker_team_id = project.worker_team_id

    ticket = models.Ticket(
        title=ticket_in.title,
        description=ticket_in.description,
        type=ticket_in.type,
        priority=ticket_in.priority,
        created_by=user_id,
        assigned_to=assigned_user_id,
        worker_team_id=assigned_worker_team_id,
        team_id = team.id,
        project_id=project_id,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    return _load_ticket(db, ticket.id)

#------------------------------ GET LOGICS

def get_ticket_by_id(db: Session, ticket_id: int, project_id: int) -> TicketOut:
    ticket = (
        db.query(models.Ticket)
          .options(
              joinedload(models.Ticket.creator),
              joinedload(models.Ticket.assignee),
              joinedload(models.Ticket.worker_team),
          )
          .filter_by(id=ticket_id, project_id=project_id)
          .first()
    )
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} not found")
    return TicketOut.model_validate(ticket)

def get_all_tickets(db: Session, project_id: int) -> List[TicketOut]:
    tickets = (
        db.query(models.Ticket)
          .options(
              joinedload(models.Ticket.creator),
              joinedload(models.Ticket.assignee),
              joinedload(models.Ticket.worker_team),
          )
          .filter_by(project_id=project_id)
          .all()
    )
    return [TicketOut.model_validate(t) for t in tickets]

def get_user_tickets(db: Session, user_id, project_id: int) -> List[TicketOut]:
    tickets = (
        db.query(models.Ticket)
          .options(
              joinedload(models.Ticket.creator),
              joinedload(models.Ticket.assignee),
              joinedload(models.Ticket.worker_team),
          )
          .filter_by(created_by=user_id, project_id=project_id)
          .all()
    )
    return [TicketOut.model_validate(t) for t in tickets]

def get_tickets_assigned_to_user(
    db: Session, current_user: models.User, project_id: int
) -> List[TicketOut]:
    if not any(pu.project_id == project_id for pu in current_user.project_users):
        raise HTTPException(403, "Not a project member")

    tickets = (
        db.query(models.Ticket)
          .options(
              joinedload(models.Ticket.creator),
              joinedload(models.Ticket.assignee),
              joinedload(models.Ticket.worker_team),
          )
          .filter_by(assigned_to=current_user.id, project_id=project_id)
          .filter(models.Ticket.status.in_([TicketStatus.open, TicketStatus.in_progress]))
          .all()
    )
    return [TicketOut.model_validate(t) for t in tickets]

#-------------------------------- UPDATE LOGIC

# only can change it step by step
ALLOWED_STATUS_TRANSITIONS = {
    "open": ["in_progress"],
    "in_progress": ["closed"],
    "closed": [],
}

def update_ticket_status_by_assignee(
    db: Session,
    ticket_id: int,
    project_id: int,
    update: TicketStatusUpdate,
    current_user: models.User
) -> TicketOut:
    ticket = (
        db.query(models.Ticket)
          .filter_by(id=ticket_id, project_id=project_id)
          .first()
    )
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket.assigned_to != current_user.id:
        raise HTTPException(403, "Only assignee can update")

    curr, nxt = ticket.status.value, update.status.value
    if nxt not in ALLOWED_STATUS_TRANSITIONS[curr]:
        raise HTTPException(400, f"Cannot go from {curr} to {nxt}")

    ticket.status = update.status
    if update.status.name == "closed":
        ticket.closed_at = datetime.now(timezone.utc)
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _load_ticket(db, ticket.id)

def leave_feedback_by_creator(
    db: Session,
    ticket_id: int,
    project_id: int,
    update: TicketFeedbackUpdate,
    current_user: models.User
) -> TicketOut:
    ticket = (
        db.query(models.Ticket)
          .filter_by(id=ticket_id, project_id=project_id)
          .first()
    )
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if ticket.created_by != current_user.id:
        raise HTTPException(403, "Only creator can leave feedback")
    if ticket.status.name != "closed":
        raise HTTPException(400, "Feedback only after close")

    ticket.feedback = update.feedback or ticket.feedback
    ticket.confirmed = update.confirmed
    ticket.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _load_ticket(db, ticket.id)

def update_ticket_assignee(
    db: Session,
    ticket_id: int,
    update: TicketAssigneeUpdate,
    project_id: int
) -> TicketOut:
    ticket = (
        db.query(models.Ticket)
          .filter_by(id=ticket_id, project_id=project_id)
          .first()
    )
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    # only project-admin may reassign
    is_admin = (
        db.query(ProjectUser)
          .filter_by(
              user_id=update.assigned_to,
              project_id=project_id,
              role=ProjectRole.admin
          )
          .first()
    )
    if not is_admin:
        raise HTTPException(403, "Only project admin can reassign")

    role_link = (
        db.query(ProjectUser)
          .filter_by(user_id=update.assigned_to, project_id=project_id)
          .first()
    )
    if not role_link or role_link.role not in (
            ProjectRole.member, WorkerRole.worker
    ):
        raise HTTPException(403, "Must be member or worker")

    user = db.get(models.User, update.assigned_to)
    if not user or not user.is_available:
        raise HTTPException(400, "User not available")

    ticket.assigned_to = update.assigned_to
    db.commit()
    return _load_ticket(db, ticket.id)

#-------------------------------- DELETE TICKET
def delete_ticket(
    db: Session,
    ticket_id: int,
    project_id: int,
    current_user: models.User,
) -> None:
    ticket = (
        db.query(models.Ticket)
          .filter_by(id=ticket_id, project_id=project_id)
          .first()
    )
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    is_creator = ticket.created_by == current_user.id
    is_admin = any(
        pu.project_id == project_id and pu.role == ProjectRole.admin
        for pu in current_user.project_users
    )
    if not (is_creator or is_admin):
        raise HTTPException(403, "Not permitted")

    db.delete(ticket)
    db.commit()

#-------------------------------- HELPER
def _load_ticket(db: Session, ticket_id: int) -> TicketOut:
    ticket = (
        db.query(models.Ticket)
          .options(
              joinedload(models.Ticket.creator),
              joinedload(models.Ticket.assignee),
              joinedload(models.Ticket.worker_team),
          )
          .get(ticket_id)
    )
    return TicketOut.model_validate(ticket)
