#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <SPI.h>
#include <MFRC522.h>

// --- RFID RC522 SPI PINOUT ---
#define SS_PIN  5
#define RST_PIN 22

MFRC522 rfc522(SS_PIN, RST_PIN);

// Struct matching the sender nodes exactly
typedef struct struct_message {
    int node;
    float disp;
    float tilt;
    char uid[20];
} struct_message;

struct_message incomingData;

// ESP-NOW Receive Callback (Receives wireless telemetry from Node 1 & Node 2)
void OnDataRecv(const uint8_t * mac, const uint8_t *incomingDataBytes, int len) {
    memcpy(&incomingData, incomingDataBytes, sizeof(incomingData));

    // Output telemetry as a clean JSON string over USB Serial for Python
    Serial.printf("{\"node\":%d,\"disp\":%.1f,\"tilt\":%.1f}\n", 
                  incomingData.node, 
                  incomingData.disp, 
                  incomingData.tilt);
}

void setup() {
    Serial.begin(115200);

    // Set Wi-Fi to Station mode for ESP-NOW
    WiFi.mode(WIFI_STA);

    Serial.print("HUB MAC ADDRESS: ");
    Serial.println(WiFi.macAddress());

    // Initialize ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println("{\"error\":\"ESP-NOW Init Failed\"}");
        return;
    }

    // Register receive callback
    esp_now_register_recv_cb(OnDataRecv);

    // Initialize SPI and RC522 RFID
    SPI.begin();
    rfc522.PCD_Init();

    Serial.println("{\"status\":\"Underground Hub Online\"}");
}

void loop() {
    // 1. Check for RFID Cards/Tags
    if (rfc522.PICC_IsNewCardPresent() && rfc522.PICC_ReadCardSerial()) {
        String uidStr = "";
        for (byte i = 0; i < rfc522.uid.size; i++) {
            if (rfc522.uid.uidByte[i] < 0x10) uidStr += "0";
            uidStr += String(rfc522.uid.uidByte[i], HEX);
        }
        uidStr.toUpperCase();

        // Output RFID scan as a JSON string over USB Serial for Python
        Serial.printf("{\"uid\":\"%s\"}\n", uidStr.c_str());

        // Halt PICC & Stop Encryption on PCD
        rfc522.PICC_HaltA();
        rfc522.PCD_StopCrypto1();
    }

    delay(100); // Polling delay for RFID
}