// Modelo das credenciais do ESP32 atuador (LEDs).
// Copie este arquivo para secrets.h (mesma pasta) e preencha.
// O secrets.h está no .gitignore e nunca é commitado.

#define WIFI_SSID     "SUA_REDE"
#define WIFI_PASSWORD "SUA_SENHA"

// Broker MQTT: o mesmo do ESP32 dos sensores. Usuário "atuador" com a
// senha MQTT_ATUADOR_PASSWORD do .env (só lê estado/status e publica o
// próprio status; ver broker/acl).
#define MQTT_HOST     "192.168.1.100"
#define MQTT_PORT     1883
#define MQTT_USER     "atuador"
#define MQTT_PASSWORD "SENHA_DO_ATUADOR"

// deviceId do ESP32 dos sensores, cujas vagas este atuador acompanha.
// Aparece no monitor serial dele ao ligar ("deviceId: esp32-garagem-...").
#define SENSOR_DEVICE_ID "esp32-garagem-000000000000"
