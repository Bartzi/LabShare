import argparse
import logging
import random
from unittest.mock import patch

from device_query import main


def parse_nvidia_xml(xml):
    memory = {
        "total": "12000 MiB",
        "used": f"{random.randint(0, 12000)} MiB",
        "free": f"{random.randint(0, 12000)} MiB",
    }

    processes = []
    num_processes = random.randint(0, 2)
    for i in range(num_processes):
        process_info = {
            "pid": random.randint(0, 10000),
            "username": "a user",
            "name": "computing",
            "used_memory": f"{random.randint(0, 12000)} MiB",
        }
        processes.append(process_info)

    gpu_data = [{
        "name": "NVIDIA Super Ultra",
        "uuid": "test123",
        "memory": memory,
        "gpu_util": f"{random.randint(0, 100)} %",
        "processes": processes,
        "in_use": "no" if num_processes == 0 else "yes"
    }]
    return gpu_data


def mocked_subprocess_run(*args, **kwargs):
    return "funny xml".encode('utf-8')


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

    with patch('device_query.parse_nvidia_xml', parse_nvidia_xml), patch('subprocess.check_output', mocked_subprocess_run):
        main(args)
