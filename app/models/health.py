from pydantic import BaseModel


class DependencyStatus(BaseModel):
    redis: bool


class HealthResponse(BaseModel):
    status: str
    env: str
    dependencies: DependencyStatus
