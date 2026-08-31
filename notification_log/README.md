# 9Space Notification Log

This Home Assistant add-on records the Pikmin Bloom players who invite you to
mushroom challenges. It answers:

- Who invited me?
- How many times did each player invite me?
- When was each player's latest invite?

Tasker sends only an already extracted player name:

```json
{"inviter":"玩家名稱"}
```

The intended network path is:

```text
Tasker
  -> HTTPS Home Assistant Cloud webhook
  -> Home Assistant webhook automation
  -> rest_command over the Supervisor internal network
  -> Notification Log add-on :8099
  -> /data/notifications.sqlite3
```

The add-on does not connect to Nabu Casa. Do not expose port `8099` to the
Internet. Its host port mapping is disabled; Home Assistant Core reaches it only
through the Supervisor internal network. Tasker knows only the Home Assistant
Cloud webhook URL.

## Install and configure

1. Install **9Space Notification Log** from this Home Assistant app repository.
2. Leave the `8099/tcp` host mapping disabled.
3. Start the add-on.

When upgrading from 0.1.x, remove the old `auth_token` entry from the add-on
Configuration if Supervisor reports that the option no longer exists.

SQLite data is stored at `/data/notifications.sqlite3` and is included in normal
Home Assistant add-on backups. Invite contents are not written to the add-on
log.

## Web UI

Open the add-on page in Home Assistant and select **Open Web UI**, or use the
**Pikmin 蘑菇邀請** sidebar entry. Home Assistant Ingress keeps the page behind
your Home Assistant login while the add-on's host port remains disabled.

The page shows total invites, unique inviters, and a table of player name,
invite count, and latest invite time in UTC. It is read-only and uses the same
SQLite aggregation as `GET /api/v1/invites/stats`.

## Pikmin invite API

Invite endpoints are available only on the Home Assistant internal app network.

### Record an invite

```http
POST /api/v1/invites
Content-Type: application/json

{"inviter":"玩家名稱"}
```

`inviter` is required, trimmed by the server, and limited to 256 characters.
Each request creates a row; the add-on intentionally does not deduplicate
repeated invitations.

Success:

```json
{"ok":true,"id":123}
```

### Statistics

```http
GET /api/v1/invites/stats
```

```json
{
  "total_invites": 15,
  "unique_inviters": 4,
  "inviters": [
    {
      "inviter": "AAA",
      "count": 7,
      "last_invited_at": "2026-08-31T12:34:56.789Z"
    }
  ]
}
```

Players are ordered by invite count descending, then latest invite time
descending.

### Recent history for debugging

```http
GET /api/v1/invites?limit=100
```

The response contains newest-first rows under `invites`. The limit defaults to
100 and is bounded to 500.

## Home Assistant Cloud webhook example

The following uses the current Home Assistant automation syntax. Never commit
the real webhook ID or Cloud webhook URL.

Add placeholders to `secrets.yaml` locally and replace them only inside Home
Assistant:

```yaml
pikmin_mushroom_webhook_id: "REPLACE_WITH_A_RANDOM_WEBHOOK_ID"
```

Add the REST command to `configuration.yaml`. `afa94ae2-9space-notification-log`
is this GitHub repository app's Supervisor-internal DNS name; it is not exposed
to the LAN:

```yaml
rest_command:
  mushroom_invite:
    url: "http://afa94ae2-9space-notification-log:8099/api/v1/invites"
    method: post
    content_type: "application/json"
    payload: >-
      {{ {"inviter": inviter} | to_json }}
```

Add the automation in `automations.yaml`:

```yaml
- id: pikmin_mushroom_invite_webhook
  alias: Pikmin mushroom invite webhook
  mode: queued
  max: 20
  triggers:
    - trigger: webhook
      webhook_id: !secret pikmin_mushroom_webhook_id
      allowed_methods:
        - POST
      local_only: false
  actions:
    - variables:
        inviter: >-
          {{ trigger.json.get("inviter", "") | string | trim }}
    - condition: template
      value_template: >-
        {{ inviter | length > 0 and inviter | length <= 256 }}
    - action: rest_command.mushroom_invite
      data:
        inviter: "{{ inviter }}"
```

After saving the automation, go to **Settings > Home Assistant Cloud**, find the
webhook, select **Manage**, and copy its unique `https://hooks.nabu.casa/...`
URL into Tasker. Treat that entire URL as a password.

Tasker's HTTP request is only:

- URL: the secret Home Assistant Cloud webhook URL
- Method: `POST`
- Content-Type: `application/json`
- Body: `{"inviter":"the extracted Tasker player variable"}`

Use JSON-safe variable encoding in Tasker so quotes or backslashes in a player
name cannot produce malformed JSON.

## Existing notification API compatibility

Version 0.3.0 keeps the existing server, container port, database, health
endpoint, generic notification paths, and responses. Authentication is removed
as requested, together with the host port mapping:

```text
GET  /healthz
POST /api/v1/notifications
GET  /api/v1/notifications?limit=100
GET  /api/v1/stats
```
