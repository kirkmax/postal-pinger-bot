from .utils.general import db_init, get_unambiguous_username, parse_fsas, parse_username
import argparse
import discord
from discord.ext import commands, tasks
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
COMMAND_PREFIX = "!pp"
DISCORD_MESSAGE_LENGTH_LIMIT = 2000
DISCORD_MESSAGE_LENGTH_HIGH_WATERMARK = DISCORD_MESSAGE_LENGTH_LIMIT - 500
# NOTE: Each FSA takes at 3 characters + 1 space in the message, so this is meant to be a value that doesn't overwhelm the message with FSAs
MAX_FSAS_TO_PING_AT_ONCE = 100


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
    found_fsa = False

    with conn:
        with conn.cursor() as cur:
            fsas = []
            cur.execute("SELECT fsa FROM ping_reg WHERE user_id=%(user_id)s", {"user_id": str(user_id)})
            for row in cur:
                found_fsa = True

                fsas.append(row["fsa"].upper())
                NUM_CHARS_PER_FSA = 4
                MAX_USER_ID_LENGTH = 30
                if len(fsas) >= DISCORD_MESSAGE_LENGTH_LIMIT / NUM_CHARS_PER_FSA - MAX_USER_ID_LENGTH:
                    await ctx.channel.send("{} {}".format(ctx.author.mention, ' '.join(fsas)))
                    fsas = []
            if len(fsas):
                await ctx.channel.send("{} {}".format(ctx.author.mention, ' '.join(fsas)))
                fsas = []

    return found_fsa


def find_missing_users(guilds, cur, missing_user_ids):
    for row in cur:
        user_id = row["user_id"]

        # NOTE: This is inefficient if the bot is used for only one guild but helps when the same bot instance is used both in production and on a testing
        #   server. Since it's an infrequent task, it's probably worth it overall.
        user_exists = False
        for guild in guilds:
            if guild.get_member(int(user_id)) is not None:
                user_exists = True
        if not user_exists:
            missing_user_ids.append(user_id)


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
    user_command_channel_name = config["user_command_channel"]
    delete_missing_users_interval = config["delete_missing_users_interval"]
    responses = config["responses"]

    # Need the members intent to get users by username
    intents = discord.Intents.default()
    intents.members = True
    bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)

    @bot.event
    async def on_ready():
        print(f'{bot.user.name} has connected to Discord!')

    @bot.event
    async def on_message(message):
        if message.author == bot.user:
            # Ignore messages from the bot
            return

        if user_command_channel_name != message.channel.name:
            if not message.content.startswith(COMMAND_PREFIX):
                # Ignore non-command messages
                return

            if not message.author.permissions_in(message.channel).kick_members:
                # Ignore commands from non-moderators
                return
        else:
            if not message.content.startswith(COMMAND_PREFIX):
                # Delete non-command messages
                try:
                    await message.delete()
                except discord.NotFound:
                    # Message already deleted
                    pass
                return

        await bot.process_commands(message)

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
        found_fsa = await list_fsas_for_user(ctx, conn, ctx.author.id)
        if not found_fsa:
            await ctx.channel.send("{} You're not in the list.".format(ctx.author.mention))

    @bot.command(name="help", help="Show this message.")
    async def pphelp(ctx):
        await ctx.channel.send(responses["user_help"])

    @bot.command(name="useradd", help="Run 'add' for the given user (ex: user1#1001).", usage="user1#1001 area1 area2 ...")
    @commands.has_permissions(kick_members=True)
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
    @commands.has_permissions(kick_members=True)
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
    @commands.has_permissions(kick_members=True)
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
    @commands.has_permissions(kick_members=True)
    async def ppuserlist(ctx, raw_username):
        # Validate username
        try:
            user = parse_username(raw_username, ctx.author.guild)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        found_fsa = await list_fsas_for_user(ctx, conn, user.id)
        if not found_fsa:
            await ctx.channel.send("{} User not in list.".format(ctx.author.mention))

    @bot.command(name="send", help="Ping the given area codes (ex: K1P).", usage="area1 area2 ...")
    @commands.has_permissions(kick_members=True)
    async def ppsend(ctx, *raw_fsas):
        if len(raw_fsas) < 1:
            await ctx.channel.send("{} Please provide an area code (ex: K1P).".format(ctx.author.mention))
            return
        try:
            fsas = parse_fsas(raw_fsas)
        except ValueError as ex:
            await ctx.channel.send("{} {}".format(ctx.author.mention, str(ex)))
            return

        if len(fsas) > MAX_FSAS_TO_PING_AT_ONCE:
            await ctx.channel.send("{} Sorry, you're trying to ping too many area codes at once.".format(ctx.author.mention))
            return

        message_prefix = "New info for {} is here! Check the pins! ".format(" ".join(sorted([fsa.upper() for fsa in fsas])))
        message = message_prefix
        found_users_to_ping = False
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT user_id FROM ping_reg WHERE fsa IN %(fsas)s", {"fsas": tuple(fsas)})
                for row in cur:
                    found_users_to_ping = True
                    message += "<@{}> ".format(row["user_id"])
                    if len(message) > DISCORD_MESSAGE_LENGTH_HIGH_WATERMARK:
                        # Flush the buffer before we reach discord's message length limit
                        await ctx.channel.send(message)
                        message = message_prefix
        # Ping any remaining users
        if len(message) > len(message_prefix):
            await ctx.channel.send(message)

        if not found_users_to_ping:
            await ctx.channel.send("{} No one to ping.".format(ctx.author.mention))

    @bot.command(name="modhelp", help="Show this message.")
    @commands.has_permissions(kick_members=True)
    async def ppmodhelp(ctx):
        await ctx.channel.send(responses["mod_help"])

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.errors.CheckFailure):
            await ctx.send("{} Sorry, you're not allowed to use this command.".format(ctx.author.mention))
        elif isinstance(error, commands.errors.CommandNotFound):
            # NOTE: We delete the message to prevent users from getting around the non-command deletion rule
            await ctx.message.delete()
            await ctx.send("{} Sorry, that command doesn't exist.".format(ctx.author.mention))
        elif isinstance(error, commands.errors.MissingRequiredArgument):
            await ctx.send("{} Command requires a parameter.".format(ctx.author.mention))
        else:
            logger.error(error)

    @tasks.loop(hours=delete_missing_users_interval["hours"], minutes=delete_missing_users_interval["minutes"],
                seconds=delete_missing_users_interval["seconds"])
    async def remove_missing_users():
        if not bot.is_ready():
            # Wait until the bot is connected
            return

        try:
            # Get users that are still missing
            confirmed_missing_user_ids = []
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT user_id FROM ping_missing_reg")
                    find_missing_users(bot.guilds, cur, confirmed_missing_user_ids)

            # Remove missing users from ping_reg and clear ping_missing_reg table
            with conn:
                with conn.cursor() as cur:
                    for user_id in confirmed_missing_user_ids:
                        cur.execute("DELETE FROM ping_reg WHERE user_id = %s", (user_id,))
                    cur.execute("DELETE FROM ping_missing_reg")

            # Find currently missing users
            missing_user_ids = []
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT user_id FROM ping_reg")
                    find_missing_users(bot.guilds, cur, missing_user_ids)

            # Save currently missing users
            with conn:
                with conn.cursor() as cur:
                    for user_id in missing_user_ids:
                        cur.execute("INSERT INTO ping_missing_reg VALUES (%s)", (user_id,))
        except Exception:
            logger.exception("Exception during remove_missing_users.")

    remove_missing_users.start()
    bot.run(config["discord_token"])


if "__main__" == __name__:
    sys.exit(main(sys.argv))
