#include <WiFi.h>
#include <PubSubClient.h>
#include <SPI.h>
#include <MFRC522.h>

// Firmware único: um ESP32 opera as duas vagas.
//   - HC-SR04 de cada vaga -> publica o estado em <base>/vaga/<numero>
//   - RC522 de cada vaga   -> publica o UID lido em <base>/rfid/<numero>
//   - LED RGB de cada vaga <- assina <base>/vaga/# (o que o broker
//     distribuiu, inclusive o que este próprio ESP32 publicou)
// Mapa de pinos em docs/mapa-de-pinos.md; decisão em docs/adr/0001.

// ---------- Configurações Wi-Fi ----------
// Rede e senha ficam em secrets.h, que NÃO vai para o git (.gitignore).
// Copie secrets.example.h para secrets.h nesta mesma pasta e preencha.
#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Falta secrets.h: copie secrets.example.h para secrets.h e preencha WIFI_SSID e WIFI_PASSWORD"
#endif

const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// ---------- Configurações MQTT ----------
//const char* mqtt_server = "192.168.1.100"; // IP do servidor com Mosquitto
//const int   mqtt_port   = 1883;
const char* mqtt_server = "broker.hivemq.com";
const int   mqtt_port   = 1883;

// Raiz dos tópicos — deve bater EXATAMENTE com o painel e o app
//   <base>/vaga/<numero>       -> "livre" / "ocupada" / "erro" (retained)
//   <base>/rfid/<numero>       -> UID da tag lida, ex.: "04a1b2c3" (evento)
//   <base>/no/<id>/status      -> "online" / "offline" (retained, LWT)
//   <base>/no/<id>/vagas       -> lista das vagas deste nó, ex.: "1,2"
const char* topico_base = "garagem";

// ID do nó, gerado a partir do MAC em setup() — único por placa
char no_id[13];          // MAC em hex, ex.: "240ac4000110"
char mqtt_client_id[32]; // "esp32-garagem-<no_id>"
char topico_status[64];  // "<base>/no/<no_id>/status"
char topico_vagas[48];   // "<base>/vaga/#"
char prefixo_vaga[48];   // "<base>/vaga/"

WiFiClient espClient;
PubSubClient client(espClient);

// ---------- Pinos compartilhados ----------
// SPI (VSPI) e reset comuns aos dois RC522; cada leitor tem o seu SS
const int PIN_SPI_SCK  = 18;
const int PIN_SPI_MISO = 19;
const int PIN_SPI_MOSI = 23;
const int PIN_RFID_RST = 22;

// ---------- Estados de uma vaga ----------
// ERRO = o HC-SR04 não responde (fio solto, sensor queimado...). Publicado
// como "erro"; painel, app e LED tratam como "sem dados", nunca como livre.
enum Estado { DESCONHECIDO, LIVRE, OCUPADA, ERRO };
const char* NOMES_ESTADO[] = {"desconhecido", "livre", "ocupada", "erro"};

// ---------- Definição das vagas ----------
// Cada vaga: {numero, trig, echo, rfidSS, ledVermelho, ledVerde}
// numero = número da vaga na garagem (o mesmo usado no painel e no app)
// LED RGB catodo comum: HIGH acende. Os demais campos são controle
// interno e começam zerados.
struct Vaga {
  int numero;
  int trig;
  int echo;
  int rfidSS;
  int ledVermelho;
  int ledVerde;

  // Ultrassônico (fonte do estado da vaga)
  Estado estado;     // estado confirmado (o que é publicado)
  Estado candidato;  // estado novo aguardando confirmação
  int confirmacoes;  // leituras seguidas que concordam com o candidato
  int falhas;        // leituras inválidas seguidas
  bool pendente;     // estado ainda não publicado no broker (mudança,
                     // reconexão ou falha no publish)

  // RFID (só identifica; nunca altera o estado da vaga)
  char ultimoUid[21];          // última tag lida neste leitor
  unsigned long ultimaTag;     // millis() da última leitura
  char uidPendente[21];        // leitura ainda não publicada ("" = nenhuma)
  bool leitorOk;               // RC522 respondeu na última verificação

  // LED (segue o que chega do broker)
  bool conhecida;  // tem estado válido (livre/ocupada); senão LED apagado
  bool ocupada;    // último estado recebido
};

Vaga vagas[] = {
  {1, 25, 34, 5, 32, 33},
  {2, 26, 35, 4, 27, 13},
};
const int NUM_VAGAS = sizeof(vagas) / sizeof(vagas[0]);

