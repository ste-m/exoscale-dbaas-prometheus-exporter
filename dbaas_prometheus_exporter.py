import json
import requests
import time
import hashlib
import hmac
import os
import logging
from base64 import standard_b64encode
from prometheus_client import start_http_server, Gauge
from requests.auth import AuthBase
from urllib.parse import parse_qs, urlparse

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Set your API keys and secrets as environment variables
api_key = os.environ.get('exoscale_key')
api_secret = os.environ.get('exoscale_secret')

#database name to scrape
database_names_str = os.environ.get('database_names')

# Check if the environment variables are set
if api_key is None or api_secret is None or api_key == "" or api_secret == "":
    logger.error("Error: Please set the 'exoscale_key' and 'exoscale_secret' environment variables.")
    exit(1)

if database_names_str is None or database_names_str == "":
    logger.error("Error: Please set the 'database_names' environment variables.")
    exit(1)

#split database names
database_names = database_names_str.split(',')

# Define Prometheus gauge metrics for each metric with a 'database' label
dbaas_disk_usage = Gauge('dbaas_disk_usage', 'Disk space usage percentage', ['database'])
dbaas_load_average = Gauge('dbaas_load_average', 'Load average (5 min)', ['database'])
dbaas_mem_usage = Gauge('dbaas_memory_usage', 'Memory usage percentage', ['database'])
dbaas_diskio_writes = Gauge('dbaas_disk_io_writes', 'Disk IOPS (writes)', ['database'])
dbaas_mem_available = Gauge('dbaas_memory_available', 'Memory available percentage', ['database'])
dbaas_cpu_usage = Gauge('dbaas_cpu_usage', 'CPU usage percentage', ['database'])
dbaas_diskio_reads = Gauge('dbaas_disk_io_reads', 'Disk IOPS (reads)', ['database'])
dbaas_net_send = Gauge('dbaas_network_transmit_bytes_per_sec', 'Network transmit (bytes/s)', ['database'])
dbaas_net_receive = Gauge('dbaas_network_receive_bytes_per_sec', 'Network receive (bytes/s)', ['database'])

# Exoscale API endpoint for metrics
exoscale_api_base_url = "https://api-de-muc-1.exoscale.com/v2/dbaas-service-metrics/"


class ExoscaleV2Auth(AuthBase):
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret.encode('utf-8')

    def __call__(self, request):
        expiration_ts = int(time.time() + 10 * 60)
        self._sign_request(request, expiration_ts)
        return request

    def _sign_request(self, request, expiration_ts):
        auth_header = 'EXO2-HMAC-SHA256 credential={}'.format(self.key)
        msg_parts = []

        # Request method/URL path
        msg_parts.append('{method} {path}'.format(
            method=request.method, path=urlparse(request.url).path
        ).encode('utf-8'))

        # Request body
        msg_parts.append(request.body if request.body else b'')

        # Request query string parameters
        # Important: this is order-sensitive, we have to have to sort
        # parameters alphabetically to ensure signed # values match the
        # names listed in the 'signed-query-args=' signature pragma.
        params = parse_qs(urlparse(request.url).query)
        signed_params = sorted(params)
        params_values = []
        for p in signed_params:
            if len(params[p]) != 1:
                continue
            params_values.append(params[p][0])
        msg_parts.append(''.join(params_values).encode('utf-8'))
        if signed_params:
            auth_header += ',signed-query-args={}'.format(';'.join(signed_params))

        # Request headers -- none at the moment
        # Note: the same order-sensitive caution for query string parameters
        # applies to headers.
        msg_parts.append(b'')

        # Request expiration date (UNIX timestamp)
        msg_parts.append(str(expiration_ts).encode('utf-8'))
        auth_header += ',expires=' + str(expiration_ts)

        msg = b'\n'.join(msg_parts)
        signature = hmac.new(
            self.secret, msg=msg, digestmod=hashlib.sha256
        ).digest()

        auth_header += ',signature=' + str(
            standard_b64encode(bytes(signature)), 'utf-8'
        )

        request.headers['Authorization'] = auth_header

# Create an authentication object
auth = ExoscaleV2Auth(api_key, api_secret)

# Request body
request_body = {"period": "hour"}



def fetch_metrics(database_names):
    while True:
        try:
            for database_name in database_names:
                # Construct the API URL for the specific database
                exoscale_api_url = exoscale_api_base_url + database_name

                # Make an HTTP POST request to the Exoscale API to retrieve metrics
                response = requests.post(exoscale_api_url, json=request_body, headers={"Content-Type": "application/json"}, auth=auth)

                if response.status_code == 200:
                    metrics_data = response.json()

                    # Extract the latest metric data for each metric
                    latest_disk_usage = metrics_data['metrics']['disk_usage']['data']['rows'][-1][1]
                    latest_load_average = metrics_data['metrics']['load_average']['data']['rows'][-1][1]
                    latest_mem_usage = metrics_data['metrics']['mem_usage']['data']['rows'][-1][1]
                    latest_diskio_writes = metrics_data['metrics']['diskio_writes']['data']['rows'][-1][1]
                    latest_mem_available = metrics_data['metrics']['mem_available']['data']['rows'][-1][1]
                    latest_cpu_usage = metrics_data['metrics']['cpu_usage']['data']['rows'][-1][1]
                    latest_diskio_reads = metrics_data['metrics']['diskio_read']['data']['rows'][-1][1]
                    latest_net_send = metrics_data['metrics']['net_send']['data']['rows'][-1][1]
                    latest_net_receive = metrics_data['metrics']['net_receive']['data']['rows'][-1][1]

                    # Set the Prometheus metrics with the latest values
                    dbaas_disk_usage.labels(database=database_name).set(latest_disk_usage)
                    dbaas_load_average.labels(database=database_name).set(latest_load_average)
                    dbaas_mem_usage.labels(database=database_name).set(latest_mem_usage)
                    dbaas_diskio_writes.labels(database=database_name).set(latest_diskio_writes)
                    dbaas_mem_available.labels(database=database_name).set(latest_mem_available)
                    dbaas_cpu_usage.labels(database=database_name).set(latest_cpu_usage)
                    dbaas_diskio_reads.labels(database=database_name).set(latest_diskio_reads)
                    dbaas_net_send.labels(database=database_name).set(latest_net_send)
                    dbaas_net_receive.labels(database=database_name).set(latest_net_receive)

                    logger.info(f"Info: Metrics for {database_name} has been scraped")

                else:
                    logger.error(f"Error: Failed to fetch metrics for {database_name}. Status code: {response.status_code}")

        except Exception as e:
            logger.error(f"Error: An error occurred for {database_name}: {str(e)}")

        # Sleep for some time before fetching metrics again
        time.sleep(30)

if __name__ == '__main__':
    # Start an HTTP server to expose the metrics
    start_http_server(8080)

    # Fetch and update metrics
    fetch_metrics(database_names)

