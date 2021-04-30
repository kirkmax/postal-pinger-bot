from .utils.general import MAX_FSAS_TO_PROCESS_AT_ONCE, db_init, get_unambiguous_username, parse_fsas, parse_username
import argparse
import discord
from discord.ext import commands
import logging
import pathlib
import psycopg2
from psycopg2 import extras
import sys
import yaml

# Setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
# Enable console logging
logging_console_handler = logging.StreamHandler()
logging_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
logging_console_handler.setFormatter(logging_formatter)
logger.addHandler(logging_console_handler)

# Constants
DISCORD_MESSAGE_LENGTH_LIMIT = 2000


def add_user_to_fsas(user, raw_fsas, conn):
    # Parse FSAs
    if len(raw_fsas) < 1:
        raise ValueError("Please provide an area code (ex: K1P).")
    fsas = parse_fsas(raw_fsas)

    # Assemble rows
    user_id = str(user.id)
    username = get_unambiguous_username(user)
    rows = []
    for fsa in fsas:
        rows.append({"username": username, "user_id": str(user_id), "fsa": fsa})

    # Insert rows
    with conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, "INSERT INTO ping_reg VALUES %s ON CONFLICT DO NOTHING", rows, template="(%(username)s, %(user_id)s, %(fsa)s)")


def del_user_from_fsas(user_id, raw_fsas, conn):
    # Parse FSAs
    if len(raw_fsas) < 1:
        raise ValueError("Please provide an area code (ex: K1P).")
    fsas = parse_fsas(raw_fsas)

    # Delete rows
    with conn:
        with conn.cursor() as cur:
            for fsa in fsas:
                cur.execute("DELETE FROM ping_reg WHERE user_id=%(user_id)s AND fsa=%(fsa)s", {"user_id": str(user_id), "fsa": fsa})


def purge_user(user_id, conn):
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ping_reg WHERE user_id=%(user_id)s", {"user_id": str(user_id)})


async def list_fsas_for_user(ctx, conn, user_id):
    with conn:
        with conn.cursor() as cur:
            fsas = []
            cur.execute("SELECT fsa FROM ping_reg WHERE user_id=%(user_id)s", {"user_id": str(user_id)})
            for row in cur:
                fsas.append(row["fsa"].upper())
                NUM_CHARS_PER_FSA = 4
                MAX_USER_ID_LENGTH = 30
                if len(fsas) >= DISCORD_MESSAGE_LENGTH_LIMIT / NUM_CHARS_PER_FSA - MAX_USER_ID_LENGTH:
                    await ctx.channel.send("{} {}".format(ctx.author.mention, ' '.join(fsas)))
                    fsas = []
            if len(fsas):
                await ctx.channel.send("{} {}".format(ctx.author.mention, ' '.join(fsas)))
                fsas = []


