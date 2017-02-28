import argparse
import subprocess
import xml.etree.ElementTree as ET
import os
import pwd
import re
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class DeviceQueryHandler(BaseHTTPRequestHandler):

    def parse_nvidia_xml(self, xml):
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
                            "username": self.owner(pid),
                            "name": process.find("process_name").text,
                            "used_memory": process.find("used_memory").text,
                        }
                        current_gpu_data["processes"].append(process_info)
            current_gpu_data["memory"] = memory
            gpu_data.append(current_gpu_data)
        return gpu_data

    def owner(self, pid):
        UID = 1
        for line in open('/proc/{}/status'.format(pid)):
            if line.startswith('Uid:'):
                uid = int(line.split()[UID])
                return pwd.getpwuid(uid).pw_name

    def do_GET(self):
        try:
            if not re.match(self.allowed_client, self.client_address[0]):
                self.send_error(403)
                return

            raw_gpu_data = subprocess.check_output(["nvidia-smi", "-x", "-q"]).decode('utf-8')
            gpu_data = self.parse_nvidia_xml(raw_gpu_data)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(bytes(json.dumps(gpu_data, indent=4), 'utf-8'))
        except Exception as e:
            self.send_error(500)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Tool that provides information about GPUs in this machine')
    parser.add_argument("-ac", "--allowed-client-address", default='.*', required=False, help="Restricts possible clients to given ip address")

    args = parser.parse_args()

    RestrictedDeviceQueryHandler = type('RestrictedDeviceQueryHandler', (DeviceQueryHandler,), dict(allowed_client=args.allowed_client_address))
    server = HTTPServer(("0.0.0.0", 12000), RestrictedDeviceQueryHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
