#!/bin/sh
# Sobe o Mosquitto com os usuários definidos no .env.
# Gera o arquivo de senhas (hash) a cada subida; as senhas em texto
# ficam só no .env, que não vai para o git.
set -eu

: "${MQTT_DISPOSITIVO_USER:?defina no .env}"
: "${MQTT_DISPOSITIVO_PASSWORD:?defina no .env}"
: "${MQTT_ATUADOR_USER:?defina no .env}"
: "${MQTT_ATUADOR_PASSWORD:?defina no .env}"
: "${MQTT_BACKEND_USER:?defina no .env}"
: "${MQTT_BACKEND_PASSWORD:?defina no .env}"
: "${MQTT_PERSISTENCIA_USER:?defina no .env}"
: "${MQTT_PERSISTENCIA_PASSWORD:?defina no .env}"
: "${MQTT_MOTORISTA_USER:?defina no .env}"
: "${MQTT_MOTORISTA_PASSWORD:?defina no .env}"
: "${MQTT_MONITOR_USER:?defina no .env}"
: "${MQTT_MONITOR_PASSWORD:?defina no .env}"

for senha in "$MQTT_DISPOSITIVO_PASSWORD" "$MQTT_ATUADOR_PASSWORD" "$MQTT_BACKEND_PASSWORD" "$MQTT_PERSISTENCIA_PASSWORD" "$MQTT_MOTORISTA_PASSWORD" "$MQTT_MONITOR_PASSWORD"; do
  [ "$senha" != "troque-esta-senha" ] || { echo "Troque as senhas do .env (ainda estão com o valor do exemplo)" >&2; exit 1; }
done

dados=/mosquitto/data
senhas="$dados/passwd"

rm -f "$senhas"
touch "$senhas"
chmod 0600 "$senhas"
mosquitto_passwd -b "$senhas" "$MQTT_DISPOSITIVO_USER" "$MQTT_DISPOSITIVO_PASSWORD"
mosquitto_passwd -b "$senhas" "$MQTT_ATUADOR_USER" "$MQTT_ATUADOR_PASSWORD"
mosquitto_passwd -b "$senhas" "$MQTT_BACKEND_USER" "$MQTT_BACKEND_PASSWORD"
mosquitto_passwd -b "$senhas" "$MQTT_PERSISTENCIA_USER" "$MQTT_PERSISTENCIA_PASSWORD"
mosquitto_passwd -b "$senhas" "$MQTT_MOTORISTA_USER" "$MQTT_MOTORISTA_PASSWORD"
mosquitto_passwd -b "$senhas" "$MQTT_MONITOR_USER" "$MQTT_MONITOR_PASSWORD"

# O Mosquitto exige arquivos de senha e ACL legíveis só pelo dono
# (usuário mosquitto); por isso a ACL é copiada para o volume de dados
install -m 0600 /mosquitto/config/acl "$dados/acl"
chown 1883:1883 "$senhas" "$dados/acl"

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
