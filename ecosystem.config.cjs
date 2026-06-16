module.exports = {
  apps: [
    {
      name: "itgov-flask",
      script: "/home/zabbix/projects/it-governance-dashboard/.venv/bin/gunicorn",
      args: [
        "-w", "2",
        "-b", "0.0.0.0:5000",
        "--timeout", "60",
        "--access-logfile", "/home/zabbix/projects/it-governance-dashboard/logs/gunicorn-access.log",
        "--error-logfile", "/home/zabbix/projects/it-governance-dashboard/logs/gunicorn-error.log",
        "wsgi:application"
      ].join(" "),
      cwd: "/home/zabbix/projects/it-governance-dashboard",
      interpreter: "none",
      autorestart: true,
      watch: false,
      max_restarts: 10,
      min_uptime: "10s",
      env: {
        FLASK_ENV: "production",
        FLASK_DEBUG: "0"
      }
    }
  ]
};
