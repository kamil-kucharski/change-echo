from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

SupportedPullRequestAction = Literal["opened", "reopened", "synchronize", "edited"]

SUPPORTED_PULL_REQUEST_ACTIONS: frozenset[str] = frozenset(
    {"opened", "reopened", "synchronize", "edited"}
)


class PullRequestActionEnvelope(BaseModel):
    action: str = Field(min_length=1)


class InstallationPayload(BaseModel):
    id: int = Field(gt=0)


class RepositoryPayload(BaseModel):
    full_name: str = Field(min_length=1)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        parts = value.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository full name must contain owner and repository")
        return value


class PullRequestHeadPayload(BaseModel):
    sha: str = Field(min_length=1)


class PullRequestPayload(BaseModel):
    head: PullRequestHeadPayload


@dataclass(frozen=True, slots=True)
class PullRequestContext:
    delivery_id: str
    event: Literal["pull_request"]
    action: SupportedPullRequestAction
    repository_full_name: str
    pull_request_number: int
    head_sha: str
    installation_id: int


class PullRequestWebhookPayload(BaseModel):
    action: SupportedPullRequestAction
    number: int = Field(gt=0)
    installation: InstallationPayload
    repository: RepositoryPayload
    pull_request: PullRequestPayload

    def to_context(self, delivery_id: str) -> PullRequestContext:
        return PullRequestContext(
            delivery_id=delivery_id,
            event="pull_request",
            action=self.action,
            repository_full_name=self.repository.full_name,
            pull_request_number=self.number,
            head_sha=self.pull_request.head.sha,
            installation_id=self.installation.id,
        )


class PullRequestFile(BaseModel):
    filename: str = Field(min_length=1)
    status: str = Field(min_length=1)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    changes: int = Field(ge=0)
    patch: str | None = None


class RepositoryCommit(BaseModel):
    sha: str = Field(min_length=1)


class AssociatedPullRequest(BaseModel):
    number: int = Field(gt=0)
    title: str = Field(min_length=1)
    body: str | None = None
    state: str = Field(min_length=1)
    merged_at: str | None = None
    closed_at: str | None = None
    html_url: Annotated[str, Field(min_length=1)] | None = None
