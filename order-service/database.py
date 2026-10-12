
import os
import sqlite3

DATABASE_NAME = os.getenv(
    "DATABASE_PATH",
    "incidents.db"
)


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

    # Add the endpoint column to older databases if needed.
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(incidents)"
        ).fetchall()
    }

    if "endpoint" not in columns:
        connection.execute(
            "ALTER TABLE incidents ADD COLUMN endpoint TEXT"
        )

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
            endpoint
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        incident["incident_id"],
        incident["detected_at"],
        incident["service"],
        incident.get("http_status"),
        incident["message"],
        incident.get("endpoint")
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
