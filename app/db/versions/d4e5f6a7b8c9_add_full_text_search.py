"""Add full-text search columns to documents

Revision ID: d4e5f6a7b8c9
Revises: c9fed60fda5a
Create Date: 2024-01-02 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: str | None = 'c9fed60fda5a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add search columns to documents
    op.add_column(
        'documents',
        sa.Column('search_text', sa.Text(), nullable=True),
    )
    op.add_column(
        'documents',
        sa.Column('search_vector', postgresql.TSVECTOR(), nullable=True),
    )

    # GIN index for fast full-text search
    op.create_index(
        'ix_documents_search_vector',
        'documents',
        [sa.text('search_vector')],
        postgresql_using='gin',
    )

    # Trigger function to keep search_vector in sync with search_text
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_document_search_vector()
        RETURNS trigger AS $$
        BEGIN
            new.search_vector :=
                setweight(to_tsvector('english', coalesce(new.filename, '')), 'A')
              || setweight(to_tsvector('english', coalesce(new.search_text, '')), 'B');
            RETURN new;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER documents_search_vector_trigger
        BEFORE INSERT OR UPDATE ON documents
        FOR EACH ROW
        EXECUTE FUNCTION update_document_search_vector();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS documents_search_vector_trigger ON documents")
    op.execute("DROP FUNCTION IF EXISTS update_document_search_vector()")
    op.drop_index('ix_documents_search_vector', table_name='documents')
    op.drop_column('documents', 'search_vector')
    op.drop_column('documents', 'search_text')
