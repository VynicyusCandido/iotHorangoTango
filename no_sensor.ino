#include <WiFi.h>
#include <PubSubClient.h>

// ---------- Configurações Wi-Fi ----------
//const char* ssid = "SUA_REDE";
//const char* password = "SUA_SENHA";

const char* ssid = "Wokwi-GUEST";
const char* password = "";


// ---------- Configurações MQTT ----------
//const char* mqtt_server = "192.168.1.100"; // IP do broker Mosquitto
//const int   mqtt_port   = 1883;
//const char* mqtt_client_id = "esp32-garagem";
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;
const char* mqtt_client_id = "esp32-garagem-fnd-8471";


WiFiClient espClient;
PubSubClient client(espClient);

// ---------- Definição das vagas ----------
// Cada vaga: {trigPin, echoPin}
struct Vaga {
  int trig;
  int echo;
  bool ocupada;
};

Vaga vagas[] = {
  {5, 18, false},
};
const int NUM_VAGAS = sizeof(vagas) / sizeof(vagas[0]);

// Distância (cm) abaixo da qual consideramos a vaga ocupada
const float LIMIAR_CM = 30.0;

// Intervalo entre leituras (ms)
const unsigned long INTERVALO = 1000;
unsigned long ultimaLeitura = 0;

// ---------- Mede distância de um sensor ----------
float medirDistancia(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);

  // timeout de 30ms (~5m) evita travar se não houver eco
  long duracao = pulseIn(echo, HIGH, 30000);
  if (duracao == 0) return -1; // sem leitura válida

  return duracao * 0.0343 / 2.0; // cm
}

// ---------- Conexão Wi-Fi ----------
void conectarWiFi() {
  WiFi.begin(ssid, password);
  Serial.print("Conectando ao Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi conectado. IP: " + WiFi.localIP().toString());
}

// ---------- Reconexão MQTT ----------
void conectarMQTT() {
  while (!client.connected()) {
    Serial.print("Conectando ao broker MQTT...");
    if (client.connect(mqtt_client_id)) {
      Serial.println("conectado.");
    } else {
      Serial.print("falhou, rc=");
      Serial.print(client.state());
      Serial.println(" tentando de novo em 2s");
      delay(2000);
    }
  }
}

void setup() {
  Serial.begin(115200);

  for (int i = 0; i < NUM_VAGAS; i++) {
    pinMode(vagas[i].trig, OUTPUT);
    pinMode(vagas[i].echo, INPUT);
    digitalWrite(vagas[i].trig, LOW);
  }

  conectarWiFi();
  client.setServer(mqtt_server, mqtt_port);
}

void loop() {
  if (!client.connected()) conectarMQTT();
  client.loop();

  if (millis() - ultimaLeitura >= INTERVALO) {
    ultimaLeitura = millis();

    for (int i = 0; i < NUM_VAGAS; i++) {
      float dist = medirDistancia(vagas[i].trig, vagas[i].echo);
      bool ocupadaAgora = (dist > 0 && dist < LIMIAR_CM);

      // Publica só quando o estado muda (evita tráfego desnecessário)
      if (ocupadaAgora != vagas[i].ocupada) {
        vagas[i].ocupada = ocupadaAgora;

        char topico[32];
        snprintf(topico, sizeof(topico), "garagem/vaga/%d", i + 1);
        const char* estado = ocupadaAgora ? "ocupada" : "livre";

        client.publish(topico, estado, true); // retained
        Serial.printf("Vaga %d: %s (%.1f cm)\n", i + 1, estado, dist);
      }
    }
  }
}