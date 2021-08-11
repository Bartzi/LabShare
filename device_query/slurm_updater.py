import argparse
import configparser
import json
import logging
import re

import subprocess
import sys
import time
import urllib

from typing import Dict, List

import requests

NODES_REGEX = re.compile(r"^(?P<prefix>.+)\[(?P<suffix>.*)]$")
MULTIPLE_NODES_REGEX = re.compile(r"^(?P<first>.*)-(?P<last>.*)$")
TWO_NODES_REGEX = re.compile(r"^(?P<first>.*),(?P<last>.*)$")
COMMA_NODES_REGEX = re.compile(r"(?P<gpu_id>\d+),*")
GPU_ID_REGEX = re.compile(r"\(IDX:(?P<gpu_ids>[-,\d]+|N/A)\)")


def extract_multi_match(match: re.Match, prefix: str = '') -> List[str]:
    matches = []
    first = int(match.group('first'))
    last = int(match.group('last'))
    num = last - first
    for i in range(num + 1):
        node_name = prefix + f"{first + i:02}"
        matches.append(node_name)
    return matches


def extract_comma_match(matched_items: List[str], prefix: str = '') -> List[str]:
    return [prefix + matched_item for matched_item in matched_items]


def parse_sinfo_output(sinfo_output: str) -> Dict[str, list]:
    lines = [stripped for line in sinfo_output.split('\n') if len(stripped := line.strip()) > 0]
    node_info = {}

    for item in lines:
        nodes, gpu_info = item.split()
        node_names = []
        match = NODES_REGEX.match(nodes)
        if match is None:
            # we have only one node
            node_names.append(nodes)
        else:
            node_prefix = match.group('prefix')
            nodes_match = MULTIPLE_NODES_REGEX.match(match.group('suffix'))
            if nodes_match is not None:
                # we have to handle multiple nodes
                node_names.extend(extract_multi_match(nodes_match, prefix=node_prefix))
            elif (comma_nodes_match := COMMA_NODES_REGEX.findall(match.group('suffix'))) is not None:
                node_names.extend(extract_comma_match(comma_nodes_match, prefix=node_prefix))
            else:
                logging.error(f"Could not determine node names from suffix: {match.group('suffix')}, full string: {nodes}")
                continue

        gpu_types = GPU_ID_REGEX.findall(gpu_info)
        all_gpu_ids = []
        for idx, gpu_id in enumerate(gpu_types):
            gpu_id_match = MULTIPLE_NODES_REGEX.match(gpu_id)
            gpu_ids = []
            if gpu_id_match is not None:
                # there are multiple gpus
                gpu_ids.extend(extract_multi_match(gpu_id_match))
            elif len((gpu_id_match := COMMA_NODES_REGEX.findall(gpu_id))) > 0:
                # there are comma separated GPUS
                gpu_ids.extend(extract_comma_match(gpu_id_match))
            else:
                if gpu_id != "N/A":
                    gpu_ids.append(gpu_id)
            all_gpu_ids.extend([idx + int(i) for i in gpu_ids])

        for node_name in node_names:
            node_info[node_name] = all_gpu_ids

    return node_info


def main(args: argparse.Namespace):
    config = configparser.ConfigParser()
    config.read("slurm_update.ini")

    server_base_url = config["MAIN"]["server_url"]
    server_url = urllib.parse.urljoin(server_base_url, "/gpu/allocations")
    update_interval = float(config["MAIN"]["update_interval"])

    auth_token = config["MAIN"]["token"]
    if auth_token == "":
        print("Authentication token must be manually set in config.ini file.")
        sys.exit(1)
    headers = {"Authorization": f"Token {auth_token}"}

    while True:
        try:
            sinfo_output = subprocess.check_output(["sinfo", "-O \"Nodelist,GresUsed:60\"", "-h"]).decode("utf-8")
            node_info = parse_sinfo_output(sinfo_output)

            post_data = json.dumps(node_info).encode("utf-8")
            logging.info("posting reservation data to server")
            response = requests.post(server_url, headers=headers, data=post_data, verify=args.verify)
            logging.info(f"Response from Server: {response.status_code} {response.reason}")
        except Exception as e:
            logging.error(f"Error: {e}")
        finally:
            time.sleep(update_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", default=False,
                        help="path to the certificate file that should be used to verify requests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Shows additional log messages")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(format="[%(asctime)s] %(message)s", level=logging.DEBUG)
    else:
        logging.basicConfig(format="[%(asctime)s] %(message)s")

    main(args)
