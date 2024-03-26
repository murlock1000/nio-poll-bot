import logging
from typing import Any, Dict

# The latest migration version of the database.
#
# Database migrations are applied starting from the number specified in the database's
# `migration_version` table + 1 (or from 0 if this table does not yet exist) up until
# the version specified here.
#
# When a migration is performed, the `migration_version` table should be incremented.
latest_migration_version = 1

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, database_config: Dict[str, str]):
        """Setup the database.

        Runs an initial setup or migrations depending on whether a database file has already
        been created.

        Args:
            database_config: a dictionary containing the following keys:
                * type: A string, one of "sqlite" or "postgres".
                * connection_string: A string, featuring a connection string that
                    be fed to each respective db library's `connect` method.
        """
        self.conn = self._get_database_connection(
            database_config["type"], database_config["connection_string"]
        )
        self.cursor = self.conn.cursor()
        self.db_type = database_config["type"]

        # Try to check the current migration version
        migration_level = 0
        try:
            self._execute("SELECT version FROM migration_version")
            row = self.cursor.fetchone()
            migration_level = row[0]
        except Exception:
            self._initial_setup()
        finally:
            if migration_level < latest_migration_version:
                self._run_migrations(migration_level)

        logger.info(f"Database initialization of type '{self.db_type}' complete")

    def _get_database_connection(
        self, database_type: str, connection_string: str
    ) -> Any:
        """Creates and returns a connection to the database"""
        if database_type == "sqlite":
            import sqlite3

            # Initialize a connection to the database, with autocommit on
            return sqlite3.connect(connection_string, isolation_level=None)
        elif database_type == "postgres":
            import psycopg2

            conn = psycopg2.connect(connection_string)

            # Autocommit on
            conn.set_isolation_level(0)

            return conn

    def _initial_setup(self) -> None:
        """Initial setup of the database"""
        logger.info("Performing initial database setup...")

        # Set up the migration_version table
        self._execute(
            """
            CREATE TABLE migration_version (
                version INTEGER PRIMARY KEY
            );
        """
        )

        # Initially set the migration version to 0
        self._execute(
            """
            INSERT INTO migration_version (
                version
            ) VALUES (?);
        """,
            (0,),
        )

        # Set up any other necessary database tables here

        logger.info("Database setup complete")

    def _run_migrations(self, current_migration_version: int) -> None:
        """Execute database migrations. Migrates the database to the
        `latest_migration_version`.

        Args:
            current_migration_version: The migration version that the database is
                currently at.
        """
        logger.debug("Checking for necessary database migrations...")

        if current_migration_version < 1:
            logger.info("Migrating the database from v0 to v1...")
              # Create table for polls
            self._execute(
            """
            CREATE TABLE IF NOT EXISTS polls (
  room_id VARCHAR(80) NOT NULL,
  event_id VARCHAR(80) NOT NULL,
  topic TEXT NOT NULL,
  kind VARCHAR(80) NOT NULL,
  reply_event_id VARCHAR(80),
  PRIMARY KEY (room_id, event_id));
            """
        )
        # Create table for available answers to a poll
            self._execute(
            """
            CREATE TABLE IF NOT EXISTS answers (
  answer VARCHAR(80) NOT NULL,
  answer_hash VARCHAR(80) NOT NULL,
  room_id VARCHAR(80) NOT NULL,
  reference_id VARCHAR(80) NOT NULL,
  PRIMARY KEY (room_id, reference_id, answer_hash),
  CONSTRAINT poll_id
    FOREIGN KEY (room_id , reference_id)
    REFERENCES polls (room_id , event_id)
    ON DELETE CASCADE
    ON UPDATE NO ACTION);
            """
        )
        # Create a table for users who have voted on a poll and their vote
            self._execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
  response VARCHAR(80) NOT NULL,
  "user" VARCHAR(80) NOT NULL,
  room_id VARCHAR(80) NOT NULL,
  reference_id VARCHAR(80) NOT NULL,
  PRIMARY KEY (room_id, reference_id, "user"),
  CONSTRAINT answer_id
    FOREIGN KEY (room_id, reference_id, response)
    REFERENCES answers (room_id, reference_id, answer_hash)
    ON DELETE CASCADE
    ON UPDATE NO ACTION);
            """
        )
        
            # Update the stored migration version
            current_migration_version += 1
            self._execute("UPDATE migration_version SET version = ?", (current_migration_version,))

            logger.info(f"Database migrated to v{current_migration_version}")

    def _execute(self, *args) -> None:
        """A wrapper around cursor.execute that transforms placeholder ?'s to %s for postgres.

        This allows for the support of queries that are compatible with both postgres and sqlite.

        Args:
            args: Arguments passed to cursor.execute.
        """
        if self.db_type == "postgres":
            self.cursor.execute(args[0].replace("?", "%s"), *args[1:])
        else:
            self.cursor.execute(*args)

    def create_poll(self, room_id, event_id, topic, kind):
        """Create a poll in the database."""
        logger.debug(
            f"Creating new poll `{topic}` in room {room_id}, event {event_id}"
        )
        self._execute(
            """
            INSERT INTO polls (
                room_id,
                event_id,
                topic,
                kind
            ) VALUES (?, ?, ?, ?)
        """,
            (room_id, event_id, topic, kind),
        )
    
    def get_poll(self, room_id, event_id):
        """Get the poll from the database."""
        logger.debug(
            f"Getting poll in room {room_id}, event {event_id}"
        )
        self._execute(
            """
            SELECT * FROM polls WHERE room_id = ? AND event_id = ?
        """,
            (room_id, event_id),
        )
        return self.cursor.fetchone()
    # MYSQL queries for deleting answers/responses that are no longer have an active poll. (sqlite fk ondelete cascade constraint doesn't work?)
    # DELETE FROM answers WHERE (room_id,reference_id) IN (SELECT t1.room_id,t1.reference_id FROM answers AS t1 LEFT JOIN polls AS t2 ON t1.room_id = t2.room_id AND t1.reference_id = t2.event_id WHERE t2.event_id is null OR t2.room_id is null);
    # DELETE FROM responses WHERE (room_id,reference_id) IN (SELECT t1.room_id,t1.reference_id FROM responses AS t1 LEFT JOIN polls AS t2 ON t1.room_id = t2.room_id AND t1.reference_id = t2.event_id WHERE t2.event_id is null OR t2.room_id is null);
    def delete_poll(self, room_id, event_id):
        """Delete the poll from the database."""
        logger.debug(
            f"Deleting poll in room {room_id}, event {event_id}"
        )
        self._execute(
            """
            DELETE FROM polls WHERE room_id = ? AND event_id = ?
        """,
            (room_id, event_id),
        )
        self._execute(
            """
            DELETE FROM answers WHERE room_id = ? AND reference_id = ?
        """,
            (room_id, event_id),
        )
        self._execute(
            """
            DELETE FROM responses WHERE room_id = ? AND reference_id = ?
        """,
            (room_id, event_id),
        )

    def update_reply_event_id_in_poll(self, room_id, event_id, reply_event_id):
        """Update the reply event id in the database."""
        logger.debug(
            f"Updating reply event id in room {room_id}, event {event_id}"
        )
        self._execute(
            """
            UPDATE polls SET reply_event_id = ? WHERE room_id = ? AND event_id = ?
        """,
            (reply_event_id, room_id, event_id),
        )
    
    def get_reply_event(self, room_id, event_id):
        """Get the reply event from the database."""
        logger.debug(
            f"Getting reply event in room {room_id}, event {event_id}"
        )
        self._execute(
            """
            SELECT * FROM polls WHERE room_id = ? AND event_id = ?
        """,
            (room_id, event_id),
        )
        return self.cursor.fetchone()

    def add_answer(self, answer, answer_hash, room_id, reference_id):
        """Add an answer to the database."""
        logger.debug(
            f"Adding answer {answer} to room {room_id}, reference event {reference_id}"
        )
        self._execute(
            """
            INSERT INTO answers (
                answer,
                answer_hash,
                room_id,
                reference_id
            ) VALUES (?, ?, ?, ?)
        """,
            (answer, answer_hash, room_id, reference_id),
        )

    def get_answers(self, room_id, reference_id):
        """Get the answers from the database."""
        logger.debug(
            f"Getting answers in room {room_id}, reference event {reference_id}"
        )
        self._execute(
            """
            SELECT * FROM answers WHERE room_id = ? AND reference_id = ?
        """,
            (room_id, reference_id),
        )
        return self.cursor.fetchall()

    def get_responses(self, room_id, reference_id):
        """Get the response from the database."""
        logger.debug(
            f"Getting responses for poll in room {room_id}, reference event {reference_id}"
        )
        self._execute(
            """
            SELECT * FROM responses WHERE room_id = ? AND reference_id = ?
        """,
            (room_id, reference_id),
        )
        return self.cursor.fetchall()

    def create_or_update_response(self, response, user, room_id, reference_id):
        """Create or update a response in the database."""
        logger.debug(
            f"Creating or updating {user} response {response} in room {room_id}, reference event {reference_id}"
        )
        self._execute(
            """
            INSERT INTO responses (
                response,
                user,
                room_id,
                reference_id
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT (user, room_id, reference_id) DO UPDATE SET response = ?
        """,
            (response, user, room_id, reference_id, response),
        )
