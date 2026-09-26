from neo4j import GraphDatabase
from backend.config import get_settings


class Neo4jConnection:
    _driver = None

    @classmethod
    def get_driver(cls):
        if cls._driver is None:
            settings = get_settings()
            cls._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        return cls._driver

    @classmethod
    def close(cls):
        if cls._driver is not None:
            cls._driver.close()
            cls._driver = None

    @classmethod
    def get_session(cls):
        driver = cls.get_driver()
        settings = get_settings()
        return driver.session(database=settings.neo4j_database)

    @classmethod
    def execute_query(cls, query, parameters=None):
        with cls.get_session() as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    @classmethod
    def execute_write(cls, query, parameters=None):
        with cls.get_session() as session:
            session.execute_write(
                lambda tx: tx.run(query, parameters or {})
            )

    @classmethod
    def execute_write_transaction(cls, queries_and_params):
        """Execute multiple write queries in a single transaction.
        queries_and_params: list of (query, parameters) tuples.
        All succeed or all fail.
        """
        def _tx_fn(tx):
            for query, params in queries_and_params:
                tx.run(query, params or {})

        with cls.get_session() as session:
            session.execute_write(_tx_fn)
