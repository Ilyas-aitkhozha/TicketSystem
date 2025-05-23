from fastapi import APIRouter, Depends, HTTPException, Path, status, Response, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
from tickets.schemas import user as user_schema
from tickets.schemas import team as team_schema
from tickets.database import get_db
from tickets.repository import user as user_repository
from tickets.oauth2 import get_current_user
from tickets import models
from tickets.routers.dependencies import require_team_member
from ..enums import TeamRole

router = APIRouter(prefix="/teams/{team_id}", tags=["Team Members"])

def _ensure_member(user: models.User, team_id: int):
    if not any(t.id == team_id for t in user.teams):
        raise HTTPException(status_code=403, detail="Team not available.")

def _ensure_team_admin(user: models.User, team_id: int):
    if not any(ut.team_id == team_id and ut.role is TeamRole.admin for ut in user.user_teams):
        raise HTTPException(status_code=403, detail="Requires team admin role.")

#----------------------- GET logics
@router.get(
    "/users",
    response_model=List[user_schema.UserBrief],
)
def list_team_user_names(
    team_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_team_member),
):
    _ensure_member(current_user, team_id)
    return user_repository.get_team_user_briefs(db, team_id)

@router.get(
    "/available-admins",
    response_model=List[user_schema.UserBrief],
)
def available_admins(
    team_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_team_member),
):
    _ensure_member(current_user, team_id)
    return user_repository.get_available_admin_briefs(db, team_id)

@router.get(
    "/available-users",
    response_model=List[user_schema.UserBrief],
)
def available_members(
    team_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_team_member),
):
    _ensure_member(current_user, team_id)
    users = user_repository.get_available_users_by_role(
        db,
        role=TeamRole.member.value,
        team_id=team_id
    )
    return [user_schema.UserBrief.model_validate(u) for u in users]
@router.get("/teams", response_model=List[team_schema.TeamOut])
def list_my_teams(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return current_user.teams
@router.get(
    "/users/{user_id}",
    response_model=user_schema.UserInTeamWithProjects
)
def read_user_in_team(
    team_id: int    = Path(..., ge=1),
    user_id: int    = Path(..., ge=1),
    db: Session     = Depends(get_db),
    _: None         = Depends(require_team_member),  # current_user в этой команде
):
    assoc       = user_repository.get_user_with_projects_in_team(db, team_id, user_id)
    proj_assocs = user_repository.get_project_memberships_for_user_in_team(db, team_id, user_id)
    #pydantic take UserBrief,
    #assoc.role/joined_at
    #from list proj_assocs in ProjectMembership.
    return {
        "user":      assoc.user,
        "role":      assoc.role,
        "joined_at": assoc.joined_at,
        "projects":  proj_assocs
    }

#---- Update Logics
@router.put("/availability", response_model=user_schema.UserAvailabilityOut)
def update_my_availability(
    is_available: bool,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    current_user.is_available = is_available
    db.commit()
    db.refresh(current_user)
    return current_user

@router.post(
    "/members/{user_id}",
    response_model=team_schema.TeamMembership,
    status_code=status.HTTP_201_CREATED
)
def add_user_to_team(
    user_id: int = Path(..., ge=1),
    team_id: int = Path(..., ge=1),
    role: TeamRole = Query(TeamRole.member),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _ensure_team_admin(current_user, team_id)
    exists = (db.query(models.UserTeam).filter_by(user_id=user_id, team_id=team_id).first())
    if exists:
        raise HTTPException(status_code=400, detail="User already in team")
    association = models.UserTeam(
        user_id=user_id,
        team_id=team_id,
        role=role,
        joined_at=datetime.now()
    )
    db.add(association)
    db.commit()
    db.refresh(association)
    return association

@router.delete(
    "/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
def remove_user_from_team(
    user_id: int = Path(..., ge=1),
    team_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    _ensure_team_admin(current_user, team_id)
    deleted = (
        db.query(models.UserTeam)
          .filter_by(user_id=user_id, team_id=team_id)
          .delete()
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="User not in this team")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)