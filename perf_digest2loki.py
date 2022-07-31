#!/usr/bin/env python

import yaml
import pymysql
import logging
import logging_loki
from multiprocessing import Queue


import asyncio
import sys
from aiohttp import web
from prometheus_client import (
    Counter,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


def read_config(config_file):
    with open(config_file, "r") as ymlconfig:
        cnf = yaml.safe_load(ymlconfig)
    handler = logging_loki.LokiQueueHandler(
        Queue(-1),
        url= cnf['loki']['url'],
        tags={"instance": cnf['name'],},
        version="1",
    ) if 'loki' in cnf and 'url' in cnf['loki'] else None
    name = cnf.get('name', 'perf_digest')
    period = cnf.get('period', 120)
    logger_name = cnf.get('logger', 'PerfDigest')
    port = cnf['mysql'].get('port', 3306)
    log_key = cnf['mysql'].get('log_column', 'info')
    replacements = cnf.get('replacements', {})
    listen_address = cnf.get('listen_address', '0.0.0.0')
    listen_port = cnf.get('listen_port', 3162)

    return handler, cnf, name, period, logger_name, port, log_key, replacements, listen_address, listen_port


async def serve_metrics(request):
    resp = web.Response(body=generate_latest())
    resp.content_type = CONTENT_TYPE_LATEST
    return resp


async def serve_root(request):
    return web.Response(body='See /metrics')

handler, cnf, instance, period, logger_name, port, log_key, replacements, listen_address, listen_port = read_config("perf_digest2loki-config.yml")
metrics = {}
metric = {}

if handler:
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)

if 'extra_tags' in cnf['mysql']:
    for tag in cnf['mysql']['extra_tags']:
        if 'COUNT_' in tag or 'SUM_' in tag:
            metric['perf_digest_' + tag.lower()] = Counter('perf_digest_' +
                                                           tag.lower(), 'Counter of ' + tag, ['instance', 'schema', 'digest'])

web_app = web.Application()
web_app.add_routes([
    web.get('/', serve_root),
    web.get('/metrics', serve_metrics),
])

async def main_loop():
    global metrics, metric, handler
    while True:
        try:
            connection = pymysql.connect(host=cnf['mysql']['host'], user=cnf['mysql']['user'], password=cnf['mysql']['pass'], database=None, port=port, cursorclass=pymysql.cursors.DictCursor)
            with connection.cursor() as cur:
                cur.execute(cnf['mysql']['query'])
                numrows = cur.rowcount
                for i in range(0,numrows):
                    row = cur.fetchone()
                    if row is None or log_key not in row:
                        continue
                    extra_tags = {"severity": "info", }
                    stmt = str(row[log_key])
                    if row['SCHEMA_NAME'] not in metrics:
                        metrics[row['SCHEMA_NAME']] = {}
                    if row['DIGEST'] not in metrics[row['SCHEMA_NAME']]:
                        metrics[row['SCHEMA_NAME']][row['DIGEST']] = {}
                    if len(stmt) > 0:
                        if 'extra_tags' in cnf['mysql']:
                            for tag in cnf['mysql']['extra_tags']:
                                if tag in row:
                                    extra_tags[tag] = str(row[tag])
                                    if 'SUM_' in tag or 'COUNT_' in tag:
                                        if tag not in metrics[row['SCHEMA_NAME']][row['DIGEST']]:
                                            metrics[row['SCHEMA_NAME']
                                                    ][row['DIGEST']][tag] = int(row[tag])

                                        extra_tags['DIFF_' +
                                                tag] = str(int(row[tag]) - int(metrics[row['SCHEMA_NAME']][row['DIGEST']][tag]))
                                        metrics[row['SCHEMA_NAME']][row['DIGEST']][tag] = int(
                                            row[tag])
                                        metric['perf_digest_' + tag.lower()
                                            ].labels(instance, row['SCHEMA_NAME'], row['DIGEST'][:10])._value.set(int(row[tag]))
                        if handler:
                            retry_counter = 0
                            while True:
                                try:
                                    logger.error(stmt, extra={"tags": extra_tags },)
                                    break
                                except ValueError:
                                    if retry_counter > 10:
                                        break
                                    retry_counter += 1
                                    await asyncio.sleep(5)
                    if i % 50 == 0:
                        await asyncio.sleep(1)
        finally:
            connection.close()

        await asyncio.sleep(period)

loop = asyncio.get_event_loop()
asyncio.ensure_future(main_loop())
web.run_app(web_app, host=listen_address, port=listen_port, loop=loop)
loop.run_forever()

