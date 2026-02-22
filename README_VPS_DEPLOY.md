# VPS Deployment Guide for HacettepeBot

This guide explains how to deploy your Hacettepe Bot to a Linux VPS (Ubuntu/Debian) and link it to your custom domain.

## Prerequisites
- A VPS with Ubuntu 20.04 or 22.04
- A domain name pointing to your VPS IP address (A Record)
- SSH access to your VPS

## Step 1: Install Dependencies
Run the following commands on your VPS to install Python, Node, and Playwright dependencies:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx libgtk-3-0 libasound2 libgbm-dev
```

## Step 2: Clone the Project
Upload your project files to the VPS or use Git to clone them:

```bash
git clone <your-repo-url> hacettepe-bot
cd hacettepe-bot
```

## Step 3: Setup Python Environment
Create a virtual environment and install the required packages:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
playwright install-deps
```

## Step 4: Setup Environment Variables
Create a `.env` file from the example:

```bash
cp .env.example .env
nano .env
```
Add your 2Captcha API key and other necessary configurations.

## Step 5: Run the App as a Background Service
We'll use `systemd` to keep your FastAPI app running continuously.
Create a service file:

```bash
sudo nano /etc/systemd/system/hacettepebot.service
```

Add the following (replace `/path/to/hacettepe-bot` with your actual directory, e.g., `/home/ubuntu/hacettepe-bot`):

```ini
[Unit]
Description=HacettepeBot FastAPI Service
After=network.target

[Service]
User=root
# Or use your specific username (e.g. ubuntu)
WorkingDirectory=/path/to/hacettepe-bot
Environment="PATH=/path/to/hacettepe-bot/.venv/bin"
Environment="PYTHONPATH=/path/to/hacettepe-bot"
ExecStart=/path/to/hacettepe-bot/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl enable hacettepebot
sudo systemctl start hacettepebot
sudo systemctl status hacettepebot
```

## Step 6: Configure NGINX Reverse Proxy
Create a new Nginx configuration for your domain:

```bash
sudo nano /etc/nginx/sites-available/hacettepebot
```

Add the following configuration (replace `yourdomain.com` with your actual domain):

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support for the /ws/search endpoint
    location /ws/search {
        proxy_pass http://127.0.0.1:8000/ws/search;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
    }
}
```

Enable the site and restart Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/hacettepebot /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

## Step 7: Secure with SSL (HTTPS)
Use Certbot to get a free SSL certificate from Let's Encrypt:

```bash
sudo certbot --nginx -d yourdomain.com
```

Follow the prompts to enable HTTPS. Certbot will automatically update your Nginx configuration.

---

### Troubleshooting
- To check the app logs: `sudo journalctl -u hacettepebot -f`
- If Playwright fails on the server, try running `playwright install-deps` again.
- Ensure your server has at least 1GB - 2GB RAM because Google Chrome (Playwright) running in the background uses significant memory during searches.
