import json
import os
import sqlite3

DATABASE_NAME = os.getenv(
    "DATABASE_PATH",
    "incidents.db"
)

# Columns added after the original table was created.
# Older databases get these through ALTER TABLE below.
NEW_COLUMNS = {
    "endpoint": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'open'",
    "fingerprint": "TEXT",
    "resolved_at": "TEXT",
    "evidence_json": "TEXT",
}


def get_connection():
    # Create the database directory if it doesn't exist.
    directory = os.path.dirname(DATABASE_NAME)

    if directory:
        os.makedirs(directory, exist_ok=True)

    return sqlite3.connect(DATABASE_NAME)


def initialize_database():
    connection = get_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            incident_id TEXT PRIMARY KEY,
            detected_at TEXT NOT NULL,
            service TEXT NOT NULL,
            http_status INTEGER,
            message TEXT NOT NULL
        )
    """)

    # Add any missing columns to older databases.
    existing = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(incidents)"
        ).fetchall()
    }

    for name, definition in NEW_COLUMNS.items():
        if name not in existing:
            connection.execute(
                f"ALTER TABLE incidents ADD COLUMN {name} {definition}"
            )

    connection.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            deployment_id TEXT PRIMARY KEY,
            deployed_at TEXT NOT NULL,
            service TEXT NOT NULL,
            version TEXT NOT NULL,
            previous_version TEXT,
            deployed_by TEXT
        )
    """)

    connection.commit()
    connection.close()


def save_incident(incident):
    connection = get_connection()

    connection.execute("""
        INSERT INTO incidents (
            incident_id,
            detected_at,
            service,
            http_status,
            message,
            endpoint,
            fingerprint
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        incident["incident_id"],
        incident["detected_at"],
        incident["service"],
        incident.get("http_status"),
        incident["message"],
        incident.get("endpoint"),
        incident.get("fingerprint")
    ))

    connection.commit()
    connection.close()


def get_incidents():
    connection = get_connection()
    connection.row_factory = sqlite3.Row

    rows = connection.execute("""
        SELECT *
        FROM incidents
        ORDER BY detected_at DESC
    """).fetchall()

    incidents = [dict(row) for row in rows]
    connection.close()

    return incidents


def find_open_incident(fingerprint):
    """Return the newest open incident with this fingerprint, or None."""
    connection = get_connection()
    connection.row_factory = sqlite3.Row

    row = connection.execute("""
        SELECT *
        FROM incidents
        WHERE fingerprint = ? AND status = 'open'
        ORDER BY detected_at DESC
        LIMIT 1
    """, (fingerprint,)).fetchone()

    connection.close()

    return dict(row) if row else None


def update_incident_status(incident_id, status, resolved_at=None):
    connection = get_connection()

    connection.execute("""
        UPDATE incidents
        SET status = ?, resolved_at = ?
        WHERE incident_id = ?
    """, (status, resolved_at, incident_id))

    connection.commit()
    connection.close()


def save_evidence(incident_id, evidence):
    connection = get_connection()

    connection.execute("""
        UPDATE incidents
        SET evidence_json = ?
        WHERE incident_id = ?
    """, (json.dumps(evidence), incident_id))

    connection.commit()
    connection.close()


def save_deployment(deployment):
    connection = get_connection()

    connection.execute("""
        INSERT INTO deployments (
            deployment_id,
            deployed_at,
            service,
            version,
            previous_version,
            deployed_by
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        deployment["deployment_id"],
        deployment["deployed_at"],
        deployment["service"],
        deployment["version"],
        deployment.get("previous_version"),
        deployment.get("deployed_by")
    ))

    connection.commit()
    connection.close()


def get_deployments(limit=50):
    connection = get_connection()
    connection.row_factory = sqlite3.Row

    rows = connection.execute("""
        SELECT *
        FROM deployments
        ORDER BY deployed_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

    deployments = [dict(row) for row in rows]
    connection.close()

    return deployments