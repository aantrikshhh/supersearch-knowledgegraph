"""Session state and intent memory for multi-turn SuperSearch conversations.

Conversation turns need sticky constraints such as product type, occasion,
gender, body type, and religion to survive follow-ups like "in red" or "make
it cheaper". This module stores turns and merges intent state safely.
"""

import json
import time
import uuid
from dataclasses import dataclass, field, asdict


@dataclass
class Turn:
    query: str
    intents: dict
    workflow_type: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class Session:
    session_id: str = ""
    brand: str = "aza"
    turns: list = field(default_factory=list)
    active_intents: dict = field(default_factory=dict)
    active_workflow: str = ""
    preferences: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.session_id:
            self.session_id = str(uuid.uuid4())[:8]

    def add_turn(self, query, intents, workflow_type=""):
        turn = Turn(query=query, intents=intents, workflow_type=workflow_type)
        self.turns.append(turn)
        self.merge_intents(intents)

    # Keys that persist from prior turns unless the new turn explicitly overrides them
    _STICKY_KEYS = frozenset({
        "activity", "agegroup", "avoid_product_type", "bodytype", "budget",
        "colour", "complexion", "duration", "event", "functional_needs",
        "gender", "health", "location", "month", "occasion", "place",
        "price_max", "price_min", "product_type", "profession", "relation",
        "religion", "style_goals", "time", "weather",
    })
    _CLEARABLE_FLAGS = frozenset({"_needs_religion"})

    def merge_intents(self, new_intents):
        """Smart merge: new values always win, sticky keys persist when not contradicted.

        Internal flags usually persist, but clarification flags clear when the
        new turn resolves them.
        """
        merged = {}
        clear_flags = set()
        if new_intents.get("_needs_religion") is False or "religion" in new_intents:
            clear_flags.add("_needs_religion")

        for k, v in self.active_intents.items():
            if k.startswith("_"):
                if k in clear_flags:
                    continue
                merged[k] = v
            elif k in self._STICKY_KEYS and k not in new_intents:
                merged[k] = v
        for k, v in new_intents.items():
            if k in self._CLEARABLE_FLAGS and v is False:
                merged.pop(k, None)
                continue
            merged[k] = v
        self.active_intents = merged

    def get_context_for_extraction(self):
        """Return context from prior turns for intent extraction."""
        if not self.turns:
            return None
        return {
            "prior_intents": self.active_intents,
            "brand": self.brand,
            "turn_count": len(self.turns),
        }

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "brand": self.brand,
            "turns": [{"query": t.query, "intents": t.intents,
                       "workflow_type": t.workflow_type} for t in self.turns],
            "active_intents": self.active_intents,
            "active_workflow": self.active_workflow,
            "preferences": self.preferences,
        }

    def save(self, path=None):
        if not path:
            path = f"sessions/{self.session_id}.json"
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            data = json.load(f)
        session = cls(
            session_id=data["session_id"],
            brand=data.get("brand", "aza"),
            active_intents=data.get("active_intents", {}),
            active_workflow=data.get("active_workflow", ""),
            preferences=data.get("preferences", {}),
        )
        for t in data.get("turns", []):
            session.turns.append(Turn(
                query=t["query"], intents=t["intents"],
                workflow_type=t.get("workflow_type", ""),
            ))
        return session
