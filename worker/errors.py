from dataclasses import dataclass


class ErrorCode:
    CANCELLED = "CANCELLED"
    OOM = "OOM"
    MODEL_LOAD = "MODEL_LOAD"
    IO = "IO"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"
    HUNG = "HUNG"


@dataclass
class WorkerError(Exception):
    code: str
    message: str
    detail: dict | None = None

    def __str__(self):
        return f"{self.code}: {self.message}"