"""Add model_id to research_embed_event

Revision ID: 9f2e6d1c4a80
Revises: 670d7c5c0ffa
Create Date: 2026-07-08 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision = "9f2e6d1c4a80"
down_revision = "670d7c5c0ffa"
branch_labels = None
depends_on = None


def upgrade():
    # ### Add model_id column so events can be scoped to one study ###
    op.add_column(
        "research_embed_event",
        sa.Column(
            "model_id", sa.Text(), nullable=True
        ),  # Which model's research embed produced this event
    )
    op.create_index(
        "ix_research_embed_event_model_id",
        "research_embed_event",
        ["model_id"],
    )


def downgrade():
    # ### Drop model_id column ###
    op.drop_index(
        "ix_research_embed_event_model_id", table_name="research_embed_event"
    )
    op.drop_column("research_embed_event", "model_id")
