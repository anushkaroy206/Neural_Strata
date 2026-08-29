#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <SPI.h>
#include <MFRC522.h>

// --- RFID RC522 SPI PINOUT ---
#define SS_PIN  5
#define RST_PIN 22

// --- ALERT SYSTEM PINOUT (NEW) ---
#define GREEN_LED 25
#define BUZZER 26
#define RED_LED 27

MFRC522 rfc522(SS_PIN, RST_PIN);

// Struct matching the sender nodes exactly
typedef struct struct_message {
    int node;
    float disp;
    float tilt;
    char uid[20];
} struct_message;

struct_message incomingData;

// --- Siren State Variables ---
bool alarmActive = false;
unsigned long lastBuzzerToggle = 0;
bool buzzerState = false;
const int BUZZER_INTERVAL = 400; // Pulsing speed (400ms ON / 400ms OFF)

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

    // --- Initialize Alert System ---
    pinMode(BUZZER, OUTPUT);
    pinMode(GREEN_LED, OUTPUT);
    pinMode(RED_LED, OUTPUT);

    // Set default state: Safe (Green ON, Alarm OFF)
    digitalWrite(GREEN_LED, HIGH);
    digitalWrite(RED_LED, LOW);
    digitalWrite(BUZZER, LOW);

    // Set Wi-Fi to Station mode for ESP-NOW
    WiFi.mode(WIFI_STA);

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

    Serial.println("{\"status\":\"Underground Hub Online with Alerts\"}");
}

void loop() {
    // --- 1. Listen for AI Commands from Python over USB ---
    if (Serial.available() > 0) {
        String command = Serial.readStringUntil('\n');
        command.trim(); // Remove whitespace/newlines

        if (command == "CRITICAL") {
            digitalWrite(GREEN_LED, LOW);
            digitalWrite(RED_LED, HIGH);
            alarmActive = true; // Turn on the pulsing siren logic
        } 
        else if (command == "SAFE") {
            digitalWrite(GREEN_LED, HIGH);
            digitalWrite(RED_LED, LOW);
            digitalWrite(BUZZER, LOW);  // Force buzzer off
            alarmActive = false;        // Turn off siren logic
        }
    }

    // --- 2. Pulsing Siren Logic (Non-blocking) ---
    if (alarmActive) {
        // Toggle the buzzer every 400ms
        if (millis() - lastBuzzerToggle >= BUZZER_INTERVAL) {
            lastBuzzerToggle = millis();
            buzzerState = !buzzerState; 
            digitalWrite(BUZZER, buzzerState ? HIGH : LOW);
        }
    }

    // --- 3. Check for RFID Cards/Tags ---
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

    delay(50); // Small polling delay to keep loop stable
}