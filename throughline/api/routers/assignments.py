"""Project creation and explicit conversation membership corrections."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from throughline.queries import assignments as Q

from ..deps import connection
from ..settings import Settings
from .common import get_settings

router = APIRouter(tags=["project membership"])


class Project(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def readable(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank.")
        return value.strip()


class Assignment(BaseModel):
    conversation_ids: list[int] = Field(min_length=1, max_length=100)
    project: str | None = Field(None, max_length=200)
    expected_project: str = Field(max_length=4096)

    @field_validator("conversation_ids")
    @classmethod
    def unique_positive(cls, value):
        if any(i <= 0 for i in value) or len(set(value)) != len(value):
            raise ValueError("Choose unique conversation IDs.")
        return value


@router.post("/projects/create", status_code=201)
def create(body: Project, settings: Settings = Depends(get_settings)):
    with connection(settings) as conn:
        return Q.create_project(conn, body.display_name)


@router.put("/projects/assign")
def assign(body: Assignment, settings: Settings = Depends(get_settings)):
    try:
        with connection(settings) as conn:
            return Q.assign(conn, body.conversation_ids, body.project, body.expected_project)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/projects/assignment-history/{conversation_id}")
def history(conversation_id: int, settings: Settings = Depends(get_settings)):
    with connection(settings) as conn:
        return {"changes": Q.history(conn, conversation_id)}
