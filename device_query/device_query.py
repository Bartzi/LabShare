import subprocess
import xml.etree.ElementTree as ET
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class DeviceQueryHandler(BaseHTTPRequestHandler):

    def __parse_memory(self, memory_usage):
        return {
            "total": memory_usage.find("total").text,
            "used": memory_usage.find("used").text,
            "free": memory_usage.find("free").text,
        }

    def __parse_processes(self, procs):
        processes = []
        if procs.text == "N/A": return processes

        for process in procs.iter("process_info"):
            processes.append({
                "pid": process.find("pid").text,
                "process_name": process.find("process_name").text,
                "used_memory": process.find("used_memory").text,
            }   )
        return processes

    def parse_nvidia_xml(self, xml):
        gpu_data = []
        root = ET.fromstring(xml)
        for gpu in root.iter('gpu'):
            gpu_data.append({
                "name": gpu.find("product_name").text,
                "uuid": gpu.find("uuid").text,
                "memory": self.__parse_memory(gpu.find("fb_memory_usage")),
                "processes": self.__parse_processes(gpu.find("processes")),
            })

        return gpu_data

    def do_GET(self):
        try:
            raw_gpu_data = subprocess.check_output(["nvidia-smi", "-x", "-q"]).decode('utf-8')
            gpu_data = self.parse_nvidia_xml(raw_gpu_data)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(bytes(json.dumps(gpu_data, indent=4), 'utf-8'))
        except Exception as e:
            self.send_error(500, explain = str(e))


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 12000), DeviceQueryHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
