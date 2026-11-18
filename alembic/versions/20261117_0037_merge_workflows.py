"""Join local message intake/AE card and R3 clinical/AS2 histories.

Both parents retain their original revision identifiers and data migrations.
"""

revision = "20261117_0037"
down_revision = ("20261112_0030", "20261112_0036")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back the integrated workflows.")
