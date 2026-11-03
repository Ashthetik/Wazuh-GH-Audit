# Wazuh-GH-Audit
Workaround to organisations lacking Enterprise Tier for GitHub SEIM logging.

## Benefit vs Loses
Fortunately enough for this setup, there's not much to lose from taking the webhook approach. Sadly enough, the webhook approach doesn't encompass all of GitHub's audit logging capabilities.
- Pros:
- - Real-Time/Push-Based Events
- - Org/Repo/Team/Member Changes
- - Branch Protection Changes
- - Webhook Lifecycle
- - Secret Scanning Alerts
- - Pretty much everything from the native Audit Logs
- Cons:
- - Lose of native `git` events (such as clone/fetch/push events)

## Installation
I've tried to steamline this as much as possible, so here's a little script to get you started
```sh
# Directory setup
mkdir -p /var/log/github-webhooks /opt/github-webhook /etc/github-webhook
touch /etc/github-webhook/env

# locked-down account for the webhook
useradd --system --no-create-home github-webhook

# give the account access to the folders
chown github-webhook:github-webhook /var/log/github-webhooks
chmod 600 /etc/github-webhook/env
chown github-webhook:github-webhook /etc/github-webhook/env

echo "GITHUB_SECRET=$(openssl rand -hex 32)" >> /etc/github-webhook/env
echo "WEBHOOK_LOG_PATH=/var/log/github-webhooks/events.log" >> /etc/github-webhook/env

# Setup Python for our receiver.
# The views are `async def`, so Flask needs the async extra or it raises
# at request time; gunicorn serves it.
python3 -m venv /opt/github-webhook/venv
/opt/github-webhook/venv/bin/pip install "flask[async]" gunicorn

# Clone the repo and place our files
git clone https://github.com/Ashthetik/Wazuh-GH-Audit.git

cp Wazuh-GH-Audit/src/main.py /opt/github-webhook/
cp Wazuh-GH-Audit/webhook-receiver.service /etc/systemd/system/

# Wazuh 4.x auto-loads every .xml under etc/rules, so drop the file in
# whole rather than flattening it into local_rules.xml.
cp Wazuh-GH-Audit/github_rules.xml /var/ossec/etc/rules/
chown wazuh:wazuh /var/ossec/etc/rules/github_rules.xml

# Tell Wazuh to actually read the log. Without this the receiver writes
# events and nothing ever ingests them.
cat >> /var/ossec/etc/ossec.conf <<'EOF'
<ossec_config>
  <localfile>
    <log_format>json</log_format>
    <location>/var/log/github-webhooks/events.log</location>
  </localfile>
</ossec_config>
EOF

# The receiver logs the full raw payload per event, so rotate it.
cat > /etc/logrotate.d/github-webhooks <<'EOF'
/var/log/github-webhooks/events.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF

# Reload the daemon and Wazuh Manager
systemctl daemon-reload
systemctl enable --now webhook-receiver.service
systemctl restart wazuh-manager

echo "Wooo! We're done!"
```

The receiver binds `127.0.0.1:8787`, so GitHub cannot reach it directly.
Put it behind a reverse proxy that terminates TLS and forwards
`POST /webhooks/github`, and point the GitHub webhook at that public URL
with the same secret you generated above.

## Key Takeaway
There's a critical point that needs to be addressed! Unlike using the native git audit logs, our log files **will not** appear in the Cloud Security tab of the Wazuh WUI. It will instead appear in the Threat Hunting tab under the following:
- `rule.id >= 100050 and rule.id <= 100080`
- `rule.groups: "github_webhook"`

It is also recommended to create a Webhook with the following permissions:

- Identity & access:
- - Memberships
- - Organizations
- - Teams
- - Team adds
- - Collaborator add, remove, or changed
- - Org blocks
- - Personal access token requests

- Repo integrity & exposure:
- - Repositories
- - Visibility changes
- - Branch protection rules
- - Branch protection configurations
- - Repository rulesets
- - Bypass requests for push rulesets
- - Deploy keys
- - Deployments
- - Deployment Statuses
- - Meta (fires when the webhook itself is deleted)

- Secrets & vulnerabilities:
- - Secret scanning alerts
- - Secret scanning alert locations
- - Secret scanning scans
- - Bypass requests for secret scanning push protections
- - Dismissal requests for secret scanning alerts
- - Dependabot alerts
- - Dismissal requests for dependabot alerts
- - Repository vulnerability alerts
- - Code scanning alerts
- - Dismissal requests for code scanning alerts
- - Security and analyses

- Supply-Chain Integrity
- - Releases
- - Repository Advisories
- - Packages
- - Registry Packages

With all that done, you should be able to also cover GitHub in the same way as your servers! Feel free to open an issue if there's any concerns, issues, or improvements you'd like to discuss!