def main(argv):
    args_parser = argparse.ArgumentParser(description="Bot for pinging users in postal areas.")
    args_parser.add_argument("--config-path", help="Path to the config file.", required=True)
    parsed_args = args_parser.parse_args(argv[1:])

    config_path = pathlib.Path(parsed_args.config_path).resolve()

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

    @bot.command(name="add", help="Add me to pings for the given area (ex: K1P).", usage="area1 area2 ...")
    async def ppadd(ctx, *raw_fsas):
        try:
            add_user_to_fsas(ctx.author, raw_fsas, conn)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        await ctx.channel.send("{} You've been added to those areas!".format(ctx.author.mention))

    @bot.command(name="del", help="Delete me from pings for the given area (ex: K1P).", usage="area1 area2 ...")
    async def ppdel(ctx, *raw_fsas):
        try:
            del_user_from_fsas(ctx.author.id, raw_fsas, conn)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        await ctx.channel.send("{} You've been removed from those areas.".format(ctx.author.mention))

    @bot.command(name="stop", help="Delete me from all pings.")
    async def ppstop(ctx):
        purge_user(ctx.author.id, conn)

        await ctx.channel.send("{} You've been purged from the list.".format(ctx.author.mention))

    @bot.command(name="list", help="List my areas for pings.")
    async def pplist(ctx):
        await list_fsas_for_user(ctx, conn, ctx.author.id)

    @bot.command(name="useradd", help="Run 'add' for the given user (ex: user1#1001).", usage="user1#1001 area1 area2 ...")
    @commands.has_role("ppmod")
    async def ppuseradd(ctx, raw_username, *raw_fsas):
        try:
            # Validate username
            user = parse_username(raw_username, ctx.author.guild)

            add_user_to_fsas(user, raw_fsas, conn)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        await ctx.channel.send("{} User added to those areas.".format(ctx.author.mention))

    @bot.command(name="userdel", help="Run 'del' for the given user (ex: user1#1001).", usage="user1#1001 area1 area2 ...")
    @commands.has_role("ppmod")
    async def ppuserdel(ctx, raw_username, *raw_fsas):
        try:
            # Validate username
            user = parse_username(raw_username, ctx.author.guild)

            del_user_from_fsas(user.id, raw_fsas, conn)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        await ctx.channel.send("{} User has been removed from those areas.".format(ctx.author.mention))

    @bot.command(name="userstop", help="Run 'stop' for the given user (ex: user1#1001).", usage="user1#1001")
    @commands.has_role("ppmod")
    async def ppuserstop(ctx, raw_username):
        try:
            # Validate username
            user = parse_username(raw_username, ctx.author.guild)

            purge_user(user.id, conn)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        await ctx.channel.send("{} User has been purged from the list.".format(ctx.author.mention))

    @bot.command(name="userlist", help="Run 'list' for the given user (ex: user1#1001).", usage="user1#1001")
    @commands.has_role("ppmod")
    async def ppuserlist(ctx, raw_username):
        # Validate username
        try:
            user = parse_username(raw_username, ctx.author.guild)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        await list_fsas_for_user(ctx, conn, user.id)

    @bot.command(name="send", help="Ping the given area codes (ex: K1P).", usage="area1 area2 ...")
    @commands.has_role("ppmod")
    async def ppsend(ctx, *raw_fsas):
        if len(raw_fsas) < 1:
            await ctx.channel.send("{} Please provide an area code (ex: K1P).".format(ctx.author.mention))
            return
        try:
            fsas = parse_fsas(raw_fsas)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        if len(fsas) > MAX_FSAS_TO_PROCESS_AT_ONCE:
            await ctx.channel.send("{} Sorry, you're trying to ping too many area codes at once.".format(ctx.author.mention))
            return

        users = ""
        found_users_to_ping = False
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT user_id FROM ping_reg WHERE fsa IN %(fsas)s", {"fsas": tuple(fsas)})
                for row in cur:
                    found_users_to_ping = True
                    users += "<@{}> ".format(row["user_id"])
                    if len(users) > DISCORD_MESSAGE_LENGTH_LIMIT - 500:
                        # Flush the buffer before we reach discord's message length limit
                        await ctx.channel.send("{} is pinging {}!".format(ctx.author.mention, users))
                        users = ""
        # Ping any remaining users
        if "" != users:
            await ctx.channel.send("{} is pinging {}!".format(ctx.author.mention, users))

        if not found_users_to_ping:
            await ctx.channel.send("{} No one to ping.".format(ctx.author.mention))

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.errors.CheckFailure):
            await ctx.send("{} Sorry, you don't have the correct role for this command.".format(ctx.author.mention))
        elif isinstance(error, commands.errors.CommandNotFound):
            await ctx.send("{} Sorry, that command doesn't exist.".format(ctx.author.mention))
        else:
            logger.error(error)

    bot.run(config["discord_token"])


if "__main__" == __name__:
    sys.exit(main(sys.argv))
