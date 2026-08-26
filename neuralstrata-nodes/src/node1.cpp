#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

// ==========================================
// 1. UPDATE THIS FOR EACH BOARD!
// Change to 1 for the first board, 2 for the second board
#define NODE_ID 1 

// 2. PASTE YOUR HUB'S MAC ADDRESS HERE!
// Example: {0x24, 0x6F, 0x28, 0xAE, 0x9B, 0x1C}
uint8_t hubAddress[] = {0x00, 0x4B, 0x12, 0x33, 0x78, 0x38}; 
// ==========================================

#define POT_PIN 34 // Pin where the middle leg of your potentiometer connects

Adafruit_MPU6050 mpu;

// This struct MUST match the receiver exactly
typedef struct struct_message {
    int node;
    float disp;
    float tilt;
    char uid[20]; // Empty for surface nodes, used by the Hub later
} struct_message;

struct_message telemetryData;
esp_now_peer_info_t peerInfo;

// Callback when data is sent
void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  Serial.print("Last Packet Send Status: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Delivery Success" : "Delivery Fail");
}

void setup() {
  Serial.begin(115200);
  
  // Set ESP32 as a Wi-Fi Station (needed for ESP-NOW)
  WiFi.mode(WIFI_STA);

  // Init ESP-NOW
  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }
  
  esp_now_register_send_cb(OnDataSent);
  
  // Register the Hub as a peer
  memcpy(peerInfo.peer_addr, hubAddress, 6);
  peerInfo.channel = 0;  
  peerInfo.encrypt = false;
  
  if (esp_now_add_peer(&peerInfo) != ESP_OK){
    Serial.println("Failed to add peer");
    return;
  }

  // Init MPU-6050
  if (!mpu.begin()) {
    Serial.println("Failed to find MPU6050 chip. Check wiring!");
    while (1) { delay(10); }
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  
  telemetryData.node = NODE_ID;
  strcpy(telemetryData.uid, ""); // No RFID on surface nodes
  
  Serial.println("Surface Node Initialized!");
}

void loop() {
  // 1. Read Potentiometer (Displacement)
  // analogRead returns 0-4095. We map this to 0.0mm - 100.0mm
  int potValue = analogRead(POT_PIN);
  telemetryData.disp = (potValue / 4095.0) * 100.0;

  // 2. Read MPU-6050 (Tilt)
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);
  
  // Simple tilt calculation based on X and Z axis gravity
  float tilt_x = atan2(a.acceleration.x, a.acceleration.z) * 180.0 / PI;
  telemetryData.tilt = tilt_x;

  // 3. Send over ESP-NOW
  esp_err_t result = esp_now_send(hubAddress, (uint8_t *) &telemetryData, sizeof(telemetryData));
   
  if (result == ESP_OK) {
    Serial.printf("Sent - Node: %d | Disp: %.1f mm | Tilt: %.1f deg\n", telemetryData.node, telemetryData.disp, telemetryData.tilt);
  } else {
    Serial.println("Error sending the data");
  }

  // Send data every 500 milliseconds (Matches your dashboard expectations)
  delay(500); 
}