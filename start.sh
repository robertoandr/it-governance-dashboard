#!/bin/bash
cd /home/zabbix/projects/it-governance-dashboard
exec .venv/bin/gunicorn \
  -w 2 \
  -b 0.0.0.0:5000 \
  --timeout 60 \
  --access-logfile logs/gunicorn-access.log \
  --error-logfile logs/gunicorn-error.log \
  wsgi:application
