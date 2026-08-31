# 9Space Notification Log

This Home Assistant add-on receives Android notification events from Tasker and
stores them in `/data/notifications.sqlite3`. The database is included in normal
Home Assistant add-on backups. Notification bodies and authorization headers are
never written to the add-on log.

## Install and configure

1. Add this Git repository as a Home Assistant app repository, then install
   **9Space Notification Log**.
2. In the app configuration, replace `auth_token` with a random value of at
   least 24 characters. Keep `retention_days` and `max_rows` at their defaults
   unless a different bounded retention policy is required.
3. Start the app. It listens on Home Assistant host port `8099`.

The API uses plain HTTP. Use it only on a trusted LAN or through Tailscale; do
not port-forward it to the public Internet. The Bearer token is a secret and
must not be placed in Tasker logs, flash messages, or exported public profiles.

## Tasker HTTP Request action

Configure Tasker's **HTTP Request** action:

- Method: `POST`
- URL: `http://HOME_ASSISTANT_PRIVATE_IP:8099/api/v1/notifications`
- Headers:

  ```text
  Authorization: Bearer YOUR_LONG_RANDOM_TOKEN
  Content-Type: application/json
  ```

- Body: map the variables exposed by your Tasker Notification event into this
  JSON. Tasker versions and notification event types expose different variable
  names, so select them from Tasker's variable picker instead of assuming fixed
  `%evtprm()` positions. Make sure Tasker JSON-escapes variable values; direct
  raw substitution will produce invalid JSON when a title or message contains a
  quote, backslash, or line break.

  ```json
  {
    "package_name": "TASKER_PACKAGE_VARIABLE",
    "app_name": "TASKER_APP_VARIABLE",
    "title": "TASKER_TITLE_VARIABLE",
    "text": "TASKER_TEXT_VARIABLE",
    "source_device": "my-phone"
  }
  ```

`occurred_at` is optional. Omit it to use the server receive time, or send an
ISO-8601 timestamp with timezone, Unix seconds, or Unix milliseconds. Optional
fields are `sub_text`, `category`, `notification_key`, `event_type` (`posted` or
`removed`), and an `extra` JSON object.

A successful write returns HTTP `201` and the saved row. A bad or missing token
returns `401`; invalid fields return `422`.

## Read API

All read endpoints except health require the same Bearer token:

```text
GET /healthz
GET /api/v1/notifications?limit=100
GET /api/v1/notifications?limit=100&before_id=1234
GET /api/v1/notifications?package_name=com.example.app
GET /api/v1/stats
```

The list response is newest-first and returns `next_before_id` when another page
may exist. Request limits are bounded to 500 rows.