// Um RC522 por vaga, mesma ordem de vagas[]
MFRC522 leitores[NUM_VAGAS];

// ---------- Limiares de detecção (com histerese) ----------
// Abaixo de LIMIAR_OCUPA_CM a vaga passa a ocupada; acima de
// LIMIAR_LIBERA_CM, a livre. Entre os dois, mantém o estado atual.
// Valores para carro real; na maquete, medir e ajustar.
const float LIMIAR_OCUPA_CM  = 25.0;
const float LIMIAR_LIBERA_CM = 35.0;

// Leituras seguidas iguais necessárias para trocar de estado
const int LEITURAS_CONFIRMACAO = 3;

// Leituras inválidas seguidas até marcar a vaga como "erro"
const int FALHAS_PARA_ERRO = 5;

// Faixa de medição válida do HC-SR04
const float DIST_MIN_CM = 2.0;
const float DIST_MAX_CM = 400.0;

// Intervalo entre leituras do ultrassônico (ms)
const unsigned long INTERVALO = 1000;
unsigned long ultimaLeitura = 0;

// ---------- Tempos do RFID ----------
// Intervalo entre consultas aos leitores (ms)
const unsigned long INTERVALO_RFID = 100;
unsigned long ultimaConsultaRfid = 0;

// Mesma tag no mesmo leitor dentro deste tempo é ignorada (ms)
const unsigned long RFID_REPETICAO = 3000;

// A cada quanto tempo confere se os RC522 ainda respondem (ms)
const unsigned long INTERVALO_CHECA_RFID = 5000;
unsigned long ultimaChecagemRfid = 0;

// ---------- LEDs ----------
// Livre: verde. Ocupada: vermelho. Sem estado ou sem broker: apagado.
void atualizarLed(Vaga& v) {
  bool apagar = !v.conhecida || !client.connected();
  digitalWrite(v.ledVerde,    !apagar && !v.ocupada ? HIGH : LOW);
  digitalWrite(v.ledVermelho, !apagar &&  v.ocupada ? HIGH : LOW);
}

void atualizarTodosLeds() {
  for (int i = 0; i < NUM_VAGAS; i++) atualizarLed(vagas[i]);
}

// ---------- Procura a vaga pelo número (-1 se não for deste nó) ----------
int indiceDaVaga(int numero) {
  for (int i = 0; i < NUM_VAGAS; i++) {
    if (vagas[i].numero == numero) return i;
  }
  return -1;
}

// ---------- Callback: mensagem em <base>/vaga/<numero> ----------
void callback(char* topic, byte* payload, unsigned int length) {
  if (strncmp(topic, prefixo_vaga, strlen(prefixo_vaga)) != 0) return;

  int indice = indiceDaVaga(atoi(topic + strlen(prefixo_vaga)));
  if (indice < 0) return; // vaga de outro nó

  char msg[16];
  unsigned int n = length < sizeof(msg) - 1 ? length : sizeof(msg) - 1;
  memcpy(msg, payload, n);
  msg[n] = '\0';

  // "livre" / "ocupada": estado válido. "erro", retained apagado ou
  // qualquer outro valor: LED apagado — nunca mostrar verde sem ter
  // certeza de que a vaga está livre.
  Vaga& v = vagas[indice];
  bool livre = (strcmp(msg, "livre") == 0);
  v.ocupada = (strcmp(msg, "ocupada") == 0);
  v.conhecida = livre || v.ocupada;
  atualizarLed(v);

  Serial.printf("LED vaga %d -> %s\n", v.numero,
                strcmp(msg, "erro") == 0 ? "ERRO NO SENSOR (apagado)" :
                !v.conhecida             ? "SEM ESTADO (apagado)"     :
                v.ocupada                ? "OCUPADA (vermelho)"       : "LIVRE (verde)");
}

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

