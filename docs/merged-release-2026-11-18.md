# Integrated PV1 workflows

This change joins the local message-intake and adverse-event workflows with the
R3 clinical, revision, XML, local AS2 and operational-submission workflows already
present on main. Existing message registration, package previews, product matching,
partner contacts and product cards remain available.

## Schema and existing data

Revision `20261117_0037` joins `20261112_0030` and `20261112_0036`.
Keep both migration histories and run the ordinary `upgrade-db` command.
Do not use `stamp head`, replace an existing database with a demonstration
database, or initialize an empty database as a substitute for existing records.

`tools/working_data.py` can prepare one separate installation from an existing
offline SQLite database and its matching storage. It refuses to overwrite a
destination, checks the actual migration configuration, relocates disk paths
including package-manifest paths, and compares every original field and every
copied file. User credentials, identifiers, relationships and history are retained.
The source remains the rollback set; subsequent new target records are not
automatically synchronized back to it. Preexisting unresolved file references
are reported explicitly and do not become recovered files through migration.

The tool takes explicit paths; see `python tools/working_data.py --help`.
Stop and identify all source writers before using `--offline`, verify the result,
and keep the source installation until the migrated installation is accepted.

## Windows startup

For a new installation, follow README: install the locked dependencies, copy
`.env.example` to `.env`, create `data/storage`, explicitly initialize the new
database and create its administrator. The example environment and `start.cmd`
use `data/pv1.db` and `data/storage`. There is no default working password.

`start-site.ps1` checks an existing database and an existing listener; it does
not initialize, migrate or reset data during startup. Its default interpreter
is the clone's `.venv/Scripts/python.exe`. `PV1_PYTHON` can select another existing
interpreter without embedding a machine-specific path in the source.

The preview commands control only a separately prepared synthetic installation
in `output/demo`. They do not prepare or reset it automatically. Local AS2 remains
a synthetic loopback facility; this release does not enable production RZN exchange.

## Validation

The integrated runtime application, migrations and tests match the verified local
integration. A completed full pytest run had 1088 passed and 6 failed. The failures
were corrected in historical-schema and legacy-writer test fixtures, then the
affected suites were rerun. The latest outcomes across runs are 1095 passed,
with no remaining failed or skipped outcomes. There was no single all-passing
full-suite run. Deployment-specific data inventories and logs remain local.

The public checkout passed 42 targeted migration, workflow, attachment,
access-control and transfer-tool checks using isolated synthetic databases
(258.45 seconds). No production data was used by these tests.

```text
python -m pytest -q tests/test_merge_workflows.py tests/test_migrations.py
  tests/test_working_data.py tests/test_attachment_storage_paths.py
  tests/test_access_control_matrix.py
```

## Publication boundaries

This branch is based on the existing public main history. The unrelated local
snapshot history is not merged into it. Working databases, attachments, Access
exports, local deployment/audit reports, backup archives, secrets and one-off
scripts containing real regulatory identifiers are not included.
