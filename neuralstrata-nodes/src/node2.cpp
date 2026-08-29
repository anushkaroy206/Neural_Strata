#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <Wire.h> 

// ==========================================
// NODE 2 CONFIGURATION
#define NODE_ID 2 

// YOUR HUB'S MAC ADDRESS 
uint8_t hubAddress[] = {0x00, 0x4B, 0x12, 0x33, 0x78, 0x38}; 
// ==========================================

#define POT_PIN 34 
const int MPU_ADDR = 0x68; 

typedef struct struct_message {
    int node;
    float disp;
    float tilt;
    char uid[20]; 
} struct_message;

struct_message telemetryData;
esp_now_peer_info_t peerInfo;

void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  Serial.print("Last Packet Send Status: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "Delivery Success" : "Delivery Fail");
}

void setup() {
  Serial.begin(115200);
  
  WiFi.mode(WIFI_STA);

  if (esp_now_init() != ESP_OK) {
    Serial.println("Error initializing ESP-NOW");
    return;
  }
  
  esp_now_register_send_cb(OnDataSent);
  
  memcpy(peerInfo.peer_addr, hubAddress, 6);
  peerInfo.channel = 0;  
  peerInfo.encrypt = false;
  
  if (esp_now_add_peer(&peerInfo) != ESP_OK){
    Serial.println("Failed to add peer");
    return;
  }

  // --- RAW MPU-6050 WAKE UP ---
  Wire.begin();
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);  // PWR_MGMT_1 register
  Wire.write(0);     // Set to 0 to wake up the MPU-6050
  Wire.endTransmission(true);
  
  telemetryData.node = NODE_ID;
  strcpy(telemetryData.uid, ""); 
  
  Serial.println("Node 2 Initialized! (Raw I2C Mode)");
}

void loop() {
  // 1. Read Potentiometer
  int potValue = analogRead(POT_PIN);
  telemetryData.disp = (potValue / 4095.0) * 100.0;

  // 2. Read MPU-6050 directly
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);  // Start reading at ACCEL_XOUT_H register
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 6, true);  
  
  // Combine High and Low bytes
  int16_t AcX = Wire.read()<<8 | Wire.read();  
  int16_t AcY = Wire.read()<<8 | Wire.read();  
  int16_t AcZ = Wire.read()<<8 | Wire.read();  
  
  // Calculate tilt
  float tilt_x = atan2(AcX, AcZ) * 180.0 / PI;
  telemetryData.tilt = tilt_x;

  // 3. Send over ESP-NOW
  esp_err_t result = esp_now_send(hubAddress, (uint8_t *) &telemetryData, sizeof(telemetryData));
   
  if (result == ESP_OK) {
    Serial.printf("Sent - Node: %d | Disp: %.1f mm | Tilt: %.1f deg\n", telemetryData.node, telemetryData.disp, telemetryData.tilt);
  } else {
    Serial.println("Error sending the data");
  }

  delay(500); 
}