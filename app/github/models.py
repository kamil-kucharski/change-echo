from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

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
