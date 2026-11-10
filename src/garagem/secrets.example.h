// Modelo das credenciais deste sketch.
// Copie este arquivo para secrets.h (mesma pasta) e preencha com a sua rede.
// O secrets.h está no .gitignore e nunca é commitado.
//
// No Wokwi: crie uma aba "secrets.h" com WIFI_SSID "Wokwi-GUEST" e
// WIFI_PASSWORD "" (senha vazia).

#define WIFI_SSID     "SUA_REDE"
#define WIFI_PASSWORD "SUA_SENHA"

// Broker MQTT (opcional). Sem estas linhas, o firmware usa
// broker.hivemq.com:1883 sem usuário. Para o Mosquitto local, use o IP
// do servidor; usuário e senha vazios = conexão anônima.
// #define MQTT_HOST     "192.168.1.100"
// #define MQTT_PORT     1883
// #define MQTT_USER     "garagem"
// #define MQTT_PASSWORD "SENHA_DO_BROKER"