// ---------- Processa uma leitura; retorna true se o estado mudou ----------
bool processarLeitura(Vaga& v, float dist) {
  // Leitura inválida: não diz nada sobre a vaga. Só vira "erro" se persistir.
  if (dist < DIST_MIN_CM || dist > DIST_MAX_CM) {
    v.confirmacoes = 0;
    if (v.falhas < FALHAS_PARA_ERRO) v.falhas++;
    if (v.falhas >= FALHAS_PARA_ERRO && v.estado != ERRO) {
      v.estado = ERRO;
      return true;
    }
    return false;
  }
  v.falhas = 0;

  // Classifica com histerese; na faixa intermediária mantém o estado atual
  // (ou, sem estado definido ainda, usa o meio da faixa)
  Estado leitura;
  if (dist < LIMIAR_OCUPA_CM) {
    leitura = OCUPADA;
  } else if (dist > LIMIAR_LIBERA_CM) {
    leitura = LIVRE;
  } else if (v.estado == LIVRE || v.estado == OCUPADA) {
    leitura = v.estado;
  } else {
    leitura = dist < (LIMIAR_OCUPA_CM + LIMIAR_LIBERA_CM) / 2 ? OCUPADA : LIVRE;
  }

  // Confirmação: só troca após LEITURAS_CONFIRMACAO leituras seguidas iguais
  if (leitura == v.estado) {
    v.confirmacoes = 0;
    return false;
  }
  if (leitura == v.candidato && v.confirmacoes > 0) {
    v.confirmacoes++;
  } else {
    v.candidato = leitura;
    v.confirmacoes = 1;
  }
  if (v.confirmacoes >= LEITURAS_CONFIRMACAO) {
    v.estado = leitura;
    v.confirmacoes = 0;
    return true;
  }
  return false;
}

// ---------- Ultrassônico: lê as vagas uma de cada vez e publica ----------
void lerUltrassonicos() {
  for (int i = 0; i < NUM_VAGAS; i++) {
    Vaga& v = vagas[i];
    float dist = medirDistancia(v.trig, v.echo);
    if (processarLeitura(v, dist)) v.pendente = true;

    // Publica só quando o estado confirmado muda ou há estado pendente
    // (reconexão / falha anterior). Após o boot, espera o primeiro estado.
    if (v.pendente && v.estado != DESCONHECIDO) {
      char topico[64];
      snprintf(topico, sizeof(topico), "%s/vaga/%d", topico_base, v.numero);
      const char* estado = NOMES_ESTADO[v.estado];

      // retained; se falhar, tenta de novo na próxima leitura
      v.pendente = !client.publish(topico, estado, true);
      Serial.printf("Vaga %d: %s (%.1f cm)\n", v.numero, estado, dist);
    }
  }
}

// ---------- RFID: confere se o RC522 responde ----------
// VersionReg 0x00 ou 0xFF = leitor desligado ou fio solto
bool leitorResponde(MFRC522& leitor) {
  byte versao = leitor.PCD_ReadRegister(MFRC522::VersionReg);
  return versao != 0x00 && versao != 0xFF;
}

void iniciarLeitores() {
  for (int i = 0; i < NUM_VAGAS; i++) {
    leitores[i].PCD_Init(vagas[i].rfidSS, PIN_RFID_RST);
  }
}

void checarLeitores() {
  bool todosOk = true;
  for (int i = 0; i < NUM_VAGAS; i++) {
    if (!leitorResponde(leitores[i])) todosOk = false;
  }
  // Algum leitor parou (RC522 às vezes trava após ruído na alimentação):
  // reinicia TODOS, porque o reset é comum e pode ter zerado os outros
  if (!todosOk) iniciarLeitores();

  for (int i = 0; i < NUM_VAGAS; i++) {
    bool ok = leitorResponde(leitores[i]);
    if (ok != vagas[i].leitorOk) {
      Serial.printf("RFID vaga %d: %s\n", vagas[i].numero,
                    ok ? "leitor OK" : "LEITOR NAO RESPONDE");
    }
    vagas[i].leitorOk = ok;
  }
}

