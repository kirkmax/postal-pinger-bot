import argparse
import csv
import discord
from discord.ext import commands
import datetime
import logging
import pathlib
import psycopg2
from psycopg2 import extras
import sys
from postal_pinger_bot.utils.general import db_init, get_unambiguous_username, parse_fsa, parse_username
import yaml

# Setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
# Enable console logging
logging_console_handler = logging.StreamHandler()
logging_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
logging_console_handler.setFormatter(logging_formatter)
logger.addHandler(logging_console_handler)


def main(argv):
    args_parser = argparse.ArgumentParser(description="Script to insert registrations from a spreadsheet.")
    args_parser.add_argument("--config-path", help="Path to the config file.", required=True)
    args_parser.add_argument("--spreadsheet-path", help="Path to the spreadsheet.", required=True)
    args_parser.add_argument("--guild-name", help="Name of the guild that members belong to.", required=True)
    parsed_args = args_parser.parse_args(argv[1:])

    config_path = pathlib.Path(parsed_args.config_path).resolve()
    spreadsheet_path = pathlib.Path(parsed_args.spreadsheet_path).resolve()
    guild_name = parsed_args.guild_name

    # Load config
    with open(config_path, 'r') as config_file:
        config = yaml.safe_load(config_file)
    if config is None:
        raise Exception("Unable to parse configuration from {}.".format(config_path))

    conn = db_init(config["db_config"])

    # Need the members intent to get users by username
    intents = discord.Intents.default()
    intents.members = True
    bot = commands.Bot(command_prefix="!pp", intents=intents)

    @bot.event
    async def on_ready():
        print(f'{bot.user.name} has connected to Discord!')

        # Find guild
        guild = None
        for guild in bot.guilds:
            if guild.name == guild_name:
                break
        if guild is None:
            raise Exception("Guild not found")

        # Parse spreadsheet of the form:
        # Date | FSA | FSA | ...
        # mm/dd/YYYY HH:MM:SS | @username#1234 | @username#1234 | ...
        FIRST_FSA_FIELD_IX = 1
        with open(spreadsheet_path) as f:
            r = csv.reader(f, delimiter=',', quotechar='"')
            field_names = next(r)

            fsas = []
            for field_name in field_names[FIRST_FSA_FIELD_IX:]:
                try:
                    fsa = parse_fsa(field_name)
                    fsas.append(fsa)
                except ValueError as ex:
                    logger.error("{} - {}".format(field_name, ex))
            if len(fsas) < len(field_names) - FIRST_FSA_FIELD_IX:
                logger.error("Quitting")
                return

            for csv_row in r:
                rows_to_insert = []
                for i, col in enumerate(csv_row):
                    if 0 == i:
                        # Parse timestamp
                        raw_timestamp = csv_row[0]
                        timestamp = datetime.datetime.strptime(raw_timestamp, "%m/%d/%Y %H:%M:%S")
                        created_at = timestamp.strftime("%Y-%m-%d %H:%M:%S")
                        continue

                    # Validate that this column has a field name
                    if i > len(field_names):
                        logger.error("{} has no corresponding FSA.".format(username))
                        continue
                    # Get fsa
                    fsa = fsas[i - FIRST_FSA_FIELD_IX]

                    if "" == col:
                        # Skip empty FSAs
                        continue
                    username = col

                    # Ensure username starts with an '@' and strip it
                    if username[0] != '@':
                        logger.error("{} - doesn't start with '@'".format(username))
                        continue
                    username = username[1:]

                    # Get user corresponding to username
                    try:
                        user = parse_username(username, guild)
                    except ValueError as ex:
                        logger.error("{} - {}".format(username, ex))
                        continue

                    rows_to_insert.append({"username": get_unambiguous_username(user), "user_id": user.id, "fsa": fsa, "created_at": created_at})

                    # Insert rows
                    with conn:
                        with conn.cursor() as cur:
                            psycopg2.extras.execute_values(cur, "INSERT INTO ping_reg VALUES %s ON CONFLICT DO NOTHING", rows_to_insert,
                                                           template="(%(username)s, %(user_id)s, %(fsa)s, %(created_at)s)")

    bot.run(config["discord_token"])


if "__main__" == __name__:
    sys.exit(main(sys.argv))
