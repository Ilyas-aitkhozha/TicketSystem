from pydantic import BaseModel
from pydantic import ConfigDict
from typing import Optional
from datetime import datetime
from tickets.schemas import user
from tickets.schemas.worker_team import WorkerTeamBrief
from ..enums import *



#base, parent
class TicketBase(BaseModel):
    title: str
    description: str

class TicketCreate(TicketBase):
    type: TicketType = TicketType.worker
    team_id: int | None = None
    project_id: int | None = None
    assigned_to_name: Optional[str] = None
    assigned_to: int | None = None
    worker_team_id: Optional[int] = None
    priority: Optional[TicketPriority] = TicketPriority.medium
    model_config = ConfigDict(from_attributes=True)

class TicketStatusUpdate(BaseModel):
    status: TicketStatus

class TicketFeedbackUpdate(BaseModel):
    feedback: Optional[str] = None
    confirmed: bool

class TicketAssigneeUpdate(BaseModel):
    assigned_to: int
#we can create tickets only in projects, so i removed team_id
class TicketOut(TicketBase):
    id: int
    type: TicketType
    status: TicketStatus
    creator: user.UserBrief
    worker_team: Optional[WorkerTeamBrief] = None
    assignee: Optional[user.UserBrief] = None
    created_at: datetime
    priority: TicketPriority
    confirmed: bool
    feedback: Optional[str]
    model_config = ConfigDict(from_attributes=True)
