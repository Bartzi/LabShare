import argparse
import logging
import random
from unittest.mock import patch

from slurm_updater import main


SINFO_OUTPUT = """
fb10dl[03,06,11]    gpu:1080ti:1(IDX:{gpu_ids})                                         
fb10dl[04-05]       gpu:titanx:1(IDX:0)                                         
fb10dl07            gpu:1080ti:4(IDX:0-3)                                       
fb10dl[{node_ids}]       gpu:2080ti:1(IDX:0)                                         
resterampe          gpu:1080ti:1(IDX:0),gpu:980gtx:0(IDX:N/A)                   
fb10dl09            gpu:3090:0(IDX:N/A)
fb10dl10            gpu:4090:3(IDX:0,2,4)
test                gpu:kekse:1(IDX:{test_ids})
"""


def build_random_ids(add_padding: bool = True) -> str:
    delimiter = random.choice([",", "-", "N/A"])
    if delimiter == ",":
        num_ids = random.randint(0, 6)
        if add_padding:
            ids = ",".join(f"{i:02}" for i in range(num_ids))
        else:
            ids = ",".join(f"{i}" for i in range(num_ids))
    elif delimiter == "-":
        first = random.randint(0, 5)
        last = random.randint(first + 1, 8)
        if add_padding:
            ids = f"{first:02}-{last:02}"
        else:
            ids = f"{first}-{last}"
    else:
        ids = delimiter
    return ids


def mocked_subprocess_run(*args, **kwargs):
    gpu_ids = build_random_ids(add_padding=False)
    node_ids = build_random_ids()
    return SINFO_OUTPUT.format(gpu_ids=gpu_ids, node_ids=node_ids, test_ids=random.choice(["0", "N/A"])).encode("utf-8")


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

    with patch('subprocess.check_output', mocked_subprocess_run):
        main(args)
