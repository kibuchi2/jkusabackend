[Unit]
Description=JKUSA Backend FastAPI Service
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/home/jkusa-backend/htdocs/backend.jkusa.org/jkusabackend
Environment="PATH=/home/jkusa-backend/htdocs/backend.jkusa.org/jkusabackend/venv/bin"
EnvironmentFile=/home/jkusa-backend/htdocs/backend.jkusa.org/jkusabackend/.env
ExecStart=/home/jkusa-backend/htdocs/backend.jkusa.org/jkusabackend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target



RUNNING SYSTEMCTL---
sudo nano /etc/systemd/system/jkusa-backend.service

sudo systemctl daemon-reload
sudo systemctl start jkusa-backend.service
sudo systemctl enable jkusa-backend.service
