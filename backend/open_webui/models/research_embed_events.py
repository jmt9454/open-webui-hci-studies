"""
Storage for the research embed's optional behavioral-tracking features
(keystroke dynamics, temporal delays, tab-visibility during streaming,
clipboard copy/paste) -- see each model's Model.meta.research_embed
track_keystrokes/track_temporal_delays/track_visibility/track_clipboard
toggles and src/lib/utils/researchEmbedTracking.ts for where these get
produced. Deliberately a separate table from `chat`, not folded into the
chat's own JSON blob: this is high-volume, append-only telemetry with a
different access pattern (bulk export for analysis) than a chat transcript,
and keeping it separate means a researcher who never enables tracking never
touches this table at all.

Every event carries model_id (set once per ingest batch from the chat the
participant was using) so exports and the in-app viewer can be scoped to one
study at a time -- see routers/research_embed.py's
GET /models/{model_id}/events. It's nullable only so rows written before
this column existed don't break; going forward every new row sets it.
"""

import logging
import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_db
from open_webui.env import SRC_LOG_LEVELS

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, String, JSON

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

####################
# DB Schema
####################


class ResearchEmbedEvent(Base):
    __tablename__ = "research_embed_event"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True)
    chat_id = Column(String, index=True, nullable=True)

    # Which model's research embed this event was produced under -- lets
    # exports/the viewer scope to one study without joining out to the chat
    # table. See the module docstring for why this is nullable.
    model_id = Column(String, index=True, nullable=True)

    # One of: "keystroke", "temporal_delay", "visibility", "clipboard".
    # Not an enum column on purpose -- new event types shouldn't need a
    # migration, just a new value here and on the frontend.
    event_type = Column(String, index=True)

    # Event-specific fields (key, timing deltas, clipboard text, etc.) --
    # freeform JSON rather than dedicated columns per event type, since the
    # four tracking features have almost nothing in common shape-wise.
    data = Column(JSON)

    # When the browser says the event happened, vs. when this server
    # received the (possibly batched, possibly delayed-by-a-few-seconds)
    # request containing it. Both are kept since they can legitimately
    # differ by a few seconds given client-side batching.
    client_timestamp = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger)


class ResearchEmbedEventModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    chat_id: Optional[str] = None
    model_id: Optional[str] = None
    event_type: str
    data: dict
    client_timestamp: Optional[int] = None
    created_at: int


####################
# Forms
####################


class ResearchEmbedEventForm(BaseModel):
    chat_id: Optional[str] = None
    event_type: str
    data: dict = {}
    client_timestamp: Optional[int] = None


class ResearchEmbedEventBatchForm(BaseModel):
    # model_id is one-per-batch rather than one-per-event: a batch always
    # comes from a single chat page, which has exactly one model in research
    # embed mode, so there's no reason to repeat it on every event. The
    # router looks up *this* model's track_* toggles to decide what (if
    # anything) in the batch gets accepted -- see POST /events.
    model_id: Optional[str] = None
    events: list[ResearchEmbedEventForm]


####################
# Table
####################


class ResearchEmbedEventsTable:
    def insert_events(
        self, user_id: str, model_id: Optional[str], events: list[ResearchEmbedEventForm]
    ) -> int:
        """Bulk-inserts a batch of events for one participant, all tagged
        with the same model_id (see ResearchEmbedEventBatchForm). Returns the
        number of rows inserted. Deliberately tolerant of a single bad event
        in a batch rather than failing the whole batch -- one malformed
        client-side event shouldn't cost the rest of a participant's session
        worth of data."""
        now = int(time.time())
        rows = []
        for event in events:
            try:
                rows.append(
                    ResearchEmbedEvent(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        chat_id=event.chat_id,
                        model_id=model_id,
                        event_type=event.event_type,
                        data=event.data,
                        client_timestamp=event.client_timestamp,
                        created_at=now,
                    )
                )
            except Exception as e:
                log.warning("Skipping malformed research embed event: %s", e)

        if not rows:
            return 0

        with get_db() as db:
            db.add_all(rows)
            db.commit()

        return len(rows)

    def get_events_by_user_id(self, user_id: str) -> list[ResearchEmbedEventModel]:
        with get_db() as db:
            events = (
                db.query(ResearchEmbedEvent)
                .filter_by(user_id=user_id)
                .order_by(ResearchEmbedEvent.created_at.asc())
                .all()
            )
            return [ResearchEmbedEventModel.model_validate(e) for e in events]

    def get_all_events(
        self, skip: Optional[int] = None, limit: Optional[int] = None
    ) -> list[ResearchEmbedEventModel]:
        """For a full-instance export across every study at once -- every
        event across every participant/model, oldest first. skip/limit are
        there so a very large export can be paginated instead of loading
        everything into memory at once. Most exports should prefer
        get_events_by_model_id below to scope to one study."""
        with get_db() as db:
            query = db.query(ResearchEmbedEvent).order_by(
                ResearchEmbedEvent.created_at.asc()
            )
            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)
            return [ResearchEmbedEventModel.model_validate(e) for e in query.all()]

    def get_events_by_model_id(
        self,
        model_id: str,
        skip: Optional[int] = None,
        limit: Optional[int] = None,
        event_type: Optional[str] = None,
    ) -> list[ResearchEmbedEventModel]:
        """Backs both the per-model CSV export (no skip/limit -- everything,
        oldest first) and the in-app data viewer (paginated via skip/limit,
        optionally filtered to one event_type)."""
        with get_db() as db:
            query = db.query(ResearchEmbedEvent).filter_by(model_id=model_id)
            if event_type:
                query = query.filter_by(event_type=event_type)
            query = query.order_by(ResearchEmbedEvent.created_at.asc())
            if skip is not None:
                query = query.offset(skip)
            if limit is not None:
                query = query.limit(limit)
            return [ResearchEmbedEventModel.model_validate(e) for e in query.all()]

    def count_events_by_model_id(
        self, model_id: str, event_type: Optional[str] = None
    ) -> int:
        """Total row count for a model (ignoring pagination) -- the viewer
        uses this to render "X of Y" and disable the Next button correctly."""
        with get_db() as db:
            query = db.query(ResearchEmbedEvent).filter_by(model_id=model_id)
            if event_type:
                query = query.filter_by(event_type=event_type)
            return query.count()

    def delete_all_events(self) -> bool:
        with get_db() as db:
            db.query(ResearchEmbedEvent).delete()
            db.commit()
            return True


ResearchEmbedEvents = ResearchEmbedEventsTable()
