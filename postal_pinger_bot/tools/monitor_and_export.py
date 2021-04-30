import argparse
import csv
import logging
import os
import pathlib
from postal_pinger_bot.utils.general import db_init
import sys
import time
import yaml

# Setup logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)
# Enable console logging
logging_console_handler = logging.StreamHandler()
logging_formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
logging_console_handler.setFormatter(logging_formatter)
logger.addHandler(logging_console_handler)


def export_results(conn, field_names, output_dir: pathlib.Path):
    temp_results_path = output_dir / "temp-results.csv"
    results_path = output_dir / "results.csv"
    temp_results_by_fsa_path = output_dir / "temp-results-by-fsa.csv"
    results_by_fsa_path = output_dir / "results-by-fsa.csv"

    with conn.cursor() as cur:
        temp_raw_results_file = open(temp_results_path, 'w')
        temp_results_by_fsa_file = open(temp_results_by_fsa_path, 'w')

        results_writer = csv.writer(temp_raw_results_file, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        results_writer.writerow(field_names)

        last_fsa = ""

        cur.execute("SELECT * FROM ping_reg ORDER BY fsa")
        for row in cur:
            results_writer.writerow([row[field_name] for field_name in field_names])
            last_id = row["id"]

            if row["fsa"] != last_fsa:
                temp_results_by_fsa_file.write("=== {} ===\n".format(row["fsa"].upper()))
                last_fsa = row["fsa"]
            temp_results_by_fsa_file.write("@{}\n".format(row["username"]))

        temp_raw_results_file.close()
        temp_results_by_fsa_file.close()

    os.rename(temp_results_path, results_path)
    os.rename(temp_results_by_fsa_path, results_by_fsa_path)

    return last_id


def main(argv):
    args_parser = argparse.ArgumentParser(description="Script to monitor the registrations and export them in a human-readable form.")
    args_parser.add_argument("--config-path", help="Path to the config file.", required=True)
    parsed_args = args_parser.parse_args(argv[1:])

    config_path = pathlib.Path(parsed_args.config_path).resolve()

    # Load config
    with open(config_path, 'r') as config_file:
        config = yaml.safe_load(config_file)
    if config is None:
        raise Exception("Unable to parse configuration from {}.".format(config_path))

    monitoring_interval = config["monitoring_interval"]
    output_dir = pathlib.Path(config["export_output_dir"]).resolve()

    conn = db_init(config["db_config"])

    field_names = ["username", "user_id", "fsa", "created_at", "id"]

    last_id = -1
    while True:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as count FROM ping_reg WHERE id > %(last_id)s LIMIT 1", {"last_id": last_id})
                result = cur.fetchone()
                count = result["count"]
            if count > 0:
                last_id = export_results(conn, field_names, output_dir)

        time.sleep(monitoring_interval)


if "__main__" == __name__:
    sys.exit(main(sys.argv))
