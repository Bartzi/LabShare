import argparse
import configparser
import json
import logging
import pwd
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from time import sleep

import requests


def parse_nvidia_xml(xml):
    gpu_data = []
    root = ET.fromstring(xml)

    for gpu in root.iter("gpu"):
        current_gpu_data = {
            "name": gpu.find("product_name").text,
            "uuid": gpu.find("uuid").text,
        }

        memory_usage = gpu.find("fb_memory_usage")
        memory = {
            "total": memory_usage.find("total").text,
            "used": memory_usage.find("used").text,
            "free": memory_usage.find("free").text,
        }

        gpu_util= gpu.find("utilization").find("gpu_util").text
        current_gpu_data["gpu_util"] = gpu_util

        process_block = gpu.find("processes")
        if process_block.text == "N/A":
            current_gpu_data["in_use"] = "na"
        else:
            current_gpu_data["in_use"] = "no"
            current_gpu_data["processes"] = []
            for process in process_block.iter("process_info"):
                if process.find("type").text.lower() == "c":
                    current_gpu_data["in_use"] = "yes"
                    pid = process.find("pid").text
                    process_info = {
                        "pid": pid,
                        "username": get_owner_for_pid(pid),
                        "name": process.find("process_name").text,
                        "used_memory": process.find("used_memory").text,
                    }
                    current_gpu_data["processes"].append(process_info)
        current_gpu_data["memory"] = memory
        gpu_data.append(current_gpu_data)
    return gpu_data


def get_owner_for_pid(pid):
    UID = 1
    for line in open("/proc/{}/status".format(pid)):
        if line.startswith("Uid:"):
            uid = int(line.split()[UID])
            return pwd.getpwuid(uid).pw_name


def main(args):
    config = configparser.ConfigParser()
    config.read("config.ini")

    server_base_url = config["MAIN"]["server_url"]
    server_url = server_base_url + "/gpu/update"
    update_interval = float(config["MAIN"]["update_interval"])
    device_name = config["MAIN"]["device_name"]

    auth_token = config["MAIN"]["token"]
    if auth_token == "":
        print("Authentication token must be manually set in config.ini file.")
        sys.exit(1)
    headers = {"Authorization": f"Token {auth_token}"}

    while True:
        try:
            raw_gpu_data = subprocess.check_output(["nvidia-smi", "-x", "-q"]).decode("utf-8")
            gpu_data = parse_nvidia_xml(raw_gpu_data)

            post_data = {
                "gpu_data": gpu_data,
                "device_name": device_name
            }
            encoded_post_data = bytes(json.dumps(post_data, indent=4), "utf-8")

            logging.info(f"Sending request...")

            if args.verify is not None:
                r = requests.post(server_url, headers=headers, data=encoded_post_data, verify=args.verify)
            else:
                r = requests.post(server_url, headers=headers, data=encoded_post_data, verify=False)

            logging.info(f"Request returned {r.status_code} {r.reason}")
        except Exception as e:
            logging.error(f"Error: {e}")
        finally:
            sleep(update_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", help="path to the certificate file that should be used to verify requests")
    parser.add_argument("-v", "--verbose", action="store_true", help="Shows additional log messages")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(format="[%(asctime)s] %(message)s", level=logging.DEBUG)
    else:
        logging.basicConfig(format="[%(asctime)s] %(message)s")

    main(args)
