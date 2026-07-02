"""Add research_embed_event table

Revision ID: 670d7c5c0ffa
Revises: 3781e22d8b01
Create Date: 2026-07-02 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = "670d7c5c0ffa"
down_revision = "3781e22d8b01"
branch_labels = None
depends_on = None


def upgrade():
    # ### Create research_embed_event table ###
    op.create_table(
        "research_embed_event",
        sa.Column(
            "id", sa.Text(), primary_key=True
        ),  # Unique identifier for each event (TEXT type)
        sa.Column(
            "user_id", sa.Text(), nullable=True
        ),  # Participant's Open WebUI user id
        sa.Column(
            "chat_id", sa.Text(), nullable=True
        ),  # Which chat this event belongs to
        sa.Column(
            "event_type", sa.Text(), nullable=True
        ),  # "keystroke" | "temporal_delay" | "visibility" | "clipboard"
        sa.Column("data", sa.JSON(), nullable=True),  # Event-specific payload
        sa.Column(
            "client_timestamp", sa.BigInteger(), nullable=True
        ),  # When the browser says the event happened
        sa.Column(
            "created_at", sa.BigInteger(), nullable=False
        ),  # When this server received it (epoch)
    )
    op.create_index(
        "ix_research_embed_event_user_id",
        "research_embed_event",
        ["user_id"],
    )
    op.create_index(
        "ix_research_embed_event_chat_id",
        "research_embed_event",
        ["chat_id"],
    )
    op.create_index(
        "ix_research_embed_event_event_type",
        "research_embed_event",
        ["event_type"],
    )


def downgrade():
    # ### Drop research_embed_event table ###
    op.drop_index(
        "ix_research_embed_event_event_type", table_name="research_embed_event"
    )
    op.drop_index("ix_research_embed_event_chat_id", table_name="research_embed_event")
    op.drop_index("ix_research_embed_event_user_id", table_name="research_embed_event")
    op.drop_table("research_embed_event")
