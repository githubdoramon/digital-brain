#!/bin/sh
set -e

PASSWORD_FILE=/mosquitto/config/password_file

if [ -z "$MQTT_USERNAME" ] || [ -z "$MQTT_PASSWORD" ]; then
  echo "ERROR: MQTT_USERNAME and MQTT_PASSWORD must be set" >&2
  exit 1
fi

# Generate password file from env vars
touch "$PASSWORD_FILE"
mosquitto_passwd -b "$PASSWORD_FILE" "$MQTT_USERNAME" "$MQTT_PASSWORD"

echo "MQTT password file generated for user: $MQTT_USERNAME"

exec mosquitto -c /mosquitto/config/mosquitto.conf
