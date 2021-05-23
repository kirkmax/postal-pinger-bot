import psycopg2
from psycopg2 import extras
import re

# Constants
MAX_FSAS_TO_PROCESS_AT_ONCE = 999


def db_init(db_config):
    # Use RealDictCursor to get named columns in results
    conn = psycopg2.connect(dbname=db_config["name"], user=db_config["user"], password=db_config["pass"], host=db_config["host"], port=db_config["port"],
                            cursor_factory=psycopg2.extras.RealDictCursor)

    with conn:
        with conn.cursor() as cur:
            fields = [
                "username TEXT NOT NULL",
                "user_id TEXT NOT NULL",
                "fsa TEXT NOT NULL",
                "created_at TIMESTAMP(0) DEFAULT CURRENT_TIMESTAMP",
                "id BIGSERIAL"
            ]
            # Create ping_reg table
            cur.execute("CREATE TABLE IF NOT EXISTS ping_reg({})".format(", ".join(fields)))

            # Create unique index
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_and_fsa ON ping_reg (user_id, fsa)")

            # Create ping_missing_reg table
            fields = [
                "user_id TEXT NOT NULL"
            ]
            cur.execute("CREATE TABLE IF NOT EXISTS ping_missing_reg({})".format(", ".join(fields)))

    return conn


def get_unambiguous_username(user):
    return "{}#{}".format(user.name, user.discriminator)


def parse_fsa(raw_fsa):
    """
    Validates and parses the given FSA
    :param raw_fsa:
    :return: Parsed FSA
    """
    fsa = raw_fsa.lower()

    # Validate FSA
    # NOTE: We don't print the user's input in case it's malicious
    if len(fsa) < 3:
        raise ValueError("One of the given area codes is too short.")
    elif len(fsa) > 3:
        raise ValueError("One of the given area codes is too long.")
    if not re.match("[a-z][0-9][a-z]", fsa):
        raise ValueError("One of the given area codes is invalid. It should look like 'K1P' (no quotes).")

    return fsa


def parse_fsas(raw_fsas):
    """
    Validates and parses FSAs from the given list
    :param raw_fsas:
    :return: Unique, parsed FSAs
    """
    fsas = []
    for raw_fsa in raw_fsas:
        if '' == raw_fsa:
            # Skip empty values
            continue

        fsa = parse_fsa(raw_fsa)
        fsas.append(fsa)

    if len(fsas) == 0:
        raise ValueError("No area codes provided.")

    unique_fsas = list(set(fsas))

    if len(unique_fsas) > MAX_FSAS_TO_PROCESS_AT_ONCE:
        raise ValueError("That's too many codes at once.")

    return unique_fsas


def parse_username(raw_username, guild):
    """
    Validates and parses the given username of the form 'user1#1001'
    :param raw_username:
    :param guild:
    :return: User matching the given username
    """
    # Validate username
    if not re.match("[^@#:`\s][^@#:`]{0,30}[^@#:`\s]#[0-9]{4}", raw_username):
        raise ValueError("Invalid username. It should look like 'user1#1001' (no quotes).")

    # Get user corresponding to username
    user = guild.get_member_named(raw_username)
    if user is None:
        raise ValueError("User not found.")

    return user
