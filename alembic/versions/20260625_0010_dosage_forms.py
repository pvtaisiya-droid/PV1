"""add dosage forms dictionary

Revision ID: 20260625_0010
Revises: 20260625_0009
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260625_0010"
down_revision = "20260625_0009"
branch_labels = None
depends_on = None


DOSAGE_FORM_ROWS = [
    (1, "таблетки подъязычные", "sublingual tablets", "таблетки", "tablets", 1, 1),
    (2, "таблетки шипучие", "effervescent tablets", "таблетки", "tablets", 1, 2),
    (3, "капсулы", "capsules", "капсулы", "capsules", 1, 3),
    (
        4,
        "лиофилизат для приготовления раствора для инфузий",
        "lyophilizate for solution for infusion",
        "лиофилизат",
        "lyophilizate",
        1,
        4,
    ),
    (
        5,
        "лиофилизат для приготовления раствора для внутрисосудистого и внутрипузырного введения",
        "lyophilizate for solution for intravascular and intravesical administration",
        "лиофилизат",
        "lyophilizate",
        1,
        5,
    ),
    (
        6,
        "порошок для приготовления раствора для приема внутрь",
        "powder for oral solution",
        "порошок",
        "powder",
        1,
        6,
    ),
    (7, "таблетки", "tablets", "таблетки", "tablets", 1, 7),
    (
        8,
        "таблетки, диспергируемые в полости рта",
        "orodispersible tablets",
        "таблетки",
        "tablets",
        1,
        8,
    ),
    (9, "гель для наружного применения", "gel for external use", "гель", "gel", 1, 9),
    (
        10,
        "таблетки, покрытые пленочной оболочкой",
        "film-coated tablets",
        "таблетки",
        "tablets",
        1,
        10,
    ),
    (11, "сироп", "syrup", "сироп", "syrup", 1, 11),
    (12, "суспензия для приема внутрь", "oral suspension", "суспензия", "suspension", 1, 12),
    (
        13,
        "порошок для приготовления раствора для внутривенного введения и ингаляций",
        "powder for solution for intravenous administration and inhalation",
        "порошок",
        "powder",
        1,
        13,
    ),
    (
        14,
        "раствор для внутривенного и подкожного введения",
        "solution for intravenous and subcutaneous administration",
        "раствор",
        "solution",
        1,
        14,
    ),
    (
        15,
        "порошок для приготовления раствора для инъекций/инфузий",
        "powder for solution for injection/infusion",
        "порошок",
        "powder",
        1,
        15,
    ),
    (
        16,
        "лиофилизированный порошок для приготовления раствора для внутривенного и внутримышечного введения",
        "lyophilized powder for solution for intravenous and intramuscular administration",
        "лиофилизированный порошок",
        "lyophilized powder",
        1,
        16,
    ),
]


def seed_dosage_forms(connection) -> None:
    statement = sa.text(
        """
        INSERT INTO dosage_forms
            (id, name_ru, name_en, group_ru, group_en, is_active, sort_order)
        SELECT
            :id, :name_ru, :name_en, :group_ru, :group_en, :is_active, :sort_order
        WHERE NOT EXISTS (
            SELECT 1 FROM dosage_forms WHERE id = :id
        )
        """
    )
    for row in DOSAGE_FORM_ROWS:
        connection.execute(
            statement,
            {
                "id": row[0],
                "name_ru": row[1],
                "name_en": row[2],
                "group_ru": row[3],
                "group_en": row[4],
                "is_active": row[5],
                "sort_order": row[6],
            },
        )


def link_existing_product_dosage_forms(connection) -> None:
    connection.execute(
        sa.text(
            """
            UPDATE "tblProducts"
            SET dosage_form_id = (
                SELECT id
                FROM dosage_forms
                WHERE lower(name_ru) = lower("tblProducts".dosage_form)
                   OR lower(name_en) = lower("tblProducts".dosage_form)
                ORDER BY sort_order
                LIMIT 1
            )
            WHERE dosage_form_id IS NULL
              AND dosage_form IS NOT NULL
              AND dosage_form <> ''
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE "tblProducts"
            SET dosage_form_id = 7,
                dosage_form = (SELECT name_ru FROM dosage_forms WHERE id = 7)
            WHERE dosage_form_id IS NULL
              AND lower(dosage_form) IN ('tablet', 'tablets')
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE "tblProducts"
            SET dosage_form_id = 3,
                dosage_form = (SELECT name_ru FROM dosage_forms WHERE id = 3)
            WHERE dosage_form_id IS NULL
              AND lower(dosage_form) IN ('capsule', 'capsules')
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("dosage_forms"):
        op.create_table(
            "dosage_forms",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name_ru", sa.Text(), nullable=False),
            sa.Column("name_en", sa.Text(), nullable=True),
            sa.Column("group_ru", sa.Text(), nullable=True),
            sa.Column("group_en", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=True),
        )

    seed_dosage_forms(bind)

    if inspector.has_table("tblProducts"):
        product_columns = {column["name"] for column in inspector.get_columns("tblProducts")}
        if "dosage_form_id" not in product_columns:
            op.add_column(
                "tblProducts",
                sa.Column("dosage_form_id", sa.Integer(), nullable=True),
            )

        if bind.dialect.name != "sqlite":
            foreign_keys = {
                tuple(key.get("constrained_columns") or [])
                for key in inspector.get_foreign_keys("tblProducts")
            }
            if ("dosage_form_id",) not in foreign_keys:
                op.create_foreign_key(
                    "fk_products_dosage_form_id",
                    "tblProducts",
                    "dosage_forms",
                    ["dosage_form_id"],
                    ["id"],
                )

        link_existing_product_dosage_forms(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("tblProducts"):
        columns = {column["name"] for column in inspector.get_columns("tblProducts")}
        if bind.dialect.name != "sqlite":
            for key in inspector.get_foreign_keys("tblProducts"):
                if key.get("name") == "fk_products_dosage_form_id":
                    op.drop_constraint(
                        "fk_products_dosage_form_id",
                        "tblProducts",
                        type_="foreignkey",
                    )
                    break
        if "dosage_form_id" in columns:
            op.drop_column("tblProducts", "dosage_form_id")
    if inspector.has_table("dosage_forms"):
        op.drop_table("dosage_forms")
