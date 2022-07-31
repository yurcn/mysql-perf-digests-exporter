FROM python:3.9-alpine

ENV PATH /usr/local/bin:$PATH
ENV LANG C.UTF-8

RUN for pkg in PyYAML PyMysql python-logging-loki aiohttp prometheus_client; do pip install $pkg ; done

RUN mkdir /app
RUN touch /app/perf_digest2loki-config.yml
COPY ./perf_digest2loki.py /app/

WORKDIR /app

EXPOSE 3162

CMD ["python", "perf_digest2loki.py"]