// ---------- RFID: consulta os leitores e publica cada tag lida ----------
// Publica toda leitura, sem olhar o estado da vaga: cruzar tag com vaga
// ocupada é papel do painel e do app.
void lerRfids() {
  for (int i = 0; i < NUM_VAGAS; i++) {
    Vaga& v = vagas[i];
    MFRC522& leitor = leitores[i];
    if (!v.leitorOk) continue;

    if (leitor.PICC_IsNewCardPresent() && leitor.PICC_ReadCardSerial()) {
      char uid[sizeof(v.ultimoUid)] = "";
      for (byte b = 0; b < leitor.uid.size && b < 10; b++) {
        snprintf(uid + 2 * b, sizeof(uid) - 2 * b, "%02x", leitor.uid.uidByte[b]);
      }
      leitor.PICC_HaltA();
      leitor.PCD_StopCrypto1();

      // Mesma tag no mesmo leitor há pouco tempo: repetição, ignora
      bool repetida = strcmp(uid, v.ultimoUid) == 0 &&
                      millis() - v.ultimaTag < RFID_REPETICAO;
      v.ultimaTag = millis();
      if (!repetida) {
        strcpy(v.ultimoUid, uid);
        strcpy(v.uidPendente, uid);
        Serial.printf("RFID vaga %d: tag %s\n", v.numero, uid);
      }
    }

    // Não retained (é um evento); se falhar, tenta de novo na próxima consulta
    if (v.uidPendente[0] != '\0' && client.connected()) {
      char topico[64];
      snprintf(topico, sizeof(topico), "%s/rfid/%d", topico_base, v.numero);
      if (client.publish(topico, v.uidPendente)) v.uidPendente[0] = '\0';
    }
  }
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

// ---------- Identidade do nó ----------
void gerarIdentidade() {
  uint64_t mac = ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL;
  snprintf(no_id, sizeof(no_id), "%012llx", mac);
  snprintf(mqtt_client_id, sizeof(mqtt_client_id), "esp32-garagem-%s", no_id);
  snprintf(topico_status, sizeof(topico_status), "%s/no/%s/status", topico_base, no_id);
  snprintf(topico_vagas, sizeof(topico_vagas), "%s/vaga/#", topico_base);
  snprintf(prefixo_vaga, sizeof(prefixo_vaga), "%s/vaga/", topico_base);
  Serial.printf("No garagem: %s\n", no_id);
}

// Publica (retained) quais vagas este nó monitora
void publicarListaVagas() {
  char topico[64];
  snprintf(topico, sizeof(topico), "%s/no/%s/vagas", topico_base, no_id);

  char lista[128] = "";
  for (int i = 0; i < NUM_VAGAS; i++) {
    size_t len = strlen(lista);
    snprintf(lista + len, sizeof(lista) - len, "%s%d", i ? "," : "", vagas[i].numero);
  }
  client.publish(topico, lista, true);
}

// ---------- Reconexão MQTT ----------
void conectarMQTT() {
  // Sem broker não há estado confiável: LEDs apagados até reconectar
  atualizarTodosLeds();

  while (!client.connected()) {
    Serial.print("Conectando ao broker MQTT...");
    // Last Will: se o nó cair sem desconectar, o broker publica "offline"
    // (retained) no tópico de status — painel e app marcam as vagas como
    // sem dados
    if (client.connect(mqtt_client_id, topico_status, 1, true, "offline")) {
      Serial.println("conectado.");
      client.publish(topico_status, "online", true);
      publicarListaVagas();
      // LEDs seguem o broker; os retained chegam logo após assinar
      client.subscribe(topico_vagas);
      Serial.printf("Assinado: %s\n", topico_vagas);
      // Republica o estado de todas as vagas: mudanças ocorridas enquanto
      // estava desconectado não chegaram ao broker
      for (int i = 0; i < NUM_VAGAS; i++) vagas[i].pendente = true;
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
    Vaga& v = vagas[i];
    pinMode(v.trig, OUTPUT);
    pinMode(v.echo, INPUT);
    digitalWrite(v.trig, LOW);

    // LEDs apagados até chegar o estado do broker
    pinMode(v.ledVerde, OUTPUT);
    pinMode(v.ledVermelho, OUTPUT);
    atualizarLed(v);

    // Todos os SS em nível alto antes do SPI: nenhum leitor selecionado
    pinMode(v.rfidSS, OUTPUT);
    digitalWrite(v.rfidSS, HIGH);
  }

  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI);
  iniciarLeitores();
  for (int i = 0; i < NUM_VAGAS; i++) {
    vagas[i].leitorOk = leitorResponde(leitores[i]);
    Serial.printf("RFID vaga %d: %s\n", vagas[i].numero,
                  vagas[i].leitorOk ? "leitor OK" : "LEITOR NAO RESPONDE");
  }

  gerarIdentidade();
  conectarWiFi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) conectarMQTT();
  client.loop();

  if (millis() - ultimaLeitura >= INTERVALO) {
    ultimaLeitura = millis();
    lerUltrassonicos();
  }

  if (millis() - ultimaChecagemRfid >= INTERVALO_CHECA_RFID) {
    ultimaChecagemRfid = millis();
    checarLeitores();
  }

  if (millis() - ultimaConsultaRfid >= INTERVALO_RFID) {
    ultimaConsultaRfid = millis();
    lerRfids();
  }
}
