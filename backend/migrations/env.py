from alembic import context

from app.db import get_engine
from app.models import Base

with get_engine().connect() as connection:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        include_name=lambda name, type_, parent: (
            not (
                type_ == "table"
                and name in {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
            )
        ),
    )
    with context.begin_transaction():
        context.run_migrations()
