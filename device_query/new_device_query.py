import configparser
import json
import pwd
from time import sleep

import requests
import xml.etree.ElementTree as ET


def parse_nvidia_xml(xml):
    gpu_data = []
    root = ET.fromstring(xml)

    for gpu in root.iter('gpu'):
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

        process_block = gpu.find("processes")
        if process_block.text == "N/A":
            current_gpu_data["in_use"] = "na"
        else:
            current_gpu_data["in_use"] = "no"
            current_gpu_data["processes"] = []
            for process in process_block.iter("process_info"):
                if process.find('type').text.lower() == "c":
                    current_gpu_data["in_use"] = "yes"
                    pid = process.find('pid').text
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
    for line in open('/proc/{}/status'.format(pid)):
        if line.startswith('Uid:'):
            uid = int(line.split()[UID])
            return pwd.getpwuid(uid).pw_name


def main():

    # TODO: how should this script be called? Cron job enough or dedicated logic better?
    server_url = "http://localhost:8000/gpu/update"  # TODO: https?
    post_interval = 5  # TODO: choose fitting value or make configurable
    device_name = "test_server"  # TODO: set dynamically

    # TODO: generate and write auth token into file somehow - setup function that pulls token from endpoint?
    config = configparser.ConfigParser()
    config.read("config.ini")
    auth_token = config["MAIN"]["token"]
    headers = {"Authorization": f"Token {auth_token}"}

    with open("nvidia-smi-output.xml", "r") as nvidia_data_file:
        raw_gpu_data = nvidia_data_file.read()
    # TODO properly parse
    # raw_gpu_data = subprocess.check_output(["nvidia-smi", "-x", "-q"]).decode('utf-8')
    gpu_data = parse_nvidia_xml(raw_gpu_data)

    post_data = {
        "gpu_data": gpu_data,
        "device_name": device_name
    }
    encoded_post_data = bytes(json.dumps(post_data, indent=4), 'utf-8')
    # while True:  # TODO
    for i in range(1):
        r = requests.post(server_url, headers=headers, data=encoded_post_data)
        sleep(post_interval)


if __name__ == '__main__':
    main()
