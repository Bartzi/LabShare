import configparser
import json
import pwd
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from time import sleep

import requests


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

        gpu_util= gpu.find("utilization").find("gpu_util").text
        current_gpu_data["gpu_util"] = gpu_util

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


# TODO: remove
def get_test_data(device_num):
    if device_num == 1:
        gpu_data = [
            {
                'name': 'Testy GPU',
                'uuid': 'loremuuuuuu',
                'memory': {
                    'total': '200 MB',
                    'used': '20 MB',
                    'free': '180 MB'
                },
                'gpu_util': '39 %',
                'in_use': 'yes',
                'processes': [{
                    'pid': 1,
                    'username': 'Mrs. Keksi',
                    'name': 'TestProcess',
                    'used_memory': '10 MB'
                }]
            },
            {
                'name': 'Testy GPU 2',
                'uuid': 'loremuuuu',
                'memory': {
                    'total': '100 MB',
                    'used': '0 MB',
                    'free': '100 MB'
                },
                'gpu_util': '0 %',
                'in_use': 'no',
                'processes': []
            }
        ]
        device_name = "test_device"
    elif device_num == 2:
        gpu_data = [
            {
                'name': 'Test GPU',
                'uuid': 'lorem',
                'memory': {
                    'total': '100 MB',
                    'used': '0 MB',
                    'free': '100 MB'
                },
                'gpu_util': '86 %',
                'in_use': 'no',
                'processes': []
            },
        ]
        device_name = "test_device2"
    else:
        gpu_data = [{
            'name': 'Toast GPU',
            'uuid': 'loremuuuuuu',
            'memory': {
                'total': '200 MB',
                'used': '20 MB',
                'free': '180 MB'
            },
            'gpu_util': '14',
            'in_use': 'yes',
            'processes': [{
                'pid': 1,
                'username': 'Mrs. Keksi',
                'name': 'TestProcess',
                'used_memory': '10 MB'
            }]
        }]
        device_name = "test_device3"
    return device_name, gpu_data


def main():
    # TODO: check if this still works with the provided service conf
    config = configparser.ConfigParser()
    config.read("config.ini")

    server_url = config["MAIN"]["server_url"]  # TODO: https - in the end
    update_interval = int(config["MAIN"]["update_interval"])
    device_name = config["MAIN"]["device_name"]

    auth_token = config["MAIN"]["token"]
    if auth_token == "":
        print("Authentication token must be manually set in config.ini file.")
        sys.exit(1)
    headers = {"Authorization": f"Token {auth_token}"}

    with open("nvidia-smi-output.xml", "r") as nvidia_data_file:
        raw_gpu_data = nvidia_data_file.read()
    # TODO: test proper parser
    # raw_gpu_data = subprocess.check_output(["nvidia-smi", "-x", "-q"]).decode('utf-8')
    gpu_data = parse_nvidia_xml(raw_gpu_data)

    #### for testing
    device_name, gpu_data = get_test_data(2)

    post_data = {
        "gpu_data": gpu_data,
        "device_name": device_name
    }
    encoded_post_data = bytes(json.dumps(post_data, indent=4), 'utf-8')
    while True:
        time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{time}] Sending request...")
        r = requests.post(server_url, headers=headers, data=encoded_post_data)
        time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{time}] Request returned {r.status_code} {r.reason}")
        sleep(update_interval)
        break  # TODO remove


if __name__ == '__main__':
    main()
