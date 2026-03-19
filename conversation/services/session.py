import uuid
from typing import Protocol

from conversation.domain.model import Conversation


class SessionStore(Protocol):
    def get(self, session_id: str) -> Conversation | None: ...
    def save(self, conversation: Conversation) -> str: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._store: dict[str, Conversation] = {}

    def get(self, session_id: str) -> Conversation | None:
        return self._store.get(session_id)

    def save(self, conversation: Conversation) -> str:
        session_id = conversation.session_id or str(uuid.uuid4())
        conv = conversation.with_session_id(session_id)
        self._store[session_id] = conv
        return session_id
